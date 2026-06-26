"""
agents/analyzer_agent.py

WHAT THIS FILE DOES:
Takes ALL the retrieved documents (from however many retrieval steps the
Planner created) and produces a single synthesized analysis: key points
extracted, an average confidence score, and any low-confidence or missing
results flagged.

WHY IT EXISTS:
Separating "fetch the data" (Retriever) from "make sense of the data"
(Analyzer) means each agent has one job, is independently testable, and
independently swappable (e.g. you could later make AnalyzerAgent call a
real LLM to summarize, without touching RetrieverAgent at all).

HOW IT INTERACTS WITH OTHER COMPONENTS:
- Reads multiple upstream StepResults from `context` (one per retrieval
  sub-query), NOT just one — this is why Step.depends_on can encode
  multiple step_ids (see planner_agent.py and orchestrator.py).
- Its output feeds into WriterAgent.
"""

"""
agents/analyzer_agent.py

WHAT THIS FILE DOES:
Implements AnalyzerAgent — the synthesis stage of the pipeline. It reads
all retrieval results from shared TaskContext.history, aggregates key
points, computes confidence metrics, and tolerates partial upstream failures.

WHY THIS EXISTS:
Raw retrieval results need to be synthesized into a structured analysis
before the Writer can compose a coherent answer. The Analyzer sits between
the data-fetching and writing stages, transforming "many individual
documents" into "one structured analysis."

HOW IT INTERACTS WITH OTHER COMPONENTS:
- Depends on ALL retrieval steps via step.depends_on (multi-dependency
  pipe-delimited string).
- Reads results from TaskContext.history using context.get_result().
- Outputs a dict consumed by ValidatorAgent (for quality checks) and
  WriterAgent (for final formatting).
- Raises StepValidationError if ALL upstream retrievals failed.
"""

from agents.base_agent import BaseAgent
from core.exceptions import StepValidationError
from core.models import Step, StepStatus, TaskContext
from utils.logger import get_logger

log = get_logger("AnalyzerAgent")


class AnalyzerAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="AnalyzerAgent")

    async def execute(self, step: Step, context: TaskContext) -> dict:
        upstream_ids = (step.depends_on or "").split("|")
        upstream_ids = [s for s in upstream_ids if s]

        retrieved_docs = []
        failed_queries = []

        for step_id in upstream_ids:
            result = context.get_result(step_id)
            if result is None:
                # Defensive guard — should not happen if the orchestrator
                # is wired correctly, but fails loudly instead of silently
                # skipping if it ever does.
                raise StepValidationError(
                    self.name, f"Expected upstream result '{step_id}' not found in context."
                )
            if result.status == StepStatus.SUCCESS:
                retrieved_docs.append(result.output)
            else:
                # A retrieval step failed even after retries and was
                # marked FAILED/SKIPPED by the orchestrator. We don't
                # crash the whole analysis over ONE missing source — we
                # note the gap and continue with what we have. This is a
                # deliberate partial-degradation strategy (see README's
                # design rationale).
                failed_queries.append(step_id)

        # FAILURE SCENARIO #2 (PERMANENT / VALIDATION): if EVERY upstream
        # retrieval failed, there is nothing left to analyze. Continuing
        # would produce a meaningless "analysis" of zero documents, so we
        # fail loudly and let the orchestrator decide how to handle a
        # step that has no usable input — this is NOT something a retry
        # would fix (retrying analysis with the same empty input changes
        # nothing), so it's raised as a StepValidationError (a
        # PermanentError subtype), not a TransientError.
        if not retrieved_docs:
            raise StepValidationError(
                self.name,
                "All upstream retrieval steps failed — nothing to analyze.",
            )

        log.info(
            f"Analyzing {len(retrieved_docs)} retrieved document(s) "
            f"({len(failed_queries)} upstream failure(s) skipped)"
        )

        avg_confidence = round(
            sum(d["confidence"] for d in retrieved_docs) / len(retrieved_docs), 2
        )
        low_confidence_items = [d for d in retrieved_docs if d["confidence"] < 0.7]

        key_points = [
            f"On '{d['query']}': {d['snippet']}" for d in retrieved_docs
        ]

        return {
            "key_points": key_points,
            "average_confidence": avg_confidence,
            "low_confidence_count": len(low_confidence_items),
            "documents_used": len(retrieved_docs),
            "documents_missing": len(failed_queries),
        }
