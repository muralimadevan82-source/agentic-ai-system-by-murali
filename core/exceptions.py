"""
core/exceptions.py

WHAT THIS FILE DOES:
Defines a small hierarchy of custom exceptions used across the whole system.

WHY IT EXISTS:
If every failure is just a generic `Exception`, the orchestrator has no way
to decide "should I retry this?" vs "should I skip this step?" vs
"should I abort the whole pipeline?". Typed exceptions let us encode that
decision directly in the except clause instead of string-matching error
messages (which is fragile and hard to explain in a review).

HOW IT INTERACTS WITH OTHER COMPONENTS:
- Agents raise these exceptions when something goes wrong inside `run()`.
- The orchestrator (core/orchestrator.py) catches them and applies different
  recovery strategies depending on the exception type.
"""


"""
core/exceptions.py

WHAT THIS FILE DOES:
Defines the typed exception hierarchy that the orchestrator uses to make
retry/recovery decisions. The key split is TransientError (retryable) vs
PermanentError (not retryable), decided by exception TYPE, not string
matching on error messages.

WHY THIS EXISTS:
A typed exception hierarchy is more reliable and explainable than
inspecting error message strings. The orchestrator catches AgentError and
checks the type — TransientError triggers backoff+retry, PermanentError
fails immediately. This is the foundation of the system's failure handling.
"""


class AgentError(Exception):
    """
    Base class for all agent-related errors.
    Catching this in the orchestrator is a catch-all for "something inside
    an agent went wrong" without swallowing unrelated bugs (e.g. a typo in
    our own orchestrator code, which should NOT be silently retried).
    """
    def __init__(self, agent_name: str, message: str):
        self.agent_name = agent_name
        self.message = message
        super().__init__(f"[{agent_name}] {message}")


class TransientError(AgentError):
    """
    Raised when a failure is *probably temporary* — e.g. a simulated network
    timeout, a rate limit, a flaky external API call.

    DESIGN DECISION: Only TransientError triggers automatic retry with
    backoff in the orchestrator. This mirrors real-world systems (network
    blips, rate limits) where retrying blindly on ALL errors would waste
    time retrying things that will never succeed (like bad input).
    """
    pass


class PermanentError(AgentError):
    """
    Raised when a failure will NOT be fixed by retrying — e.g. invalid input,
    a malformed step from the Planner, missing required data.

    DESIGN DECISION: Permanent errors should fail fast. Retrying them just
    burns time and (in a real system) money on wasted API calls.
    """
    pass


class StepValidationError(PermanentError):
    """
    Raised specifically when a step's output fails a sanity check before
    being passed to the next agent in the pipeline (e.g. Analyzer received
    an empty dataset from Retriever). Kept as its own class so the
    orchestrator can log a more specific failure reason for debugging.
    """
    pass


class PipelineAbortError(Exception):
    """
    Not an AgentError — this is raised by the ORCHESTRATOR ITSELF (not an
    agent) when a critical step fails even after recovery attempts and the
    rest of the pipeline cannot reasonably continue (e.g. Planner fails
    entirely, so there are no steps to execute).
    """
    pass
