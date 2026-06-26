"""
agents/base_agent.py

WHAT THIS FILE DOES:
Defines `BaseAgent`, an abstract base class with one required method:
`async def execute(self, step, context) -> Any`.

WHY IT EXISTS:
We want every agent (Planner, Retriever, Analyzer, Writer) to be
swappable from the orchestrator's point of view — the orchestrator should
be able to say "call execute() on whichever agent this step needs" without
caring HOW each agent does its job internally. That's the entire point of
this class: a tiny, boring contract, not a framework.

DESIGN DECISION: We use Python's `abc` module (already in the standard
library) rather than just relying on "duck typing" + documentation. Using
ABC means a missing `execute()` method fails LOUDLY at class definition
time with a clear error, rather than failing confusingly deep inside the
orchestrator at runtime. This is a deliberately small use of abstraction —
one method, no metaclass magic beyond what `abc` already gives us.

HOW IT INTERACTS WITH OTHER COMPONENTS:
- Every concrete agent (PlannerAgent, RetrieverAgent, AnalyzerAgent,
  WriterAgent) inherits from this and implements `execute()`.
- core/orchestrator.py holds a dict of {agent_name: BaseAgent instance}
  and calls `.execute(step, context)` polymorphically — it never imports
  or references a concrete agent class directly.
"""

"""
agents/base_agent.py

WHAT THIS FILE DOES:
Defines the BaseAgent abstract base class that every agent in the system
must implement. Establishes the contract: every agent has a `name` and an
`async def execute(step, context)` method.

WHY THIS EXISTS:
A consistent interface for all agents lets the Orchestrator dispatch work
with a simple dict lookup (self.agents[step.agent]) instead of a chain
of if/elif statements. Adding a new agent means writing one class and
registering it in orchestrator.py — no other dispatch code changes.
"""

from abc import ABC, abstractmethod
from typing import Any

from core.models import Step, TaskContext


class BaseAgent(ABC):
    """All agents share a `name` (for logging) and must implement
    `execute()`. Nothing else is forced on subclasses — each agent is
    free to have its own internal helper methods, its own batching
    strategy, its own retry-worthy failure points."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def execute(self, step: Step, context: TaskContext) -> Any:
        """
        Run this agent's logic for a single Step.

        Must be a coroutine (async def) so the orchestrator can run
        agents concurrently where the plan allows it, and so a single
        slow agent call doesn't block the whole interpreter.

        Returns: whatever output this agent produces (a dict, list, or
        string depending on the agent) — the orchestrator does not
        inspect the structure, it just stores it on a StepResult and
        hands it forward to whichever later step depends on it.

        Raises: TransientError or PermanentError (see core/exceptions.py)
        on failure — never a bare Exception, so the orchestrator's
        recovery logic can make an informed decision.
        """
        raise NotImplementedError
