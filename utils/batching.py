"""
utils/batching.py

WHAT THIS FILE DOES:
Implements MANUAL batching for a list of async jobs:
  1. Splits a list of items into fixed-size chunks (`chunk_list`).
  2. Runs each chunk concurrently, but caps how many jobs run AT THE SAME
     TIME using a semaphore (`run_batched`), instead of firing everything
     at once with a single asyncio.gather().

WHY THIS EXISTS (and why it's not just `asyncio.gather(*everything)`):
If the RetrieverAgent needs to fetch data for 50 sub-queries, firing 50
concurrent requests at once would be unrealistic for any real API (rate
limits) and unrealistic for the assignment's "manual batching" requirement.
Real systems batch: process N items concurrently, wait for that batch to
settle, then move to the next batch. This file is the from-scratch
implementation of that idea — no third-party batching/queueing library.

HOW IT INTERACTS WITH OTHER COMPONENTS:
- RetrieverAgent uses `run_batched()` to fetch multiple sub-queries
  concurrently within a controlled batch size.
- The Orchestrator does NOT use this directly — batching is an
  agent-level concern (specifically Retriever's), not a pipeline-level one.
"""

import asyncio
from typing import Awaitable, Callable, TypeVar

from utils.logger import get_logger

log = get_logger("Batching")

T = TypeVar("T")
R = TypeVar("R")


def chunk_list(items: list[T], batch_size: int) -> list[list[T]]:
    """
    Manual chunking — split `items` into consecutive lists of at most
    `batch_size` elements.

    Example: chunk_list([1,2,3,4,5], 2) -> [[1,2], [3,4], [5]]

    WHY WRITE THIS BY HAND instead of using e.g. itertools.batched()?
    itertools.batched() only exists from Python 3.12+, and the explicit
    loop is something every reviewer can read in 5 seconds with zero
    library lookups — that's the "explainability over cleverness" goal.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    chunks: list[list[T]] = []
    for i in range(0, len(items), batch_size):
        chunks.append(items[i : i + batch_size])
    return chunks


async def run_batched(
    items: list[T],
    worker: Callable[[T], Awaitable[R]],
    batch_size: int = 3,
    max_concurrency_per_batch: int = 3,
) -> list[R]:
    """
    Run `worker(item)` for every item in `items`, processing items in
    manually-defined batches of `batch_size`.

    Within a batch, up to `max_concurrency_per_batch` jobs run truly
    concurrently (via asyncio.Semaphore) — the rest in that batch queue up
    behind the semaphore. Batches run one after another, NOT all at once.

    This two-level control (batch boundaries + a semaphore inside each
    batch) is the "manual batching" the assignment asks for: we are
    explicitly deciding concurrency, not delegating it to a framework.

    Returns results in the SAME ORDER as the input `items`, even though
    execution order inside a batch is concurrent (not guaranteed
    sequential) — this matters because callers (e.g. RetrieverAgent)
    need to know which result maps to which original query.
    """
    semaphore = asyncio.Semaphore(max_concurrency_per_batch)

    async def _guarded(item: T) -> R:
        async with semaphore:
            return await worker(item)

    batches = chunk_list(items, batch_size)
    all_results: list[R] = []

    for batch_index, batch in enumerate(batches, start=1):
        log.info(
            f"Processing batch {batch_index}/{len(batches)} "
            f"({len(batch)} item(s), max {max_concurrency_per_batch} concurrent)"
        )
        # asyncio.gather runs the jobs in THIS batch concurrently (bounded
        # by the semaphore above). We deliberately await each batch fully
        # before starting the next one — that boundary IS the "batch".
        batch_results = await asyncio.gather(*[_guarded(item) for item in batch])
        all_results.extend(batch_results)

    return all_results
