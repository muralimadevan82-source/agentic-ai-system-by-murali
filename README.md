# Agentic AI System for Multi-Step Tasks

A from-scratch, framework-free multi-agent pipeline that takes a complex,
multi-part user request, breaks it into ordered steps, and routes those
steps through specialized agents — **Planner → Retriever → Analyzer →
Validator → Writer** — using plain `asyncio`. No CrewAI, AutoGen, LangGraph,
or similar black-box orchestration libraries.

Built to be **read, explained, and defended in an internship review**.
Every line is plain Python you can step through in a debugger.

---

## Features

- **Multi-step decomposition**: Accepts complex requests like "Find X and
  compare Y, then summarize Z" and splits them into ordered sub-tasks.
- **Specialized agents**: Planner, Retriever, Analyzer, Validator, and
  Writer — each with a single responsibility.
- **Async pipeline**: Built on Python `asyncio` with async generators for
  real-time streaming.
- **Streaming output**: Progress is printed step-by-step as it happens,
  not buffered until the end.
- **Manual batching**: Retrieval steps run in controlled batches with
  configurable concurrency limits — no black-box batching frameworks.
- **Typed failure handling**: `TransientError` vs `PermanentError`
  split ensures retries only happen when they could succeed.
- **Graceful degradation**: A failed retrieval doesn't crash the
  pipeline; the system produces an honest, partial answer.
- **Fully tested**: 18+ unit and integration tests covering happy path,
  retries, validation, and degradation scenarios.
- **Zero external runtime dependencies**: Only requires Python 3.10+ and
  the standard library.

---

## Installation

```bash
# Clone the repository
git clone https://github.com/muralimadevan82-source/agentic-ai-system-by-murali.git
cd agentic-ai-system-by-murali

# No pip install needed to RUN — Python standard library only.

# To run tests:
pip install pytest pytest-asyncio
python -m pytest tests/ -v
```

**Requires Python 3.10+** (uses `match`-compatible exception handling,
`asyncio` features).

---

## Usage

### Command-line mode
```bash
python main.py "Find the top 3 AI startups in Bangalore and compare their funding and then summarize which is the best bet for a fresher"
```

### Interactive mode
```bash
python main.py
# Enter your multi-step request: Find X and compare Y, then summarize Z
```

### Example output
```
=== Running request [a1b2c3d4] ===
Request: Find the top 3 AI startups in Bangalore and compare their funding and then summarize which is the best bet for a fresher

🗺️  Plan ready with 5 step(s): ['retrieve_1', 'retrieve_2', 'retrieve_3', 'analyze_1', 'validate_1', 'write_1']
▶️  Starting batched retrieval for 3 sub-queries...
▶️  [retrieve_1] Retrieve information for: 'Find the top 3 AI startups in Bangalore'
▶️  [retrieve_2] Retrieve information for: 'compare their funding'
▶️  [retrieve_3] Retrieve information for: 'summarize which is the best bet for a fresher'
🔁 [retrieve_1] transient failure (Simulated timeout...) — retrying in 0.5s (attempt 2/3)
✅ [retrieve_2] done in 0.45s (attempt 1/3)
✅ [retrieve_3] done in 0.62s (attempt 1/3)
✅ [retrieve_1] done in 0.51s (attempt 2/3)
▶️  [analyze_1] Analyze and synthesize all retrieved information
✅ [analyze_1] done in 0.02s (attempt 1/3)
▶️  [validate_1] Validate the analysis output before writing
✅ [validate_1] done in 0.01s (attempt 1/3)
▶️  [write_1] Compose the final answer for the user
✅ [write_1] done in 0.01s (attempt 1/3)
🏁 Pipeline complete.

============================================================
FINAL ANSWER
============================================================
Here is what I found for: "Find the top 3 AI startups in Bangalore..."

  • On 'Find the top 3 AI startups in Bangalore': Relevant information found...
  • On 'compare their funding': Relevant information found...
  • On 'summarize which is the best bet for a fresher': Relevant information found...

(Confidence: 83% avg across 3 source(s))
============================================================
```

---

## Architecture

### Pipeline flow
```
User Request
     │
     ▼
┌─────────────┐
│  Planner     │  Decomposes request into ordered steps
└──────┬──────┘
       │ plan = [retrieve_1, retrieve_2, ..., analyze_1, validate_1, write_1]
       ▼
┌─────────────────────────────────────────────┐
│ Orchestrator (async generator)              │
│  • Batches retrieval steps (manual batching) │
│  • Runs Analyzer → Validator → Writer seq.   │
│  • Handles retries with linear backoff      │
│  • Yields StreamEvent after each action      │
└──────┬──────┬──────┬──────┬──────┬──────────┘
       │      │      │      │      │
       ▼      ▼      ▼      ▼      ▼
    Retr.  Retr.  Retr.  Analyz. Valid. Writer
    (batch)                │  (gate)  │
                           ▼          ▼
                       TaskContext.history
```

### Key design decisions

| Decision | Rationale |
|----------|-----------|
| **No agent framework** | Every retry, batch, and dependency is explicit Python — nothing hidden by a framework. |
| **Typed exceptions** | `TransientError` triggers retries; `PermanentError` fails fast — decided by type, not string matching. |
| **Shared TaskContext** | Agents communicate through a shared result history, not by calling each other. |
| **Async generator for streaming** | `orchestrator.run()` yields events the moment they happen; the caller decides how to render them. |
| **Manual batching for retrieval** | `chunk_list()` + `asyncio.Semaphore` = explicit, framework-free concurrency control. |

See [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) for the full architecture deep-dive.

---

## Project structure

```
agentic-ai-system/
├── main.py                  # CLI entry point (async generator consumer)
├── core/
│   ├── models.py             # Step / StepResult / TaskContext data contracts
│   ├── orchestrator.py       # Pipeline engine: dispatch, retries, batching, streaming
│   └── exceptions.py         # TransientError / PermanentError / PipelineAbortError
├── agents/
│   ├── base_agent.py          # Abstract contract all agents implement
│   ├── planner_agent.py        # Decomposes request into ordered Step objects
│   ├── retriever_agent.py       # Simulated data retrieval (batched + concurrent)
│   ├── analyzer_agent.py        # Synthesizes retrieved data, tolerates partial failures
│   ├── validator_agent.py        # Quality gate: validates analysis before writing
│   └── writer_agent.py          # Composes final answer, degrades gracefully on failure
├── utils/
│   ├── batching.py             # Manual chunking + concurrency-limited batch runner
│   ├── streaming.py            # StreamEvent type + console printer
│   └── logger.py               # Centralized logging setup
├── tests/                    # 18+ pytest unit + integration tests
├── docs/
│   ├── sample_requests.md    # Example inputs and expected outputs
│   ├── interview_qa.md       # Model answers for internship review
│   └── demo_script.md        # Script for video demo walkthrough
├── SYSTEM_DESIGN.md          # Architecture, data flow, and design rationale
├── POST_MORTEM.md            # Scaling issues, trade-offs, and retrospective
├── LICENSE                   # MIT license
├── requirements.txt          # Test-only dependencies (pytest)
├── pytest.ini               # pytest-asyncio config
└── .gitignore
```

---

## Technology stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.10+ |
| Async runtime | `asyncio` (standard library) |
| Data models | `dataclasses` (standard library) |
| Enums | `enum` (standard library) |
| Logging | `logging` (standard library) |
| Testing | pytest + pytest-asyncio |
| External dependencies | **None for runtime** — only pytest for testing |

---

## Design principles

1. **No black-box frameworks.** Every retry, every batch, every dependency
   resolution is plain Python you can step through in a debugger.
2. **Typed failure handling.** `TransientError` vs `PermanentError` is a
   hard split — only transient failures get retried.
3. **Graceful degradation over hard failure.** A failed retrieval doesn't
   kill the pipeline. A failed analysis doesn't crash the Writer. Only an
   unplannable request aborts the whole run.
4. **One job per agent.** Planner decides *what*. Retriever decides
   *fetch it*. Analyzer decides *what it means*. Validator decides *is it
   good enough*. Writer decides *how to say it*.

---

## Explanation Video

A full walkthrough demo of the system is available on Google Drive:

[**Watch the explanation video**](https://drive.google.com/file/d/1mRqAN6DuOnREG9hhsnuQTdl_Yj_tQctf/view?usp=sharing)

The video demonstrates:
- A live run with a complex multi-step request
- Streaming partial output as the pipeline executes
- A deliberate failure case (all retrievals forced to fail) showing graceful degradation

---

## License

MIT — see [LICENSE](LICENSE).
