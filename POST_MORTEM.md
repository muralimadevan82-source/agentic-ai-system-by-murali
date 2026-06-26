# Post-Mortem / Engineering Reflection

This document reflects honestly on what would break first at scale, what
would change in hindsight, and why specific engineering trade-offs were
made. Written as if the system had already been deployed in a real
internship/production setting.

---

## 1. Scaling Issue: Unbounded In-Memory History

**Problem: `TaskContext.history` is an `O(n)` linked list with no
spill-to-disk strategy.**

Right now, every `StepResult` for a request lives in one Python list for
the lifetime of that request, and `get_result()` does a linear scan over
it (`core/models.py:get_result`). For a 5-step pipeline this is
irrelevant. But if this system were extended to handle requests that
decompose into **hundreds of retrieval steps** (e.g. "summarize every
press release this company has issued since 2015"), two things degrade:

1. **Lookup performance**: Every `get_result()` call gets slower as
   `history` grows. `AnalyzerAgent` calls it once per dependency, so
   analysis setup cost grows roughly with the square of the number of
   upstream steps in the worst case.

2. **Memory pressure**: The entire `TaskContext` — and therefore every
   retrieved document — is held in memory for the entire request
   lifetime. A high-fan-out request could exhaust memory before the
   Analyzer even runs.

**What I would do about it:** Swap `history: list[StepResult]` for a
`dict[str, StepResult]` (O(1) lookup by `step_id`). This is a
single-field change in `core/models.py`. For the memory issue,
introduce incremental/rolling analysis — have the Analyzer summarize
retrieval batches as they complete rather than waiting to hold every
raw document in memory at once. This would require changing the
Orchestrator's retrieval loop to pipe results to the Analyzer
incrementally instead of collecting all results first.

---

## 2. Design Change in Hindsight: Replace Rule-Based Planner with an LLM

**The PlannerAgent's rule-based decomposition (`_split_into_subqueries`)
is the weakest link in the system.**

The current implementation uses regex splitting on conjunctions ("and",
"then", "also") and sentence punctuation. It works well for the kind of
requests this assignment expects ("Find X and Y, then summarize Z"), but
it will mis-segment more ambiguous natural language — requests with
nested clauses, implicit sub-tasks not joined by "and"/"then", or
questions that require inferring sub-steps that aren't explicitly listed.

**Why I chose the rule-based approach initially:** Zero external
dependencies, deterministic behavior, and no API keys needed for
testing/grading. An LLM-based planner would require an API key, add
latency, and introduce non-determinism that makes testing harder.

**What I'd change in hindsight:** The extension point is already
documented in `agents/planner_agent.py` (see the `EXTENSION POINT`
comment). Because every other agent depends only on Planner producing a
`list[Step]` — not on HOW it was produced — swapping in an LLM call
requires zero changes anywhere else in the system. This was a deliberate
design choice to make this improvement a single-file change.

---

## 3. Engineering Trade-Offs

### Trade-Off A: Linear Backoff Instead of Exponential Backoff with Jitter

**What we did:** `sleep(retry_backoff_seconds * attempts)` — a simple
linear increase (0.5s, 1.0s, 1.5s, ...).

**What a production system would more likely use:** Exponential backoff
with random jitter (`min(cap, base * 2^attempt + random(0, jitter))`).
This avoids the "thundering herd" problem where many clients retry in
lockstep against the same flaky service.

**Why we chose the simpler version anyway:** The assignment explicitly
values explainability over production completeness. Linear backoff is
computable in your head while reading log output — "attempt 2 waited
1.0s, attempt 3 waited 1.5s" — which matters when explaining this system
live in a review. Exponential backoff with jitter is a ~10-line change to
`_run_single_step()` if this system ever needed to survive a real flaky
upstream API. The cognitive load of explaining "why random jitter" during
a 30-minute review wasn't worth it for a system whose explicit goal is
"a beginner-to-intermediate developer can defend this."

**The cost:** In a real deployment with thousands of concurrent requests,
this could hammer a recovering service. But for a demo system with one
user at a time, it's invisible.

---

### Trade-Off B: Tolerating Partial Failures vs Failing Closed

**What we did:** If 2 out of 3 retrieval sub-queries succeed, Analyzer
proceeds with the 2 it has. Validator checks quality thresholds. Writer
produces a final answer that explicitly notes "1 source unavailable"
rather than failing the entire request.

**The alternative:** Treat ANY retrieval failure as fatal to the whole
pipeline (fail closed). This is simpler to implement (no partial-degradation
paths in Analyzer, no need for Validator) and arguably "safer" in domains
where partial information is actively misleading — medical diagnosis,
financial trading decisions, legal document analysis.

**Why we chose partial tolerance:** For a general-purpose research-assistant
task (the kind this assignment models), a slightly incomplete answer with
an honest confidence caveat is far more useful to a user than a hard
failure over one flaky sub-query out of several. The key design rule that
makes this safe: **the system never hides the fact that data is missing.**
Every degraded answer explicitly states which sources failed and what the
confidence level is. The user can make an informed decision about whether
to trust the output.

**The cost:** More code in every downstream agent (Analyzer must count
failures, Validator must check thresholds, Writer must have a degraded
branch). The alternative (fail closed) would eliminate ~40 lines of
defensive logic but make the system much less useful for research-style
queries.

---

### Trade-Off C: Single Mutable TaskContext vs Pure Functional Data Flow

**What we did:** A single `TaskContext` object is passed to every agent
by reference. Agents read from it; only the Orchestrator appends to it.

**The alternative:** Each agent receives only the data it needs as
function arguments and returns its output to the orchestrator, which
explicitly threads it to the next agent. This is a more functional,
testable pattern.

**Why we chose the shared context:** The Analyzer depends on a variably
sized set of upstream steps (as many retrievals as the Planner generated).
If data were threaded as function arguments, the Orchestrator's call to
`analyzer.execute()` would need a dynamic signature — or an intermediate
step to collect and merge results. The shared context avoids this: every
agent has the same signature, and each looks up whatever it needs by
step ID. This makes the Orchestrator's dispatch loop a simple `for step
in plan: run(step)` with zero conditional logic about what each step
needs.

**The cost:** Shared mutable state makes reasoning about correctness
harder (who mutated what and when?). We mitigate this with a convention
enforced by the Orchestrator: **agents only read from history; only the
Orchestrator appends.** But this is a convention, not a compiler-enforced
constraint — a future agent that accidentally writes to history could
introduce subtle bugs.
