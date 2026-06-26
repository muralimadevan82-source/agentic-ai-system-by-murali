"""
main.py

WHAT THIS FILE DOES:
The command-line entry point. Takes a user request (from a CLI arg, or an
interactive prompt if none is given), runs it through the Orchestrator,
and prints streamed progress events live as they arrive, followed by the
final answer.

WHY IT EXISTS:
Every project needs ONE obvious place to start reading and ONE obvious
place to run from. This file deliberately contains almost no logic of its
own — it just drives the orchestrator's async generator and prints what
comes out. All real behavior lives in core/ and agents/, which is what
makes those pieces independently testable.

HOW IT INTERACTS WITH OTHER COMPONENTS:
- Creates one Orchestrator instance.
- Calls `orchestrator.run(request_id, user_request)` and iterates over it
  with `async for`, printing each StreamEvent immediately via
  utils.streaming.print_event.
- Extracts and prints the final answer from the last StreamEvent's payload.
"""

import asyncio
import sys
import uuid

from core.orchestrator import Orchestrator
from utils.streaming import EventType, print_event


async def run_pipeline(user_request: str) -> None:
    orchestrator = Orchestrator(
        max_retries=2,
        retry_backoff_seconds=0.5,
        retrieval_batch_size=3,
        retrieval_max_concurrency=3,
    )

    request_id = str(uuid.uuid4())[:8]
    print(f"\n=== Running request [{request_id}] ===")
    print(f"Request: {user_request}\n")

    final_output = None
    async for event in orchestrator.run(request_id, user_request):
        print_event(event)
        if event.type == EventType.PIPELINE_DONE and event.payload:
            final_output = event.payload.get("final_output")

    print("\n" + "=" * 60)
    print("FINAL ANSWER")
    print("=" * 60)
    print(final_output if final_output else "(No output produced — pipeline aborted.)")
    print("=" * 60 + "\n")


def main() -> None:
    if len(sys.argv) > 1:
        user_request = " ".join(sys.argv[1:])
    else:
        user_request = input("Enter your multi-step request: ").strip()

    if not user_request:
        print("No request provided. Exiting.")
        return

    asyncio.run(run_pipeline(user_request))


if __name__ == "__main__":
    main()
