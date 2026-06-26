"""
utils/streaming.py

WHAT THIS FILE DOES:
Defines `StreamEvent`, a small typed structure for "something happened in
the pipeline right now", and a helper to print a stream of these events
to the console AS THEY ARRIVE (not buffered until the end).

WHY IT EXISTS:
The assignment requires partial output streaming. With a synchronous
script, you'd only see output once the whole pipeline finished. Here, the
orchestrator is an ASYNC GENERATOR (`async def run(...) -> AsyncIterator`)
that `yield`s a StreamEvent the moment each step finishes — so the caller
(main.py) can print progress in real time, the same way a chat UI shows
tokens as they're generated rather than waiting for the full response.

HOW IT INTERACTS WITH OTHER COMPONENTS:
- core/orchestrator.py yields StreamEvent objects as it works through the
  plan.
- main.py iterates over the orchestrator's async generator with
  `async for event in orchestrator.run(...)` and prints each one
  immediately via `print_event`.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class EventType(Enum):
    PLAN_READY = "plan_ready"
    STEP_STARTED = "step_started"
    STEP_RETRY = "step_retry"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"
    STEP_SKIPPED = "step_skipped"
    PIPELINE_DONE = "pipeline_done"
    PIPELINE_ABORTED = "pipeline_aborted"


@dataclass
class StreamEvent:
    """One unit of streamed progress. `payload` is intentionally a loosely
    typed dict — different event types carry different useful info
    (e.g. STEP_RETRY carries attempt count, PLAN_READY carries the steps),
    and forcing one rigid schema across all event types would add
    complexity for no real benefit at this scale."""
    type: EventType
    message: str
    payload: Optional[dict[str, Any]] = None


def print_event(event: StreamEvent) -> None:
    """
    Renders a StreamEvent to the console immediately.

    DESIGN DECISION: This formatting logic lives in utils/streaming.py
    (not main.py) so that any future caller (e.g. a web socket handler,
    a test harness) can reuse the same "what does this event look like"
    logic without duplicating it. main.py just decides WHEN to call this.
    """
    icons = {
        EventType.PLAN_READY: "🗺️ ",
        EventType.STEP_STARTED: "▶️ ",
        EventType.STEP_RETRY: "🔁",
        EventType.STEP_SUCCEEDED: "✅",
        EventType.STEP_FAILED: "❌",
        EventType.STEP_SKIPPED: "⏭️ ",
        EventType.PIPELINE_DONE: "🏁",
        EventType.PIPELINE_ABORTED: "🛑",
    }
    icon = icons.get(event.type, "•")
    print(f"{icon} {event.message}", flush=True)
