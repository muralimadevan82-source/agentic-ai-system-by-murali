# Post-Mortem / Engineering Reflection

This document is written as if this system had already been run in a
real internship/production setting for a while — it reflects honestly on
what would break first, what's intentionally left out, and why specific
trade-offs were made.

---

## 1. Scaling issue

**Problem: `TaskContext.history` is an unbounded in-memory list, and
`get_result()` does a linear scan over it.**

Right now, every `StepResult` for a request lives in one Python list for
the lifetime of that request, and looking up a step's result by ID is an
`O(n)` scan (`core/models.py::get_result`). For a 5-step pipeline this is
irrelevant. But if this system were extended to handle requests that
decompose into **hundreds of retrieval steps** (e.g. "summarize every
press release this company has issued since 2015"), two things degrade:

1. Every `get_result()` lookup gets slower as `history` grows, and
   `AnalyzerAgent` calls it once per dependency — so the analysis step's
   setup cost grows roughly with the square of the number of upstream
   steps in the worst case.
2. The whole `TaskContext` — and therefore every retrieved document — is
   held in memory for the entire request lifetime, with no spill-to-disk
   or streaming-summarization strategy. A high-fan-out request could
   exhaust memory before the Analyzer even runs.

**What I'd do about it:** swap `history: list[StepResult]` for a `dict[str,
StepResult]` (O(1) lookup by `step_id`), and introduce incremental/rolling
analysis — have the Analyzer summarize retrieval batches AS they complete
(streaming summarization) rather than waiting to hold every raw document
in memory at once.

---

## 2. Future improvement

**Replace the rule-based Planner with a real LLM-backed planner, behind
the exact same `list[Step]` contract.**

The current `PlannerAgent._split_into_subqueries()` is a regex/heuristic
splitter. It works well for the kind of requests this assignment expects
("Find X and Y, then summarize Z"), but it will mis-segment more
ambiguous natural language (e.g. requests with nested clauses, or
implicit sub-tasks that aren't joined by "and"/"then" at all).

The fix is already scoped out in code — see the `EXTENSION POINT` comment
in `agents/planner_agent.py`. Because every other agent only depends on
`Planner` producing a `list[Step]`, not on HOW it was produced, swapping
in an LLM call there requires zero changes anywhere else in the system.
This was a deliberate design choice specifically so this improvement is
a single-file change, not a refactor.

---

## 3. Engineering trade-offs

### Trade-off A: Linear backoff instead of exponential backoff with jitter

**What we did:** `retry_backoff_seconds * attempts` — a simple linear
increase (0.5s, 1.0s, 1.5s, ...).

**What a production system would more likely use:** exponential backoff
with random jitter (to avoid many clients retrying in lockstep against
the same flaky service — the "thundering herd" problem).

**Why we chose the simpler version anyway:** the assignment explicitly
values explainability over completeness, and linear backoff is something
you can compute in your head while reading the log output, which matters
a lot when explaining this system live in a review. Exponential
backoff + jitter is a five-minute upgrade (`utils/batching.py`-adjacent
helper) if this ever needed to survive a real flaky upstream API at
scale — but it wasn't worth the extra cognitive load for a system whose
explicit goal is "a beginner-to-intermediate developer can defend this."

### Trade-off B: Tolerating partial retrieval failures instead of failing the whole request

**What we did:** if 2 out of 3 retrieval sub-queries succeed,
`AnalyzerAgent` proceeds with the 2 it has, and the final answer
explicitly notes "1 source unavailable" rather than failing the entire
request.

**The alternative:** treat ANY retrieval failure as fatal to the whole
pipeline (fail closed), which is simpler to implement and arguably
"safer" in domains where partial information is actively misleading
(e.g. medical or financial decision systems).

**Why we chose partial tolerance:** for a general-purpose
research-assistant-style task (the kind this assignment models), a
slightly-incomplete answer with an honest confidence caveat is far more
useful to a user than a hard failure over one flaky sub-query out of
several. The trade-off is explicitly NOT free, though — it means the
Writer must ALWAYS be defensive about missing/partial analysis (see
`writer_agent.py`'s degraded-output branch), which is more code than
"just crash and let the caller retry the whole request." We judged that
extra defensiveness worth it for the better user experience it buys.
