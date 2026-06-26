"""
agents/writer_agent.py

WHAT THIS FILE DOES:
Takes the AnalyzerAgent's synthesized output and composes the final,
human-readable answer that gets shown to the user.

WHY IT EXISTS:
This is the last stage on purpose: nothing about formatting/wording the
final answer should leak into Planner, Retriever, or Analyzer. If you
wanted to change tone, add a disclaimer, or switch to a real LLM for
prose generation, this is the ONLY file you'd touch.

HOW IT INTERACTS WITH OTHER COMPONENTS:
- Reads the AnalyzerAgent's StepResult from `context`.
- Its return value is the final output shown by main.py — nothing
  consumes WriterAgent's output downstream (it's the end of the pipeline).
"""

"""
agents/writer_agent.py

WHAT THIS FILE DOES:
Implements WriterAgent — the final stage of the pipeline. It reads the
validated analysis (or detects failure) and composes a human-readable
answer string.

WHY THIS EXISTS:
The Writer is the last mile of the pipeline — it transforms structured
data into a user-friendly response. It's designed to NEVER crash:
if the analysis or validation steps failed, it produces an honest,
clearly-labeled degraded answer instead of raising an exception.

HOW IT INTERACTS WITH OTHER COMPONENTS:
- Depends on the validator step (via step.depends_on).
- Reads analysis/validation results from TaskContext.history.
- Outputs a string that becomes the pipeline's final_output.
- This is the only agent that runs after the point-of-no-return — there
  is no downstream step to recover from a Writer failure.
"""

from agents.base_agent import BaseAgent
from core.models import Step, StepStatus, TaskContext
from utils.logger import get_logger

log = get_logger("WriterAgent")


class WriterAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="WriterAgent")

    async def execute(self, step: Step, context: TaskContext) -> str:
        analysis_result = context.get_result(step.depends_on)

        # FAILURE SCENARIO #3 (DEGRADED OUTPUT, NOT A HARD CRASH):
        # If the Analyzer step never produced output (e.g. it failed and
        # the orchestrator chose to continue the pipeline rather than
        # abort it), the Writer should NOT throw an unhandled exception —
        # it should produce an honest, clearly-labeled partial answer.
        # This is the "meaningful failure handling and recovery"
        # requirement applied at the very last mile: the user still gets
        # SOMETHING useful back instead of a stack trace.
        if analysis_result is None or analysis_result.status != StepStatus.SUCCESS:
            log.error("Analysis step did not succeed — producing degraded answer.")
            return (
                "⚠️ I was unable to fully complete this request because the "
                "analysis step failed. Here is what I can tell you:\n"
                f"- Original request: {context.original_request}\n"
                "- No verified information could be synthesized at this time. "
                "Please try again, or narrow the request."
            )

        analysis = analysis_result.output
        log.info("Composing final answer from analysis output.")

        lines = [
            f"Here is what I found for: \"{context.original_request}\"\n",
        ]

        for point in analysis["key_points"]:
            lines.append(f"  • {point}")

        lines.append("")
        lines.append(
            f"(Confidence: {analysis['average_confidence']*100:.0f}% avg "
            f"across {analysis['documents_used']} source(s)"
            + (
                f", {analysis['documents_missing']} source(s) unavailable"
                if analysis["documents_missing"]
                else ""
            )
            + ")"
        )

        if analysis["low_confidence_count"] > 0:
            lines.append(
                f"Note: {analysis['low_confidence_count']} finding(s) had "
                f"lower confidence and may need manual verification."
            )

        return "\n".join(lines)
