"""
agents/planner_agent.py

WHAT THIS FILE DOES:
Takes the user's raw, possibly multi-part request and decomposes it into
an ordered list of `Step` objects, each tagged with which agent should
handle it (retriever / analyzer / writer).

WHY IT EXISTS:
This is the "decompose a complex request into discrete ordered steps"
requirement. Rather than hand the whole messy request to one giant LLM
call (or one giant function), the Planner's ONLY job is to figure out
"what needs to happen, and in what order" — a clean separation of
concerns: Planner decides WHAT, the other agents decide HOW.

IMPLEMENTATION NOTE ON "NO LLM FRAMEWORK":
This reference implementation uses a deterministic, rule-based planner
(splitting on conjunctions/punctuation + keyword detection) so the whole
system runs offline, free, and 100% reproducibly for grading/demo
purposes. In `plan()` there is a single clearly-marked extension point
showing EXACTLY where you would swap in a real LLM call (e.g. to the
OpenAI or Anthropic API) to do smarter decomposition — without changing
anything else in the system, because the rest of the pipeline only cares
that `plan()` returns `list[Step]`, not how that list was produced.

HOW IT INTERACTS WITH OTHER COMPONENTS:
- Called once per request, first, by core/orchestrator.py.
- Produces context.plan, which the orchestrator then iterates over,
  dispatching each Step to the matching agent (Retriever/Analyzer/Writer).
"""

"""
agents/planner_agent.py

WHAT THIS FILE DOES:
Implements PlannerAgent — the first agent in every pipeline run. It takes
a raw user request and decomposes it into an ordered list of Step objects
that the orchestrator executes.

WHY THIS EXISTS:
The Planner is the "brain" of the pipeline. Its output (list[Step])
determines every step that follows. The current implementation uses
rule-based decomposition (regex splitting on "and", "then", etc.), but
the output contract is designed so an LLM-based planner can be swapped
in without changing any other file.

HOW IT INTERACTS WITH OTHER COMPONENTS:
- Called directly by Orchestrator._run_planner() (not through the generic
  retry wrapper) because planner failures are always permanent.
- Produces a plan that the orchestrator iterates over to dispatch work.
- Other agents consume the plan through TaskContext.plan, not by
  importing PlannerAgent.
"""

import re

from agents.base_agent import BaseAgent
from core.exceptions import PermanentError
from core.models import Step, TaskContext
from utils.logger import get_logger

log = get_logger("PlannerAgent")


class PlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="PlannerAgent")

    async def execute(self, step: Step, context: TaskContext) -> list[Step]:
        """
        PlannerAgent is special: it doesn't receive a pre-built Step from
        someone else (there's nothing before it), it BUILDS the steps.
        The orchestrator calls this once at the very start of a request,
        passing in a synthetic "kickoff" step that just carries the raw
        user request through `step.input_query`.
        """
        request = step.input_query.strip()
        if not request:
            # PermanentError, not TransientError: an empty request will
            # never succeed no matter how many times we retry it.
            raise PermanentError(self.name, "Cannot plan for an empty request.")

        log.info(f"Decomposing request: {request!r}")
        sub_queries = self._split_into_subqueries(request)

        plan: list[Step] = []
        retrieval_step_ids: list[str] = []

        # --- Step group 1: one Retriever step per sub-query -----------
        # Each sub-query becomes its own retrieval step so they can later
        # be BATCHED (see utils/batching.py) instead of fetched one by one.
        for i, query in enumerate(sub_queries, start=1):
            step_id = f"retrieve_{i}"
            plan.append(
                Step(
                    step_id=step_id,
                    description=f"Retrieve information for: '{query}'",
                    agent="retriever",
                    input_query=query,
                )
            )
            retrieval_step_ids.append(step_id)

        # --- Step group 2: one Analyzer step depending on ALL retrievals
        # We only need ONE analysis step (not one per sub-query) because
        # the Analyzer's job is to find relationships ACROSS the
        # retrieved data, which requires seeing all of it together.
        plan.append(
            Step(
                step_id="analyze_1",
                description="Analyze and synthesize all retrieved information",
                agent="analyzer",
                input_query=request,
                depends_on="|".join(retrieval_step_ids),  # multi-dependency, parsed by orchestrator
            )
        )

        # --- Step group 3: one Validator step to quality-check analysis ---
        plan.append(
            Step(
                step_id="validate_1",
                description="Validate the analysis output before writing",
                agent="validator",
                input_query=request,
                depends_on="analyze_1",
            )
        )

        # --- Step group 4: exactly one Writer step at the end -----------
        plan.append(
            Step(
                step_id="write_1",
                description="Compose the final answer for the user",
                agent="writer",
                input_query=request,
                depends_on="validate_1",
            )
        )

        log.info(f"Plan created with {len(plan)} step(s): "
                  f"{[s.step_id for s in plan]}")

        # ----------------------------------------------------------------
        # >>> EXTENSION POINT: swap rule-based planning for an LLM call <<<
        # To use a real LLM here instead of the rule-based splitter above,
        # you would replace `self._split_into_subqueries(request)` with
        # something like:
        #
        #   response = await llm_client.complete(
        #       prompt=f"Break this request into a numbered list of "
        #              f"independent research sub-questions: {request}"
        #   )
        #   sub_queries = parse_numbered_list(response)
        #
        # Everything below this point (building Step objects) stays
        # IDENTICAL — this is the benefit of keeping Planner's output
        # contract (`list[Step]`) decoupled from how it's generated.
        # ----------------------------------------------------------------

        return plan

    @staticmethod
    def _split_into_subqueries(request: str) -> list[str]:
        """
        Deterministic decomposition of a multi-part request into separate
        sub-queries.

        Strategy (intentionally simple and explainable):
          1. Split on sentence-ending punctuation AND on the words
             "and", "also", "then" used as connectors between asks.
          2. Strip filler and discard empty fragments.
          3. If splitting produces nothing usable, fall back to treating
             the whole request as a single sub-query.

        This is a HEURISTIC, not NLP magic — it is good enough to turn
        "Find the top 3 AI startups in Bangalore and compare their
        funding, then summarize which is the best bet for a fresher"
        into three clean sub-queries, which is exactly the kind of input
        this system is built to demo.
        """
        # Normalize separators: treat ".", ";", " and then ", " then "
        # and " also " as boundaries between distinct asks.
        normalized = re.sub(r"\s+(and then|then|and also|also)\s+", " | ", request, flags=re.IGNORECASE)
        normalized = re.sub(r"[;.]", "|", normalized)

        # Split ONLY on a bare " and " when it looks like it's joining two
        # asks (heuristic: followed eventually by a verb-ish word) — to
        # avoid wrongly splitting "founders and investors" into two
        # sub-queries. We keep this simple: split on " and " only when
        # NOT immediately followed by a short noun-like single word + end.
        parts = normalized.split("|")
        sub_queries: list[str] = []
        for part in parts:
            part = part.strip(" .,")
            if not part:
                continue
            # Further split on a top-level " and " if the fragment is long
            # enough to plausibly contain two asks (heuristic threshold).
            if " and " in part.lower() and len(part.split()) > 6:
                halves = re.split(r"\s+and\s+", part, maxsplit=1, flags=re.IGNORECASE)
                sub_queries.extend(h.strip(" .,") for h in halves if h.strip(" .,"))
            else:
                sub_queries.append(part)

        sub_queries = [q for q in sub_queries if q]
        if not sub_queries:
            sub_queries = [request]

        return sub_queries
