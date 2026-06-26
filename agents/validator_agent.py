"""
agents/validator_agent.py

WHAT THIS FILE DOES:
Implements ValidatorAgent — the quality gate of the pipeline. It validates
the AnalyzerAgent's output before the WriterAgent consumes it, ensuring:
  1. The analysis contains key_points (not empty).
  2. Average confidence meets a minimum threshold.
  3. The output is structurally sound.

WHY THIS EXISTS:
The assignment explicitly asks for a Validator agent. More importantly,
having a dedicated validation step separates the concern of "is this data
good enough to use?" from "synthesize the data" (Analyzer) and "format the
answer" (Writer). This makes each agent's responsibility
single-purpose and testable in isolation.

HOW IT INTERACTS WITH OTHER COMPONENTS:
- The PlannerAgent inserts a "validator" step between the analyzer and
  writer steps (validator depends on analyzer; writer depends on validator).
- The Orchestrator dispatches to ValidatorAgent like any other agent.
- ValidatorAgent reads from TaskContext.history to find the analysis result.
- If validation fails, it raises a StepValidationError (PermanentError
  subtype), which the orchestrator handles the same way as other permanent
  failures — the Writer then produces a degraded answer.
"""

from agents.base_agent import BaseAgent
from core.exceptions import StepValidationError
from core.models import Step, StepStatus, TaskContext
from utils.logger import get_logger

log = get_logger("ValidatorAgent")


class ValidatorAgent(BaseAgent):
    MIN_CONFIDENCE_THRESHOLD = 0.3

    def __init__(self):
        super().__init__(name="ValidatorAgent")

    async def execute(self, step: Step, context: TaskContext) -> dict:
        analysis_result = context.get_result(step.depends_on)
        if analysis_result is None or analysis_result.status != StepStatus.SUCCESS:
            raise StepValidationError(
                self.name,
                "Cannot validate: upstream analysis step did not succeed.",
            )

        analysis = analysis_result.output
        issues: list[str] = []

        if not analysis.get("key_points"):
            issues.append("No key points were produced in the analysis.")

        if analysis.get("documents_used", 0) == 0:
            issues.append("No documents were used in the analysis.")

        avg_conf = analysis.get("average_confidence", 0.0)
        if avg_conf < self.MIN_CONFIDENCE_THRESHOLD:
            issues.append(
                f"Average confidence ({avg_conf:.2f}) is below the "
                f"minimum threshold ({self.MIN_CONFIDENCE_THRESHOLD})."
            )

        validation_passed = len(issues) == 0

        if not validation_passed:
            log.warning(f"Validation failed with {len(issues)} issue(s): {'; '.join(issues)}")
            raise StepValidationError(
                self.name,
                f"Validation failed: {'; '.join(issues)}",
            )

        log.info("Validation passed — analysis is fit for the WriterAgent.")
        # Pass through the full analysis data so the Writer can consume it
        # directly from its depends_on (validate_1) without needing to also
        # look up analyze_1 separately. This preserves the single-dependency
        # chain: analyze_1 -> validate_1 -> write_1.
        return {
            "validation_status": "passed",
            "documents_validated": analysis.get("documents_used", 0),
            "confidence_at_validation": avg_conf,
            # --- pass-through of analysis data for Writer ---
            "key_points": analysis.get("key_points", []),
            "average_confidence": analysis.get("average_confidence", 0.0),
            "documents_used": analysis.get("documents_used", 0),
            "documents_missing": analysis.get("documents_missing", 0),
            "low_confidence_count": analysis.get("low_confidence_count", 0),
        }
