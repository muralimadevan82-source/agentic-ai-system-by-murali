# Sample Requests & Expected Outputs

These are real runs of the system (not fabricated transcripts) — you can
reproduce any of them with `python3 main.py "<request>"`. Because
RetrieverAgent has a randomized simulated failure rate (default 20%) and
randomized confidence scores, exact numbers will vary slightly between
runs, but the STRUCTURE of the output and the plan shape will not.

---

## Sample 1: Three-part research-and-recommend request

**Input:**
```
Find the top 3 AI startups in Bangalore and compare their funding and then summarize which is the best bet for a fresher
```

**Plan produced by PlannerAgent:**
```
retrieve_1: "Find the top 3 AI startups in Bangalore"
retrieve_2: "compare their funding"
retrieve_3: "summarize which is the best bet for a fresher"
analyze_1:  depends on all 3 retrieval steps
write_1:    depends on analyze_1
```

**Expected behavior:**
- All 3 retrieval steps run concurrently in ONE batch (batch size 3).
- ~20% chance any individual retrieval step hits a simulated transient
  failure and gets automatically retried (visible as a 🔁 event).
- Final answer lists one bullet point per sub-query plus an aggregate
  confidence percentage.

**Sample final output:**
```
Here is what I found for: "Find the top 3 AI startups in Bangalore and
compare their funding and then summarize which is the best bet for a fresher"

  • On 'Find the top 3 AI startups in Bangalore': Relevant information found...
  • On 'compare their funding': Relevant information found...
  • On 'summarize which is the best bet for a fresher': Relevant information found...

(Confidence: 83% avg across 3 source(s))
```

---

## Sample 2: Comparison + recommendation request

**Input:**
```
Find pricing for AWS Lambda and compare it with Google Cloud Functions then recommend the cheaper option for a low-traffic app
```

**Plan produced:** 3 retrieval steps (pricing lookup, comparison,
recommendation), 1 analysis step, 1 writer step — same shape as Sample 1,
demonstrating the Planner generalizes across domains without any
domain-specific code.

---

## Sample 3: Single-part request (no "and"/"then")

**Input:**
```
Summarize the latest trends in generative AI
```

**Plan produced:**
```
retrieve_1: "Summarize the latest trends in generative AI"   (only ONE sub-query — fallback path)
analyze_1:  depends on retrieve_1
write_1:    depends on analyze_1
```

**Why this matters:** confirms the Planner's fallback path — when there's
nothing to split on, it doesn't error out or produce zero steps, it
treats the whole request as a single sub-query.

---

## Sample 4: Forced total-retrieval-failure (failure handling demo)

**How to reproduce:**
```python
from core.orchestrator import Orchestrator
orch = Orchestrator(max_retries=1)
orch.agents["retriever"].simulated_failure_rate = 1.0  # force every retrieval to fail
```

**Expected behavior:**
1. Retrieval step retries once, then is marked `FAILED`.
2. Pipeline does NOT abort — it proceeds to `analyze_1`.
3. `AnalyzerAgent` raises `StepValidationError` ("nothing to analyze").
4. `WriterAgent` detects the failed analysis and returns a degraded,
   clearly-labeled message instead of crashing:

```
⚠️ I was unable to fully complete this request because the analysis
step failed. Here is what I can tell you:
- Original request: Find something and summarize it
- No verified information could be synthesized at this time. Please
  try again, or narrow the request.
```

---

## Sample 5: Empty/unplannable request (pipeline-abort demo)

**Input:**
```
python3 main.py ""
```
(or any whitespace-only string)

**Expected behavior:** Planner raises `PermanentError`, and the
ORCHESTRATOR — not an individual agent — emits a single
`PIPELINE_ABORTED` event and stops immediately. No retrieval, analysis,
or writing is attempted, because there is no plan to execute steps from.

```
🛑 Pipeline aborted: planning failed permanently (Cannot plan for an empty request.)
```
