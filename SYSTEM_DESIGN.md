# System Design Document

## 1. Architecture Overview

The system is a **pipeline-based multi-agent architecture** built entirely
on Python's `asyncio` standard library. Each stage of the pipeline is a
specialized agent with a single responsibility. The Orchestrator
(`core/orchestrator.py`) is the only component that knows about the
pipeline structure — agents are isolated from each other and communicate
through a shared `TaskContext` data object.

### High-level flow

```
                        ┌──────────────────────┐
                        │   USER REQUEST (CLI)  │
                        │  "Find X and Y, then  │
                        │   summarize Z"         │
                        └──────────┬────────────┘
                                   │
                                   ▼
                        ┌──────────────────────┐
                        │     PlannerAgent       │
                        │  splits request into   │
                        │  ordered Step objects   │
                        └──────────┬────────────┘
                                   │ plan = [retrieve_1, retrieve_2,
                                   │         retrieve_3, analyze_1,
                                   │         validate_1, write_1]
                                   ▼
              ┌───────────────────────────────────────────┐
              │           ORCHESTRATOR (asyncio)             │
              │  • Groups retrieval steps → manual batching │
              │  • Runs Analyzer → Validator → Writer seq.  │
              │  • Handles retries with linear backoff      │
              │  • Yields StreamEvent as async generator     │
              └───────────────────┬───────────────────────┘
                                  │
    ┌─────────────────────────────┼─────────────────────────────┐
    ▼                             ▼                             ▼
┌──────────┐                ┌──────────┐                ┌──────────┐
│ Retriever │                │ Retriever │                │ Retriever │
│ (query 1) │                │ (query 2) │                │ (query 3) │
│ batch 1/1 │                │ batch 1/1 │                │ batch 1/1 │
└─────┬────┘                └─────┬────┘                └─────┬────┘
      └───────────────────────────┼───────────────────────────┘
                                  ▼
                     ┌────────────────────────┐
                     │     AnalyzerAgent        │
                     │  reads ALL retrieval      │
                     │  results from shared        │
                     │  TaskContext.history          │
                     │  tolerates partial failures    │
                     └────────────┬───────────┘
                                  ▼
                     ┌────────────────────────┐
                     │    ValidatorAgent         │
                     │  quality gate: checks     │
                     │  confidence, completeness  │
                     └────────────┬───────────┘
                                  ▼
                     ┌────────────────────────┐
                     │      WriterAgent          │
                     │  composes final answer,    │
                     │  degrades gracefully if     │
                     │  analysis/validation failed  │
                     └────────────┬───────────┘
                                  ▼
                   ┌──────────────────────────┐
                   │  STREAMED StreamEvents      │
                   │  → printed live by main.py   │
                   │  → final answer printed last  │
                   └──────────────────────────┘
```

---

## 2. Data Flow

### Request lifecycle

1. **CLI input**: `main.py` reads a multi-step request from `sys.argv` or
   `input()`, generates a `request_id`, and calls
   `orchestrator.run(request_id, user_request)`.

2. **Async generator iteration**: `main.py` iterates over the result with
   `async for event in orchestrator.run(...)`, printing each
   `StreamEvent` as it arrives via `print_event()`.

3. **Planning phase**: The Orchestrator calls `PlannerAgent.execute()` to
   decompose the request into an ordered `list[Step]`. Each Step has a
   `step_id`, `agent` key, `input_query`, and optional `depends_on`.

4. **Retrieval phase**: Retrieval steps are collected and executed via
   `utils/batching.run_batched()`. Each runs in a controlled batch with
   bounded concurrency. Results are appended to `context.history`.

5. **Analysis phase**: The `analyze_1` step reads all retrieval results
   from history, synthesizes key points, and stores its analysis dict.

6. **Validation phase**: The `validate_1` step reads the analysis,
   checks confidence thresholds and completeness, and either passes or
   raises `StepValidationError`.

7. **Writing phase**: The `write_1` step reads the validated analysis
   (or detects failure) and composes the final answer string.

8. **Completion**: The Orchestrator yields `PIPELINE_DONE` with the final
   output in the payload.

### TaskContext: the shared notebook

Every agent receives the **same** `TaskContext` instance for a single
request. It holds:

- `original_request` — the raw user text.
- `plan` — the ordered `list[Step]` from the Planner.
- `history` — a growing `list[StepResult]`, appended as each step
  completes (whether success or failure).

Agents never call each other directly. Agent B finds Agent A's output by
looking it up via `context.get_result(step_id)` using the `depends_on`
field on the Step. This is **data coupling only** — you could replace any
agent entirely as long as it writes the same output shape to history.

---

## 3. Agent Responsibilities

### PlannerAgent (`agents/planner_agent.py`)
- **Purpose**: Decompose a raw natural-language request into a structured,
  ordered plan of `Step` objects.
- **How**: Rule-based splitting on conjunctions ("and", "then", "also")
  and sentence punctuation. Falls back to treating the whole request as
  one sub-query if splitting yields nothing.
- **Output**: `list[Step]` — 1 retriever step per sub-query, 1 analyzer
  step, 1 validator step, 1 writer step.
- **Failure mode**: `PermanentError` for empty/unplannable requests.
  Not retried — if you can't plan, there's nothing to execute.

### RetrieverAgent (`agents/retriever_agent.py`)
- **Purpose**: Simulate data retrieval for a single sub-query.
- **How**: `asyncio.sleep()` for simulated latency (0.3-0.8s). Has a
  tunable `simulated_failure_rate` that raises `TransientError` randomly.
- **Output**: `dict` with `query`, `snippet`, `source`, `confidence`.
- **Failure mode**: `TransientError` (retried with backoff) for
  simulated network failures.

### AnalyzerAgent (`agents/analyzer_agent.py`)
- **Purpose**: Synthesize all retrieval results into a structured analysis.
- **How**: Reads all upstream retrieval results from history. Computes
  average confidence, identifies low-confidence items, and aggregates
  key points.
- **Output**: `dict` with `key_points`, `average_confidence`,
  `low_confidence_count`, `documents_used`, `documents_missing`.
- **Failure mode**: `StepValidationError` (a `PermanentError`) when ALL
  upstream retrievals failed — nothing to analyze.

### ValidatorAgent (`agents/validator_agent.py`)
- **Purpose**: Quality gate between analysis and writing. Validates that
  the analysis meets minimum quality thresholds.
- **How**: Checks that key_points is non-empty, documents were used, and
  average confidence exceeds a minimum threshold (0.3).
- **Output**: `dict` with `validation_status`, `documents_validated`,
  `confidence_at_validation`.
- **Failure mode**: `StepValidationError` when validation fails. The
  Writer will then produce a degraded answer.

### WriterAgent (`agents/writer_agent.py`)
- **Purpose**: Compose the final human-readable answer.
- **How**: Reads the validated analysis (or detects failure) and formats
  a response with bullet points, confidence info, and caveats.
- **Output**: `str` — the final answer.
- **Failure mode**: Never fails hard. If analysis/validation failed,
  produces an honest "unable to fully complete" message.

---

## 4. Orchestrator Logic

The Orchestrator (`core/orchestrator.py`) is the heart of the system.
Its `run()` method is an **async generator** that implements the full
pipeline:

### Algorithm

```
1. Yield PIPELINE_STARTED
2. Try:
     plan = await _run_planner(request, context)
   Catch PermanentError:
     Yield PIPELINE_ABORTED and return

3. Yield PLAN_READY with step list

4. retrieval_steps = [s for s in plan if s.agent == "retriever"]
   For each event in _run_retrieval_batch(retrieval_steps, context):
     Yield event

5. For each step in [analyzer, validator, writer]:
     For each event in _run_single_step(step, context):
       Yield event

6. Yield PIPELINE_DONE with final_output
```

### Retry logic (`_run_single_step`)

```
attempts = 0
While attempts <= max_retries:
  Try:
    output = await agent.execute(step, context)
    Record SUCCESS and return
  Catch TransientError:
    If attempts < max_retries:
      Sleep(backoff * attempts)
      Continue (retry)
    Else: fall through to FAILED
  Catch PermanentError:
    Break (no retry)
  Catch Exception:
    Break (unexpected — don't retry blindly)

Record FAILED with last error message
```

### Recovery decisions

| Failed step | Effect |
|-------------|--------|
| Retriever | Pipeline continues. Analyzer tolerates missing sources. |
| Analyzer | Pipeline continues. Validator detects missing analysis. Writer degrades. |
| Validator | Pipeline continues. Writer detects missing validation and degrades. |
| Writer | No downstream steps — pipeline ends naturally. |

Only `Planner` failure aborts the entire pipeline (via `PIPELINE_ABORTED`).

---

## 5. Async Execution

### Architecture choices

- **`asyncio` for concurrency**: All agents implement `async def execute()`.
  The orchestrator uses `await` for sequential steps and
  `asyncio.gather()` (bounded by semaphore) for concurrent retrieval
  steps.
- **Async generator for streaming**: `async def run() -> AsyncIterator`
  yields `StreamEvent` objects after each meaningful action. The caller
  (`main.py`) drives the generator with `async for`.
- **No thread pool**: All concurrency is cooperative (single-threaded
  asyncio). This is appropriate for I/O-bound simulated work (sleeps)
  and avoids the complexity of thread safety.

### Why async and not threading

Python threads have the GIL limitation and require careful locking around
shared state (`TaskContext.history`). With asyncio, there's no true
parallelism (good — we don't need it for I/O-bound simulation), but there
is also no risk of race conditions on the shared context because only
one coroutine runs at a time (cooperative multitasking).

---

## 6. Streaming Implementation

### Design

The Orchestrator's `run()` method is an **async generator**:

```python
async def run(self, request_id, user_request):
    yield StreamEvent(EventType.PLAN_READY, ...)
    ...
    async for event in self._run_retrieval_batch(...):
        yield event
    ...
    yield StreamEvent(EventType.PIPELINE_DONE, ...)
```

The consumer (`main.py`) iterates:

```python
async for event in orchestrator.run(request_id, user_request):
    print_event(event)
```

### StreamEvent types

| EventType | When emitted | Payload |
|-----------|-------------|---------|
| `PLAN_READY` | After Planner succeeds | `{"plan": [step_ids]}` |
| `STEP_STARTED` | Before each step execution | — |
| `STEP_RETRY` | On transient failure with retry remaining | — |
| `STEP_SUCCEEDED` | After successful step | — |
| `STEP_FAILED` | After permanent failure or retries exhausted | — |
| `PIPELINE_DONE` | Pipeline complete | `{"final_output": str}` |
| `PIPELINE_ABORTED` | Pipeline aborted (Planner failure) | — |

### Why an async generator vs a callback

A callback (`orchestrator.run(on_event=print_event)`) inverts control and
couples the orchestrator to the output mechanism. An async generator keeps
control flow in the caller's hands — `main.py` decides what to do with
each event (print, log, send over websocket) using ordinary `async for`.

---

## 7. Failure Handling

### Exception hierarchy

```
Exception
├── AgentError
│   ├── TransientError   → triggers retry with backoff
│   └── PermanentError
│       └── StepValidationError  → fails immediately
└── PipelineAbortError   → raised by orchestrator itself
```

### Three failure scenarios

**Scenario 1: Transient retrieval failure → auto-retry → success**
- RetrieverAgent raises `TransientError` (simulated network timeout).
- Orchestrator catches it, sleeps `backoff * attempt_number`, retries.
- If retry succeeds: step recorded as SUCCESS with attempt count.
- If all retries exhausted: step recorded as FAILED.

**Scenario 2: All retrievals fail → Analyzer fails → Writer degrades**
- Every retrieval step exhausts its retries → FAILED.
- Analyzer finds zero documents → raises `StepValidationError`.
- Validator finds no analysis → raises `StepValidationError`.
- Writer detects missing analysis → produces degraded answer.

**Scenario 3: Empty/unplannable request → pipeline aborts**
- Planner receives empty/whitespace request → raises `PermanentError`.
- Orchestrator catches it → yields `PIPELINE_ABORTED` → stops.

### Retry configuration

```python
Orchestrator(
    max_retries=2,              # max retries per step
    retry_backoff_seconds=0.5,  # linear backoff base
)
```

Backoff formula: `sleep(retry_backoff_seconds * attempt_number)`
- Attempt 1: 0.5s
- Attempt 2: 1.0s
- Attempt 3: 1.5s

---

## 8. Manual Batching

### Why manual batching

The assignment requires implementing batching logic manually without
relying on black-box agent frameworks. Additionally, real-world retrieval
calls (API queries, DB lookups) are I/O-bound and can benefit from
concurrency — but firing unlimited concurrent requests is unrealistic due
to rate limits and resource constraints.

### Implementation (`utils/batching.py`)

Two functions provide the batching logic:

**`chunk_list(items, batch_size)`**
- Splits a list into fixed-size chunks.
- Example: `chunk_list([1,2,3,4,5], 2)` → `[[1,2], [3,4], [5]]`
- This is the "manual chunking" part of the requirement.

**`run_batched(items, worker, batch_size, max_concurrency_per_batch)`**
- Processes items in chunks of `batch_size`.
- Within each chunk, uses `asyncio.Semaphore(max_concurrency_per_batch)`
  to bound concurrent execution.
- Waits for one entire batch to finish before starting the next.
- Preserves input order in output.

### Two-level concurrency control

1. **Batch boundaries**: Items are divided into fixed-size chunks. Only
   one chunk processes at a time. This prevents resource exhaustion from
   a single huge batch.
2. **Semaphore within each batch**: Even within a chunk, a semaphore
   caps how many tasks run truly concurrently. The remaining tasks in
   the chunk queue behind the semaphore.

### Where batching is applied

Batching is applied **only to retrieval steps** — the stage most
analogous to real-world I/O-bound work. Analyzer, Validator, and Writer
are single, sequential steps with nothing to batch. This asymmetry is
intentional: not every stage benefits from the same execution strategy.

### Comparison with alternatives

| Approach | Pros | Cons |
|----------|------|------|
| `asyncio.gather(*all)` | Simple, fires everything | No rate limiting, resource exhaustion |
| `asyncio.Semaphore` alone | Bounds concurrency | No batching — all tasks start immediately |
| `run_batched()` (this system) | Bounds both batch size AND concurrency | Slightly more code |
| ThreadPoolExecutor | CPU-bound parallelism | GIL, thread safety complexity |
