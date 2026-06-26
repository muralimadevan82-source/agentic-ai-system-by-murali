"""
agents/retriever_agent.py

WHAT THIS FILE DOES:
Simulates fetching raw information for a single sub-query (think: a web
search, a database lookup, or a vector-store query). Returns a small
structured "document" with the query, a fabricated snippet, and a source
label.

WHY IT EXISTS:
Multi-step agentic systems almost always need a "go get me data" stage
that is SEPARATE from the "make sense of the data" stage. Keeping
retrieval as its own agent means:
  - It's the natural place to put BATCHING (utils/batching.py), since
    retrieval is the I/O-bound, embarrassingly-parallel part of the
    pipeline.
  - It's the natural place to simulate TRANSIENT failures (timeouts,
    rate limits) because that is what real retrieval calls fail with.

WHY SIMULATED INSTEAD OF A REAL API CALL:
This reference implementation does not call any real search/API service
so the project runs with zero API keys and zero network dependency for
grading. The `_fetch_raw()` method is the single, clearly marked place
you would swap in `requests`/`httpx` calls to a real search API, a vector
DB, or a SQL database — nothing else in the system needs to change
because everything downstream only depends on the returned dict shape.

HOW IT INTERACTS WITH OTHER COMPONENTS:
- The orchestrator calls `execute()` for each retrieval Step in the plan.
- In this implementation, retrieval steps are executed via
  `run_batched()` (see core/orchestrator.py) so multiple sub-queries are
  fetched concurrently, in controlled batches, instead of one at a time.
- Its output feeds directly into AnalyzerAgent.
"""

"""
agents/retriever_agent.py

WHAT THIS FILE DOES:
Implements RetrieverAgent — the data-fetching workhorse of the pipeline.
For each sub-query, it simulates an I/O-bound retrieval call with
configurable latency and failure rate.

WHY THIS EXISTS:
Retrieval is the stage most analogous to real-world API calls / DB queries.
It's also where the system demonstrates manual batching (utils/batching.py)
and transient failure handling with retry logic.

HOW IT INTERACTS WITH OTHER COMPONENTS:
- Invoked by the orchestrator for every retrieval step in the plan.
- Runs concurrently within batches via run_batched().
- Raises TransientError for simulated failures — orchestrator handles retries.
- Writes its output (a dict with query, snippet, source, confidence) to
  TaskContext.history for AnalyzerAgent to consume.
"""

import asyncio
import random

from agents.base_agent import BaseAgent
from core.exceptions import TransientError
from core.models import Step, TaskContext
from utils.logger import get_logger

log = get_logger("RetrieverAgent")


class RetrieverAgent(BaseAgent):
    def __init__(self, *, simulated_failure_rate: float = 0.2):
        super().__init__(name="RetrieverAgent")
        # Knob to make transient failures reproducible/demoable. In a real
        # system this would just be "however often the network actually
        # fails" — here we make it explicit and tunable for testing the
        # orchestrator's retry logic on command.
        self.simulated_failure_rate = simulated_failure_rate

    async def execute(self, step: Step, context: TaskContext) -> dict:
        log.info(f"Retrieving data for sub-query: '{step.input_query}'")
        return await self._fetch_raw(step.input_query)

    async def _fetch_raw(self, query: str) -> dict:
        """
        Simulates a network-bound retrieval call.

        FAILURE SCENARIO #1 (TRANSIENT): randomly raises TransientError to
        simulate a flaky network/API timeout. This is realistic — real
        retrieval calls (web search APIs, vector DBs under load) really do
        fail intermittently, and the correct response is "retry with
        backoff", not "give up immediately". See orchestrator.py's
        `_run_with_retry()` for how this is handled.
        """
        # Simulated network latency — this is WHY retrieval benefits from
        # batching/concurrency: each call "blocks" for a noticeable time.
        await asyncio.sleep(random.uniform(0.3, 0.8))

        if random.random() < self.simulated_failure_rate:
            raise TransientError(
                self.name,
                f"Simulated timeout while retrieving data for '{query}'",
            )

        # Fabricated but structurally realistic "document" result.
        return {
            "query": query,
            "snippet": f"Relevant information found regarding '{query}'. "
                       f"(simulated retrieval result)",
            "source": f"source://simulated-index/{abs(hash(query)) % 1000}",
            "confidence": round(random.uniform(0.6, 0.98), 2),
        }
