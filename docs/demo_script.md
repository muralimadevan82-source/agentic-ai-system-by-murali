# Demo Video Script (3–5 minutes)

Target length: ~4 minutes. Talk naturally — this is a rough script, not
something to read word-for-word.

---

### [0:00 – 0:30] Intro — what this is

> "This is an agentic AI system I built for [internship name]'s
> multi-step task assignment. It takes a complex, multi-part request,
> breaks it into ordered steps, and routes those steps through four
> specialized agents — a Planner, a Retriever, an Analyzer, and a
> Writer — using plain Python asyncio. No CrewAI, no LangGraph, no
> AutoGen — every piece of orchestration logic here is something I wrote
> and can explain line by line."

*(Show the folder structure in the editor — `agents/`, `core/`,
`utils/`, `tests/`.)*

---

### [0:30 – 1:15] Architecture walkthrough

> "The flow is: Planner takes the raw request and splits it into Steps —
> one Retriever step per sub-question, one Analyzer step, one Writer
> step. The Orchestrator in `core/orchestrator.py` is the only file that
> knows about retries, batching, and streaming — every agent just
> implements one method, `execute()`, and doesn't know anything about the
> others."

*(Open `docs/architecture.md`, scroll to the data flow diagram for 5–10
seconds.)*

> "Agents never call each other directly — they share one `TaskContext`
> object, and look up earlier results from it by step ID. That's the
> only coupling in the whole system."

---

### [1:15 – 2:30] Live run — happy path + streaming

> "Let's run it on a real multi-part request."

*(Run in terminal:)*
```bash
python3 main.py "Find the top 3 AI startups in Bangalore and compare their funding and then summarize which is the best bet for a fresher"
```

> "Watch the output — this isn't printed all at once at the end. The
> Orchestrator's `run()` method is an async generator, so every step
> streams a progress event the moment it finishes."

*(Let it run, point out:)*
- "Here's the Planner's plan — 3 retrieval steps, 1 analysis step, 1
  writer step."
- "These three retrieval calls are running in a manually-batched group —
  up to 3 concurrent, using a semaphore I wrote in `utils/batching.py`,
  not `asyncio.gather()` on everything at once."
- "And here — see this retry? One of the simulated retrieval calls
  failed with a transient error, and the orchestrator automatically
  retried it with backoff instead of failing the whole pipeline."

---

### [2:30 – 3:30] Failure handling demo

> "Now let's deliberately break it to show the recovery logic."

*(Run the forced-failure snippet from the README/tests — e.g. set
`simulated_failure_rate = 1.0` and show retrieval exhausting retries.)*

> "All three retrieval calls failed even after retries. Watch what
> happens — the orchestrator doesn't crash the whole program. It marks
> the retrieval step FAILED, the Analyzer correctly detects it has zero
> usable input and raises a validation error — that's a PERMANENT error,
> so it's not retried, because retrying analysis on the same empty input
> would never succeed. And the Writer still produces an honest, labeled
> degraded answer instead of a stack trace."

*(Show the final degraded-answer text in the terminal.)*

> "That's one of three failure scenarios I specifically designed for —
> transient retrieval failure, total retrieval failure causing analysis
> validation failure, and an unplannable empty request, which is the one
> case that aborts the whole pipeline, because there's no plan to even
> start from."

---

### [3:30 – 4:00] Tests + wrap-up

> "All of this is covered by automated tests — 15 tests across batching,
> orchestration, and individual agents."

*(Run: `python3 -m pytest tests/ -v`, let the green output show
briefly.)*

> "The full design rationale, a post-mortem on scaling issues and
> trade-offs, and the architecture diagram are all in the `docs/`
> folder. That's the system — thanks for watching."
