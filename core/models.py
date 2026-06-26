"""
core/models.py

WHAT THIS FILE DOES:
Defines the plain data structures (using Python's built-in `dataclasses`)
that flow between agents: Step, StepResult, and TaskContext.

WHY IT EXISTS:
Without a shared "shape" for data, every agent would invent its own dict
keys ("text" vs "content" vs "data"...) and the orchestrator would be full
of defensive .get() calls. A dataclass is the simplest possible contract:
it's just a typed container, with no hidden behavior. This is intentionally
NOT a framework-style "Message" or "AgentState" abstraction — it's a plain
struct, which keeps it easy to read, modify, and explain.

HOW IT INTERACTS WITH OTHER COMPONENTS:
- PlannerAgent produces a list[Step].
- The Orchestrator wraps the running task in a TaskContext and passes it to
  every agent.
- Every agent returns a StepResult, which the Orchestrator stores in
  TaskContext.history before moving to the next step.
"""

"""
core/models.py

WHAT THIS FILE DOES:
Defines the core data contracts used throughout the pipeline — Step,
StepResult, StepStatus, and TaskContext. These are plain dataclasses and
enums with zero business logic, kept in their own file so they can be
imported by every other module without circular dependencies.

WHY THIS EXISTS:
Separating data models from orchestration logic keeps each file small
and testable. core/models.py and core/exceptions.py are the only two files
that every other module imports — keeping them lean prevents the "import
everything" anti-pattern.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class StepStatus(Enum):
    """Lifecycle states for a single pipeline step. Using an Enum (rather
    than raw strings like "done"/"failed") prevents typos like "complete"
    vs "completed" from silently breaking status checks."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Step:
    """
    A single unit of work produced by the PlannerAgent.

    Fields:
        step_id:      Stable identifier (e.g. "step_1") used in logs and history.
        description:  Human-readable description of what this step does.
        agent:        Which agent should execute this step ("retriever",
                       "analyzer", "writer"). This is how the orchestrator
                       knows which worker to dispatch to, without any
                       complex routing logic.
        input_query:  The specific sub-query/input this step needs
                       (derived from the original user request).
        depends_on:   step_id of a prior step this one needs output from
                       (None if it doesn't depend on anything, e.g. the
                       first retrieval step).
    """
    step_id: str
    description: str
    agent: str
    input_query: str
    depends_on: Optional[str] = None


@dataclass
class StepResult:
    """
    The output of running one Step through its assigned agent.

    DESIGN DECISION: We store `status`, `error`, and `attempts` directly
    on the result (rather than raising and losing this info) because the
    WriterAgent and the final report need to know which steps degraded
    or were retried — that's part of an honest final answer, not just
    internal logging noise.
    """
    step_id: str
    agent: str
    status: StepStatus
    output: Any = None
    error: Optional[str] = None
    attempts: int = 1
    duration_seconds: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class TaskContext:
    """
    The "shared notebook" passed through the whole pipeline for a single
    user request. Carries the original request, the plan, and a running
    history of step results so later agents (e.g. Writer) can see
    everything that happened before them.

    WHY a single mutable context object instead of passing individual
    arguments to every agent? Because steps have dependencies on each
    other's outputs (depends_on), and the number of upstream steps varies
    per request. A shared context avoids an ever-growing, fragile function
    signature like `run(step, retriever_output, analyzer_output, ...)`.
    """
    request_id: str
    original_request: str
    plan: list[Step] = field(default_factory=list)
    history: list[StepResult] = field(default_factory=list)

    def get_result(self, step_id: str) -> Optional[StepResult]:
        """Look up a prior step's result by id — used when a step
        declares `depends_on`."""
        for result in self.history:
            if result.step_id == step_id:
                return result
        return None

    def successful_outputs(self) -> dict[str, Any]:
        """Convenience accessor: step_id -> output, for all steps that
        succeeded. Used heavily by WriterAgent to assemble the final
        answer from whatever data survived the pipeline."""
        return {
            r.step_id: r.output
            for r in self.history
            if r.status == StepStatus.SUCCESS
        }
