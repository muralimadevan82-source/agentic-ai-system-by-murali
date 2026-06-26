# System Architecture

## 1. High-level data flow

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
                                         │  plan = [retrieve_1, retrieve_2,
                                         │          retrieve_3, analyze_1, write_1]
                                         ▼
                  ┌───────────────────────────────────────────┐
                  │           ORCHESTRATOR (asyncio)             │
                  │  groups retrieval steps -> manual batching   │
                  └───────────────────┬───────────────────────┘
                                      │
            ┌─────────────────────────┼─────────────────────────┐
            ▼                         ▼                         ▼
   ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
   │ RetrieverAgent    │      │ RetrieverAgent    │      │ RetrieverAgent    │
   │  (sub-query 1)     │      │  (sub-query 2)     │      │  (sub-query 3)     │
   │  batch slot 1/3     │      │  batch slot 2/3     │      │  batch slot 3/3     │
   └────────┬────────┘      └────────┬────────┘      └────────┬────────┘
            │   (each call may TransientError -> retried w/ backoff)
            └─────────────────────────┼─────────────────────────┘
                                      ▼
                         ┌────────────────────────┐
                         │     AnalyzerAgent         │
                         │  reads ALL retrieval        │
                         │  results from shared          │
                         │  TaskContext.history            │
                         │  tolerates partial failures      │
                         └────────────┬───────────┘
                                      ▼
                         ┌────────────────────────┐
                         │      WriterAgent           │
                         │  composes final answer,     │
                         │  degrades gracefully if       │
                         │  analysis failed                │
                         └────────────┬───────────┘
                                      ▼
                       ┌──────────────────────────┐
                       │  STREAMED StreamEvents      │
                       │  -> printed live by main.py   │
                       │  -> final answer printed last   │
                       └──────────────────────────┘
```

## 2. The shared "notebook": `TaskContext`

Every agent receives the SAME `TaskContext` object for one request. It
holds:
- `original_request` — the raw user text.
- `plan` — the ordered list of `Step`s produced by the Planner.
- `history` — a growing list of `StepResult`s, appended to as each step
  finishes (success or failure).

Agents never call each other directly. Agent B finds Agent A's output by
looking it up in `context.history` via `step.depends_on`. This is the
**only** coupling between agents — and it's data coupling, not code
coupling. You could delete `RetrieverAgent` entirely and replace it with
a totally different implementation, and `AnalyzerAgent` would not need a
single line changed, as long as the new agent still appends a
`StepResult` with the same output shape.

## 3. Why a `Step.agent` string instead of passing agent objects around

`Step.agent` is just `"retriever"`, `"analyzer"`, etc. — a string key into
`Orchestrator.agents`, a plain dict. This keeps `Step` a pure data object
with no behavior and no imports of agent classes, which means
`core/models.py` has zero dependency on `agents/`. That's a deliberate
one-way dependency: `agents/` depends on `core/`, never the other way.
This is what lets you unit-test `core/models.py` and `core/exceptions.py`
without importing a single agent.

## 4. Why retrieval is batched but Analyzer/Writer are not

Retrieval is the only "many similar, independent, I/O-bound jobs" stage —
exactly the shape of problem batching solves. Analyzer and Writer are
each a SINGLE step that depends on ALL prior output, so there is nothing
to batch; running them is just one more `_run_single_step()` call.

This asymmetry is intentional, not an oversight: not every stage of an
agentic pipeline benefits from the same execution strategy, and forcing
a generic "batch everything" abstraction across the whole orchestrator
would have made the simplest stages (Analyzer, Writer) needlessly
roundabout.

## 5. Why retries are step-scoped, not pipeline-scoped

If `retrieve_2` fails 3 times, only `retrieve_2` is retried — we don't
restart the whole pipeline from the Planner. This matters for both
correctness (re-running Planner would produce a NEW plan, possibly with
different step IDs, breaking the in-flight batch) and efficiency (no
reason to re-fetch `retrieve_1`'s data just because `retrieve_2` was
flaky).

## 6. Why streaming is implemented as an async generator, not a callback

Two common ways to "stream" progress:
1. Pass a `callback` function into `orchestrator.run()` that gets called
   with progress updates.
2. Make `run()` itself an `async def ... yield` generator.

We chose (2) because it keeps control flow in the CALLER's hands —
`main.py` decides what to do with each event (print it, log it, send it
over a websocket) using ordinary `async for`, rather than the
orchestrator needing to know anything about WHERE updates should go.
Callbacks invert this and tend to entangle "how do I report progress"
logic into the orchestrator itself.

## 7. Concurrency model summary

| Stage      | Execution style                          | Why                                   |
|------------|-------------------------------------------|----------------------------------------|
| Planner    | Single awaited call                       | Decomposition is one fast, cheap op    |
| Retriever  | Batched + bounded concurrency (semaphore) | Many similar I/O-bound calls           |
| Analyzer   | Single awaited call, after ALL retrievals | Needs the full picture to synthesize   |
| Writer     | Single awaited call, after Analyzer       | Terminal step, nothing after it        |
