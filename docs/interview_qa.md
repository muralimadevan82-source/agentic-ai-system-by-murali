# Interview Q&A — Defending This Project

Model answers written in first person, the way you'd actually say them in
a review. Read these, then practice saying them in your own words —
don't memorize verbatim, understand the reasoning so you can handle
follow-ups.

---

**Q1: Why didn't you use LangGraph or CrewAI? Wouldn't that have been faster?**

> It probably would have been faster to get SOMETHING running, yes. But
> the assignment specifically asks for a system I can fully explain, and
> frameworks like LangGraph hide exactly the mechanics being tested here
> — how steps get ordered, how retries work, how concurrency is bounded.
> If I'd used LangGraph and you asked me "how does your retry logic
> decide what to retry," my honest answer would be "the framework
> handles that." Here, my answer is `core/orchestrator.py`, line by line.
> That's the whole point of building it by hand.

---

**Q2: How does your system decide what counts as a "step"?**

> The PlannerAgent splits the raw request on conjunctions like "and",
> "then", "also", and sentence punctuation, then builds one Step per
> resulting sub-query, plus exactly one Analyzer step and one Writer
> step at the end. It's a rule-based/heuristic splitter, not an ML model
> — intentionally, so the whole pipeline runs deterministically with zero
> API keys for grading. I left a clearly marked extension point in the
> code showing exactly where you'd swap in a real LLM call to do smarter
> decomposition, without changing anything else in the system, because
> every other agent only depends on getting back a `list[Step]` — not on
> how that list was generated.

---

**Q3: Walk me through what happens if one of your API calls fails.**

> It depends on the TYPE of failure. I split errors into
> `TransientError` and `PermanentError`. If RetrieverAgent raises a
> TransientError — think a timeout or rate limit — the orchestrator
> retries it with a linear backoff, up to a configurable max. If it's a
> PermanentError — like an empty request that Planner can't decompose —
> we don't retry at all, because retrying won't fix bad input, it just
> wastes time. And if retries run out, the failure doesn't necessarily
> kill the whole pipeline — it depends which step failed. A failed
> retrieval just means the Analyzer has fewer sources to work with; the
> Analyzer is built to tolerate that. A failed Analyzer means the Writer
> falls back to an honest "I couldn't fully complete this" message
> instead of crashing.

---

**Q4: What's the difference between your "batching" and just using `asyncio.gather()` on everything?**

> `asyncio.gather()` on the whole list fires every job at once with no
> limit — fine for 3 items, unrealistic for 50, because a real API would
> rate-limit you or you'd just overwhelm your own machine. My
> `run_batched()` function in `utils/batching.py` does two things
> `gather()` alone doesn't: it splits the input into fixed-size chunks
> (`chunk_list`), and within each chunk it uses an `asyncio.Semaphore` to
> cap how many jobs run AT THE SAME TIME. So even within one batch,
> concurrency is bounded — that's the "manual batching" the assignment
> is asking for, written from scratch with the standard library, no
> queueing library involved.

---

**Q5: Why is `TaskContext` a single mutable object passed to every agent, instead of each agent returning data directly to the next one?**

> Because dependencies aren't always "the step right before me." My
> Analyzer step depends on potentially MANY retrieval steps at once —
> however many sub-queries the Planner generated for that specific
> request. If I tried to pass that as function arguments, the function
> signature would need to change based on how many retrieval steps
> exist, which varies per request. Instead, every agent gets the same
> `TaskContext`, and looks up whatever upstream results it actually needs
> by step ID via `context.get_result()`. It's a shared read/append log,
> not global mutable state in the dangerous sense — only the orchestrator
> appends to `history`; agents only read from it.

---

**Q6: Your Writer "degrades gracefully" — isn't that hiding a failure from the user?**

> No — it's the opposite of hiding it. If Analyzer fails, Writer
> explicitly tells the user "I was unable to fully complete this request
> because the analysis step failed," and includes the original request
> text so they know exactly what didn't get answered. The alternative —
> letting an unhandled exception propagate up and crash the CLI with a
> stack trace — gives the user LESS information, not more. Graceful
> degradation here means "fail loudly in the logs, but still hand the
> user something honest and actionable," not "silently pretend it
> worked."

---

**Q7: How would this scale to handle 100 retrieval steps instead of 3?**

> Two things would need to change, and I wrote about both of these
> honestly in my post-mortem instead of pretending the current design
> scales infinitely. First, `TaskContext.history` is currently a list
> with an O(n) lookup by step ID — at 100+ steps I'd switch that to a
> dict keyed by step_id for O(1) lookups. Second, right now the whole
> pipeline holds every retrieved document in memory until the Analyzer
> runs — at high fan-out, I'd want the Analyzer to summarize retrieval
> batches incrementally as they complete, rather than waiting to hold
> everything in memory at once. Neither of those breaks the current
> architecture, they're targeted upgrades to two specific pieces.

---

**Q8: Why is your retry backoff linear instead of exponential with jitter, which is what most production systems use?**

> Honestly, because explainability mattered more here than matching
> production best-practice exactly. Linear backoff — `base_seconds *
> attempt_number` — is something I can compute in my head while reading
> a log line during this exact conversation. Exponential backoff with
> jitter is the more correct choice for a real flaky upstream service at
> scale, because it avoids many clients retrying in lockstep — but it's
> maybe a 10-line change to `_run_single_step()` if this needed to handle
> a real, heavily-loaded external API. I documented this explicitly as a
> deliberate trade-off in my post-mortem rather than leaving it
> unexplained.

---

**Q9: What was the hardest design decision you made, and why?**

> Deciding what should ABORT the whole pipeline versus what should just
> degrade gracefully. My rule ended up being: if the FAILURE happens
> before there's any plan at all (Planner fails), there's nothing to
> recover into, so abort. Anything after that — a flaky retrieval, even
> a fully-failed analysis — degrades instead of crashing, because by
> that point the user has at least asked a well-formed question, and I'd
> rather give them a partial, honestly-labeled answer than nothing at
> all. That single rule is enforced in exactly one place,
> `core/orchestrator.py`, which is what makes it possible to explain
> consistently instead of having ad-hoc try/excepts scattered everywhere.

---

**Q10: How did you test this without a real LLM or real API calls?**

> RetrieverAgent simulates network calls with randomized latency
> (`asyncio.sleep`) and a tunable `simulated_failure_rate` that raises a
> TransientError some percentage of the time. That let me write
> deterministic tests for the EXACT failure scenarios I cared about — set
> the failure rate to 100% and assert the orchestrator degrades
> gracefully instead of crashing; set it to 0% and assert the happy path
> produces a final answer. I have 15 automated tests covering batching
> correctness, agent behavior in isolation, and full pipeline runs under
> both normal and failure conditions.
