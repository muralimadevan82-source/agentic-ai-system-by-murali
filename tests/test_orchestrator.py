"""
tests/test_orchestrator.py

WHAT THIS FILE DOES:
Integration-style tests for core/orchestrator.py — runs the FULL pipeline
end-to-end under controlled conditions (forcing failure rates) to verify
all three failure scenarios behave as designed:
  1. Transient retrieval failure -> automatic retry -> eventual success.
  2. All retrievals fail -> Analyzer fails validation -> Writer degrades
     gracefully instead of crashing.
  3. Empty request -> Planner fails permanently -> pipeline aborts cleanly.

WHY IT EXISTS:
The orchestrator is the riskiest file to silently break (it's the most
complex one). These tests assert on FINAL OBSERVABLE BEHAVIOR (event
types emitted, final StepStatus values) rather than internal
implementation details, so the tests stay valid even if you refactor the
internals later.
"""

import pytest

from core.models import StepStatus
from core.orchestrator import Orchestrator
from utils.streaming import EventType


@pytest.mark.asyncio
async def test_happy_path_produces_final_output():
    orch = Orchestrator(max_retries=2)
    orch.agents["retriever"].simulated_failure_rate = 0.0  # deterministic success

    events = []
    async for event in orch.run("t1", "Find AI startups and summarize them"):
        events.append(event)

    assert events[-1].type == EventType.PIPELINE_DONE
    assert events[-1].payload["final_output"] is not None
    assert any(e.type == EventType.STEP_SUCCEEDED for e in events)


@pytest.mark.asyncio
async def test_transient_failure_triggers_retry_then_succeeds():
    orch = Orchestrator(max_retries=3, retry_backoff_seconds=0.01)
    orch.agents["retriever"].simulated_failure_rate = 1.0  # always fails on first try

    # Monkeypatch: succeed only from the 2nd attempt onward, to test the
    # RETRY path specifically rather than total failure.
    call_count = {"n": 0}
    original_fetch = orch.agents["retriever"]._fetch_raw

    async def flaky_then_ok(query):
        call_count["n"] += 1
        if call_count["n"] == 1:
            from core.exceptions import TransientError
            raise TransientError("RetrieverAgent", "forced failure for test")
        return await original_fetch.__wrapped__(query) if hasattr(original_fetch, "__wrapped__") else {
            "query": query, "snippet": "ok", "source": "test", "confidence": 0.9
        }

    orch.agents["retriever"]._fetch_raw = flaky_then_ok

    events = []
    async for event in orch.run("t2", "Find one thing"):
        events.append(event)

    assert any(e.type == EventType.STEP_RETRY for e in events)
    assert events[-1].type == EventType.PIPELINE_DONE


@pytest.mark.asyncio
async def test_all_retrieval_failure_degrades_gracefully_not_crash():
    orch = Orchestrator(max_retries=1, retry_backoff_seconds=0.01)
    orch.agents["retriever"].simulated_failure_rate = 1.0  # force permanent-by-exhaustion failure

    events = []
    async for event in orch.run("t3", "Find something and summarize it"):
        events.append(event)

    # Pipeline must NOT abort outright — Writer should still run and
    # produce a degraded message.
    assert events[-1].type == EventType.PIPELINE_DONE
    assert "unable to fully complete" in events[-1].payload["final_output"]

    # Confirm the analyzer step is explicitly marked FAILED, not silently
    # dropped.
    failed_events = [e for e in events if e.type == EventType.STEP_FAILED]
    assert any("analyze_1" in e.message for e in failed_events)


@pytest.mark.asyncio
async def test_empty_request_aborts_pipeline_cleanly():
    orch = Orchestrator()

    events = []
    async for event in orch.run("t4", "   "):
        events.append(event)

    assert len(events) == 1
    assert events[0].type == EventType.PIPELINE_ABORTED
