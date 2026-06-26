"""
tests/test_agents.py

WHAT THIS FILE DOES:
Tests each agent IN ISOLATION (without running the full orchestrator),
to verify each one's contract independently:
  - PlannerAgent decomposes multi-part requests into multiple steps.
  - AnalyzerAgent raises StepValidationError when all upstream data is missing.
  - WriterAgent degrades gracefully when analysis failed.

WHY IT EXISTS:
Testing agents standalone (passing in a hand-built TaskContext) is faster
and more precise than always going through the full orchestrator — it
lets us pinpoint exactly which agent's logic is responsible if a test
fails.
"""

import pytest

from agents.analyzer_agent import AnalyzerAgent
from agents.planner_agent import PlannerAgent
from agents.validator_agent import ValidatorAgent
from agents.writer_agent import WriterAgent
from core.exceptions import PermanentError, StepValidationError
from core.models import Step, StepResult, StepStatus, TaskContext


@pytest.mark.asyncio
async def test_planner_splits_multi_part_request_into_multiple_steps():
    planner = PlannerAgent()
    context = TaskContext(request_id="r1", original_request="x")
    kickoff = Step(
        step_id="plan_0", description="", agent="planner",
        input_query="Find AI startups in Bangalore and compare their funding and then summarize the best one",
    )
    plan = await planner.execute(kickoff, context)

    retrieval_steps = [s for s in plan if s.agent == "retriever"]
    assert len(retrieval_steps) >= 2  # decomposed into multiple sub-queries
    assert plan[-1].agent == "writer"  # writer always last
    assert any(s.agent == "analyzer" for s in plan)


@pytest.mark.asyncio
async def test_planner_rejects_empty_request():
    planner = PlannerAgent()
    context = TaskContext(request_id="r2", original_request="")
    kickoff = Step(step_id="plan_0", description="", agent="planner", input_query="   ")

    with pytest.raises(PermanentError):
        await planner.execute(kickoff, context)


@pytest.mark.asyncio
async def test_analyzer_raises_when_all_upstream_failed():
    analyzer = AnalyzerAgent()
    context = TaskContext(request_id="r3", original_request="x")
    # Simulate one retrieval step that already failed.
    context.history.append(
        StepResult(step_id="retrieve_1", agent="retriever", status=StepStatus.FAILED, error="boom")
    )
    step = Step(
        step_id="analyze_1", description="", agent="analyzer",
        input_query="x", depends_on="retrieve_1",
    )

    with pytest.raises(StepValidationError):
        await analyzer.execute(step, context)


@pytest.mark.asyncio
async def test_writer_degrades_gracefully_when_analysis_missing():
    writer = WriterAgent()
    context = TaskContext(request_id="r4", original_request="test request")
    # No analyze_1 result exists in history at all.
    step = Step(step_id="write_1", description="", agent="writer", input_query="x", depends_on="analyze_1")

    output = await writer.execute(step, context)
    assert "unable to fully complete" in output
    assert "test request" in output


@pytest.mark.asyncio
async def test_writer_produces_normal_output_on_success():
    writer = WriterAgent()
    context = TaskContext(request_id="r5", original_request="test request")
    context.history.append(
        StepResult(
            step_id="analyze_1", agent="analyzer", status=StepStatus.SUCCESS,
            output={
                "key_points": ["On 'x': found something"],
                "average_confidence": 0.85,
                "low_confidence_count": 0,
                "documents_used": 1,
                "documents_missing": 0,
            },
        )
    )
    step = Step(step_id="write_1", description="", agent="writer", input_query="x", depends_on="analyze_1")

    output = await writer.execute(step, context)
    assert "found something" in output
    assert "85%" in output


@pytest.mark.asyncio
async def test_validator_passes_on_good_analysis():
    validator = ValidatorAgent()
    context = TaskContext(request_id="r6", original_request="x")
    context.history.append(
        StepResult(
            step_id="analyze_1", agent="analyzer", status=StepStatus.SUCCESS,
            output={
                "key_points": ["On 'x': found something"],
                "average_confidence": 0.85,
                "low_confidence_count": 0,
                "documents_used": 2,
                "documents_missing": 0,
            },
        )
    )
    step = Step(step_id="validate_1", description="", agent="validator", input_query="x", depends_on="analyze_1")

    result = await validator.execute(step, context)
    assert result["validation_status"] == "passed"
    assert "key_points" in result  # pass-through field
    assert result["average_confidence"] == 0.85


@pytest.mark.asyncio
async def test_validator_rejects_empty_analysis():
    validator = ValidatorAgent()
    context = TaskContext(request_id="r7", original_request="x")
    context.history.append(
        StepResult(
            step_id="analyze_1", agent="analyzer", status=StepStatus.SUCCESS,
            output={
                "key_points": [],
                "average_confidence": 0.0,
                "low_confidence_count": 0,
                "documents_used": 0,
                "documents_missing": 2,
            },
        )
    )
    step = Step(step_id="validate_1", description="", agent="validator", input_query="x", depends_on="analyze_1")

    with pytest.raises(StepValidationError):
        await validator.execute(step, context)


@pytest.mark.asyncio
async def test_validator_rejects_low_confidence():
    validator = ValidatorAgent()
    context = TaskContext(request_id="r8", original_request="x")
    context.history.append(
        StepResult(
            step_id="analyze_1", agent="analyzer", status=StepStatus.SUCCESS,
            output={
                "key_points": ["On 'x': found something"],
                "average_confidence": 0.1,
                "low_confidence_count": 1,
                "documents_used": 2,
                "documents_missing": 0,
            },
        )
    )
    step = Step(step_id="validate_1", description="", agent="validator", input_query="x", depends_on="analyze_1")

    with pytest.raises(StepValidationError):
        await validator.execute(step, context)
