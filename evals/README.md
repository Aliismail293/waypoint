# evals

Every number here comes from a run whose raw output is committed in `evals/results/`.

| What | Script | Result file |
|---|---|---|
| Golden-set eval (answer accuracy, recall@5, P95, $/query) | `run_eval.py evaluate` | `results/eval_v0.json`, `.queries.jsonl` |
| Plan validity with and without the critic | `plan_validity.py` | `results/critic_ab.json` |
| Safety suite, live model | `safety_suite.py --live` | `results/safety_live.json` |
| Conversation state before/after | `state_ab.py` | `results/state_ab.json` (see `docs/STATE_OPTIMISATION.md`) |
| CI gate (offline, every PR) | `ci_gate.py` | GitHub Actions summary; baseline in `ci_baseline.json` |

## Golden set and eval_v0

ragaudit generated questions from the calendar corpus with Gemini 3.5 Flash-Lite. A different model, Gemini
3.5 Flash, verified them, and 62 of 127 candidates were rejected (ambiguous, too lexical, unanswerable, ...).
That left 65 questions, split dev 44 / test 21 by chunk, so no test question shares a source chunk with a dev
question. Waypoint is scored end to end on the test split. The judge is Gemini 3.5 Flash.

| Metric (test split, n=21, k=5) | eval_v0 |
|---|---|
| Answer accuracy | 66.7% |
| Retrieval hit rate (recall@5) | 81.0% |
| MRR | 0.750 |
| P50 / P95 latency | 6.0 s / 16.0 s |
| Cost per query | $0.0112 |

One question failed with a Vertex AI 429 and counts as a miss. Gemini sometimes wraps its JSON in a code fence,
and ragaudit scores unparseable judge output as incorrect. Without stripping the fence, the same answers
scored 28.6%, because 14 of 21 judgements were unparseable. `run_eval.py` strips the fence before parsing.

## Critic (self-reflection) before/after

There are 6 planning requests (`planning_questions.jsonl`). Each is run with the critic off (the Phase 1
planner) and on (draft → critique → at most 2 revisions). Validity is judged by code, not by a model: the plan
lines in the final answer are extracted and run through `validate_plan_text`.

| Planning requests (n=6, live, 2026-10-28) | Critic off | Critic on |
|---|---|---|
| Valid plans | 2/6 (33%) | 4/6 (67%) |
| Mean latency | 37.2 s | 54.7 s |
| Mean model calls | 10.8 | 15.7 |
| Mean cost per plan | $0.071 | $0.102 |

The sample is small (6 requests per arm), so this is evidence, not proof. Every invalid plan with the
critic on cites courses Waypoint has no data for: STA, CSC and ISP courses that the programs require or
that the planner added. A reviewer can't make those valid, and the answer then carries a "check with an
advisor" note. The fix is wider data coverage, not more critique.

## Safety suite

| Category | Cases | Offline guard checks (CI) | Live model checks (2026-10-28) |
|---|---|---|---|
| Prompt injection hidden in a retrieved passage | 2 | pass | pass |
| PII echo (student number, email, phone) | 2 | pass | pass |
| Out of scope (essay, homework, pizza) | 3 | 2 pass (pizza is live-only) | pass |
| Hallucinated course codes | 2 | pass | pass |
| **Total** | **9** | **8/8** | **9/9** ($0.04) |

Each injection case checks that the poisoned passage was actually retrieved, so a pass means the attack
reached the agent and was ignored. The poisoned passages are synthetic test data, in
`safety/poisoned_chunks.jsonl`, and live only in an in-memory copy of the corpus.
