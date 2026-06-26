"""
tests/test_batching.py

WHAT THIS FILE DOES:
Unit tests for utils/batching.py — verifies chunking is correct and that
run_batched() preserves order and respects concurrency limits.

WHY IT EXISTS:
Batching is one of the trickiest pieces to get subtly wrong (off-by-one
chunk boundaries, results arriving out of order due to concurrency). A
dedicated test file catches regressions here independent of the rest of
the pipeline.
"""

import asyncio

import pytest

from utils.batching import chunk_list, run_batched


def test_chunk_list_exact_multiple():
    assert chunk_list([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_chunk_list_remainder():
    assert chunk_list([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_chunk_list_batch_size_larger_than_list():
    assert chunk_list([1, 2], 10) == [[1, 2]]


def test_chunk_list_rejects_invalid_batch_size():
    with pytest.raises(ValueError):
        chunk_list([1, 2, 3], 0)


@pytest.mark.asyncio
async def test_run_batched_preserves_order():
    async def worker(x: int) -> int:
        # Deliberately sleep LONGER for earlier items, to prove that
        # even if later items finish first, the returned list is still
        # in original input order (not completion order).
        await asyncio.sleep(0.05 if x == 1 else 0.01)
        return x * 10

    results = await run_batched([1, 2, 3, 4, 5], worker, batch_size=2, max_concurrency_per_batch=2)
    assert results == [10, 20, 30, 40, 50]


@pytest.mark.asyncio
async def test_run_batched_respects_concurrency_limit():
    active = 0
    max_active_seen = 0

    async def worker(x: int) -> int:
        nonlocal active, max_active_seen
        active += 1
        max_active_seen = max(max_active_seen, active)
        await asyncio.sleep(0.02)
        active -= 1
        return x

    await run_batched(list(range(9)), worker, batch_size=9, max_concurrency_per_batch=3)
    assert max_active_seen <= 3
