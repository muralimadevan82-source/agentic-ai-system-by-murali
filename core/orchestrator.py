"""
core/orchestrator.py

WHAT THIS FILE DOES:
This is the engine of the whole system. Given a user request, it:
  1. Calls PlannerAgent to get an ordered list of Steps.
  2. Groups retrieval Steps together and runs them through manual
     batching (utils/batching.py) instead of one at a time.
  3. Runs the Analyzer step once all its dependencies are done.
  4. Runs the Writer step last.
  5. Wraps every agent call in retry logic that distinguishes transient
     vs permanent failures.
  6. Yields a StreamEvent after every meaningful thing that happens, so
     the caller can show live progress instead of waiting for the whole
     pipeline to finish.

WHY IT EXISTS:
Every other file in this project is a "worker". Workers should not need
to know about each other, about retry policy, or about how progress gets
displayed. The Orchestrator is the one place that DOES know about all of
that — this is the classic "thin controller, dumb workers" split, done in
plain asyncio with no external orchestration framework.

HOW IT INTERACTS WITH OTHER COMPONENTS:
- Holds one instance of each agent (Planner, Retriever, Analyzer, Writer)
  in a dict keyed by name, and dispatches Steps to them polymorphically
  via BaseAgent.execute().
- Is called from main.py via `async for event in orchestrator.run(request)`.
"""

"""
core/orchestrator.py

WHAT THIS FILE DOES:
The central orchestration engine of the agentic AI system. It implements
the pipeline as an async generator (for streaming), coordinates all agents,
handles retries with backoff, and manages manual batching for retrieval steps.

WHY THIS EXISTS:
This is where the assignment's core requirements converge:
  - Pipeline orchestration (Planner -> Retriever -> Analyzer -> Validator -> Writer)
  - Streaming via async generator (yield StreamEvent after each action)
  - Failure handling with typed exceptions (TransientError / PermanentError)
  - Manual batching for retrieval steps (utils/batching.run_batched)

HOW IT INTERACTS WITH OTHER COMPONENTS:
- main.py calls orchestrator.run() via async for ... yield to get streamed events.
- All agents are registered in the self.agents dict and dispatched by string key.
- TaskContext is the shared notebook passed through every step.
- Uses utils/batching.run_batched for concurrent-but-bounded retrieval.
"""

import asyncio
import time

from agents.analyzer_agent import AnalyzerAgent
from agents.base_agent import BaseAgent
from agents.planner_agent import PlannerAgent
from agents.retriever_agent import RetrieverAgent
from agents.validator_agent import ValidatorAgent
from agents.writer_agent import WriterAgent
from core.exceptions import AgentError, PermanentError, PipelineAbortError, TransientError
from core.models import Step, StepResult, StepStatus, TaskContext
from utils.batching import run_batched
from utils.logger import get_logger
from utils.streaming import EventType, StreamEvent

log = get_logger("Orchestrator")


class Orchestrator:
    def __init__(
        self,
        *,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        retrieval_batch_size: int = 3,
        retrieval_max_concurrency: int = 3,
    ):
        # DESIGN DECISION: agents are instantiated ONCE here and reused
        # across the whole request, rather than created fresh per step.
        # This mirrors how you'd manage real resources (HTTP sessions,
        # DB connection pools) in a production agent — you don't want to
        # spin up a new client object for every single step.
        self.agents: dict[str, BaseAgent] = {
            "planner": PlannerAgent(),
            "retriever": RetrieverAgent(),
            "analyzer": AnalyzerAgent(),
            "validator": ValidatorAgent(),
            "writer": WriterAgent(),
        }
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.retrieval_batch_size = retrieval_batch_size
        self.retrieval_max_concurrency = retrieval_max_concurrency

    # ------------------------------------------------------------------
    # PUBLIC ENTRY POINT
    # ------------------------------------------------------------------
    async def run(self, request_id: str, user_request: str):
        """
        The main async generator driving one end-to-end pipeline run.

        WHY AN ASYNC GENERATOR (not a regular async function returning a
        final string): because the assignment requires STREAMING partial
        output. `yield`-ing a StreamEvent after each step lets the caller
        print progress live, the moment it happens, instead of getting
        one big blob at the very end.
        """
        context = TaskContext(request_id=request_id, original_request=user_request)

        # ---- PHASE 1: PLANNING -----------------------------------------
        try:
            plan = await self._run_planner(user_request, context)
        except PermanentError as e:
            # If we can't even build a plan, there is nothing left to do.
            # This is the one case where we ABORT the whole pipeline
            # rather than degrade gracefully — you cannot "partially"
            # have a plan.
            yield StreamEvent(
                EventType.PIPELINE_ABORTED,
                f"Pipeline aborted: planning failed permanently ({e.message})",
            )
            return

        context.plan = plan
        yield StreamEvent(
            EventType.PLAN_READY,
            f"Plan ready with {len(plan)} step(s): {[s.step_id for s in plan]}",
            payload={"plan": [s.step_id for s in plan]},
        )

        # ---- PHASE 2: RETRIEVAL (BATCHED) -------------------------------
        retrieval_steps = [s for s in plan if s.agent == "retriever"]
        async for event in self._run_retrieval_batch(retrieval_steps, context):
            yield event

        # ---- PHASE 3, 4 & 5: ANALYZER -> VALIDATOR -> WRITER (sequential,
        # each depends on the previous stage's full output) -------------
        sequential_steps = [s for s in plan if s.agent in ("analyzer", "validator", "writer")]
        for step in sequential_steps:
            async for event in self._run_single_step(step, context):
                yield event

        yield StreamEvent(
            EventType.PIPELINE_DONE,
            "Pipeline complete.",
            payload={"final_output": context.history[-1].output if context.history else None},
        )

    # ------------------------------------------------------------------
    # PHASE HELPERS
    # ------------------------------------------------------------------
    async def _run_planner(self, user_request: str, context: TaskContext) -> list[Step]:
        """Planner is invoked directly (not through the generic retry
        wrapper) because a planning failure is ALWAYS permanent in this
        system — there's no transient version of "I don't understand the
        request" — so wrapping it in retry logic would just waste time."""
        kickoff_step = Step(
            step_id="plan_0",
            description="Decompose user request",
            agent="planner",
            input_query=user_request,
        )
        return await self.agents["planner"].execute(kickoff_step, context)

    async def _run_retrieval_batch(self, steps: list[Step], context: TaskContext):
        """
        Runs all retrieval steps using MANUAL BATCHING
        (utils/batching.run_batched), instead of a single
        asyncio.gather(*all_of_them) call.

        WHY batching here specifically: retrieval is the stage most
        analogous to real-world I/O-bound work (API calls, DB queries)
        where firing unlimited concurrent requests is unrealistic and
        often against provider rate limits. This is also the most
        natural place in the whole pipeline to demonstrate the assignment's
        "manual batching logic" requirement, since Analyzer/Writer are
        inherently single, sequential steps with nothing to batch.
        """
        if not steps:
            return

        yield StreamEvent(
            EventType.STEP_STARTED,
            f"Starting batched retrieval for {len(steps)} sub-quer{'y' if len(steps)==1 else 'ies'}...",
        )

        # `worker` closes over `context` so each retrieval job can write
        # its own StepResult into shared history once it finishes —
        # run_batched() only cares about running the coroutine, not about
        # what it does internally.
        async def worker(step: Step) -> StreamEvent:
            events = []
            async for event in self._run_single_step(step, context, emit_started=False):
                events.append(event)
            return events

        # NOTE: run_batched expects one return value per item — we collect
        # lists-of-events per step here and flatten them below so progress
        # for EVERY step (including retries) still gets surfaced to the
        # caller, even though steps inside a batch ran concurrently.
        nested_events = await run_batched(
            items=steps,
            worker=worker,
            batch_size=self.retrieval_batch_size,
            max_concurrency_per_batch=self.retrieval_max_concurrency,
        )
        for event_list in nested_events:
            for event in event_list:
                yield event

    async def _run_single_step(self, step: Step, context: TaskContext, emit_started: bool = True):
        """
        Runs ONE step end-to-end: emits STEP_STARTED, calls the right
        agent with retry handling, records a StepResult into
        context.history, and emits STEP_SUCCEEDED / STEP_FAILED /
        STEP_SKIPPED accordingly.

        This is the single choke point where every step in the system —
        whether sequential (Analyzer, Writer) or part of a concurrent
        batch (Retriever) — gets the SAME failure-handling treatment.
        Keeping this logic in exactly one place is what makes the system
        easy to reason about and modify.
        """
        if emit_started:
            yield StreamEvent(EventType.STEP_STARTED, f"[{step.step_id}] {step.description}")

        agent = self.agents[step.agent]
        start_time = time.monotonic()
        attempts = 0
        last_error: str | None = None

        while attempts <= self.max_retries:
            attempts += 1
            try:
                output = await agent.execute(step, context)
                duration = time.monotonic() - start_time
                result = StepResult(
                    step_id=step.step_id,
                    agent=step.agent,
                    status=StepStatus.SUCCESS,
                    output=output,
                    attempts=attempts,
                    duration_seconds=round(duration, 3),
                )
                context.history.append(result)
                yield StreamEvent(
                    EventType.STEP_SUCCEEDED,
                    f"[{step.step_id}] done in {duration:.2f}s "
                    f"(attempt {attempts}/{self.max_retries + 1})",
                )
                return

            except TransientError as e:
                last_error = e.message
                if attempts <= self.max_retries:
                    backoff = self.retry_backoff_seconds * attempts  # simple linear backoff
                    yield StreamEvent(
                        EventType.STEP_RETRY,
                        f"[{step.step_id}] transient failure ({e.message}) — "
                        f"retrying in {backoff:.1f}s "
                        f"(attempt {attempts}/{self.max_retries + 1})",
                    )
                    await asyncio.sleep(backoff)
                    continue
                # Retries exhausted — fall through to FAILED handling below.
                break

            except PermanentError as e:
                # No point retrying — record as failed immediately.
                last_error = e.message
                break

            except Exception as e:  # pragma: no cover - true unexpected bug
                # Anything that is NOT one of our typed AgentErrors is
                # treated as a permanent, unexpected failure. We do not
                # retry unknown errors blindly — that could mask real bugs
                # in our own orchestration code.
                last_error = f"Unexpected error: {e}"
                break

        # ---- All attempts exhausted or a permanent error occurred -------
        duration = time.monotonic() - start_time
        result = StepResult(
            step_id=step.step_id,
            agent=step.agent,
            status=StepStatus.FAILED,
            error=last_error,
            attempts=attempts,
            duration_seconds=round(duration, 3),
        )
        context.history.append(result)
        yield StreamEvent(
            EventType.STEP_FAILED,
            f"[{step.step_id}] failed after {attempts} attempt(s): {last_error}",
        )

        # ---- RECOVERY DECISION ------------------------------------------
        # A failed RETRIEVER step does not abort the pipeline: Analyzer is
        # built to tolerate some missing sources (see analyzer_agent.py).
        # A failed ANALYZER, VALIDATOR, or WRITER step, however, means
        # there is nothing more useful left to compute downstream of it
        # — those are sequential/terminal stages. Writer already degrades
        # gracefully if Analyzer or Validator failed (see writer_agent.py).
        # We deliberately do NOT raise PipelineAbortError here, because
        # even a failed analysis should still let WriterAgent produce a
        # degraded-but-honest message instead of crashing the whole CLI
        # with a traceback.
        if step.agent == "retriever":
            log.warning(f"Retrieval step {step.step_id} failed — continuing pipeline with partial data.")
