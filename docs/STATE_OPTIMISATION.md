# Conversation-state optimisation: before/after

Measured live on 2026-10-28 with `evals/state_ab.py` (Gemini 3.5 Flash, Cloud SQL, BigQuery). There were two
conversations of six turns each (`evals/conversations.jsonl`), and each turn after the first is a follow-up
that depends on earlier turns. Raw per-turn data: `evals/results/state_ab.json`.

- **Before:** full, append-only history.
- **After (v1):** specialist outputs from earlier turns pruned to a stub, plus a running summary
  (ADK events compaction every 3 turns, keeping the latest turn). The summariser's own model calls are
  metered and included in the cost.

| Per variant (24 turns in total, 12 each) | Before | After (v1) | Change |
|---|---|---|---|
| Orchestrator input tokens per turn, turns 3–6 | 7,020 | 4,630 | **−34%** |
| All input tokens per turn, turns 3–6 (all agents) | 9,581 | 8,444 | −12% |
| Cost per conversation (incl. summariser) | $0.092 | $0.120 | **+31%** |
| P95 turn latency | 14.8 s | 17.4 s | +18% |
| Implicit prompt-cache hit rate | 25.9% | 11.3% | −14.6 pt |
| Follow-ups still resolved | 12/12 | 12/12 | same |

## What happened

The optimisation shrank the context it targeted, but it didn't save money:
1. **Pruning broke the implicit cache.** Gemini discounts a repeated prompt prefix automatically. With
   append-only history, conversation c2 got about 7,700 cached input tokens on each of turns 3–6. Pruning
   rewrites earlier turns on every request, so the prefix never repeats, and cached tokens fell to 0 on
   those turns.
2. **Summarising costs a model call** (2 in this run), and these conversations were too short to pay it
   back.
3. **History isn't where most tokens go.** Each specialist starts fresh, so its tokens don't depend on
   history, and specialists use most of each turn's tokens. Their usage varies a lot between runs (one
   turn used 20.8k input tokens and 6 calls). With 2 conversations per arm, that noise is as large as the
   effect.

## What changed because of it

- Pruning is **off** by default (`WAYPOINT_PRUNE_TOOL_OUTPUTS=false`). History stays append-only, so the
  implicit cache keeps working.
- Compaction now triggers on **prompt size** (orchestrator prompt ≥ 30k tokens, keeping the last 10 events)
  rather than every 3 turns. It is a guard for long conversations and doesn't touch typical ones.
- Explicit context caching (`ContextCacheConfig`) stays off. It creates a billed CachedContent resource and
  needs a 4096-token prefix on Gemini 3, which Waypoint's static prompts don't reach.

**The new defaults have not been measured live in their own A/B.** For conversations under the threshold
they behave like "before" (plus implicit caching), which this run measured. A cost-per-conversation cut is
**not** claimed; that line on the resume stays [TBD]. The next experiment would be longer conversations
(15+ turns), where history growth matters, with 10+ conversations per arm to average out specialist noise.
