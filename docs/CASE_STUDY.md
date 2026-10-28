# Case study: Waypoint, a multi-agent degree-planning copilot

*Status, 2026-10-28: built and evaluated, not yet launched. Every number below comes from a committed eval
run, and anything that needs real users is marked [TBD].*

## Problem

UTM students plan their degrees from a long Academic Calendar, a separate timetable, and advising
appointments that fill up. The usual questions are:
- What do I still need for my major?
- Can I take MAT301 next term?
- Which courses fit Tuesday/Thursday afternoons?

Each one is a few lookups and a prerequisite check. Done by hand, they're easy to get wrong, and a wrong
answer costs a term.

## Discovery

[TBD] The discovery session (stakeholder, user interviews, success metrics) hasn't happened. This build
started from my own experience of the problem and the public calendar. The risks I designed for came from
that, not from interviews:
- advice that sounds right but isn't,
- personal data,
- cost.

## What I built

An orchestrator agent on Google ADK delegates to four specialists and composes one cited answer.

| Specialist | How it gets its facts |
|---|---|
| Policy | Hybrid retrieval over the calendar: pgvector HNSW plus Postgres full-text search, fused with reciprocal rank fusion |
| Course data | Five allow-listed, parameterised BigQuery queries. The model never writes SQL. The same allow-list generates an MCP Toolbox config |
| Timetable | A custom MCP server around a legacy adapter (retries, TTL cache, schema normalisation), running on labelled sample data |
| Planner | A ReAct drafter plus a critic, with at most 2 revisions. A plan that fails deterministic validation can't be approved |

Around the agents, code (not prompts) enforces:
- a request-wide model-call budget,
- citations that must point at chunks retrieved in the same request,
- flags on course codes no tool returned,
- PII redaction in both directions,
- an out-of-scope pre-filter.

Waypoint itself is also an MCP server. OpenTelemetry sends tokens, cost and latency per agent to Cloud Trace
and Langfuse. Terraform describes the whole footprint, but it has only been planned, never applied.

## Evals

| What | Result | Source |
|---|---|---|
| Answer accuracy, held-out test split (n=21) | 66.7% | `evals/results/eval_v0.json` |
| Retrieval hit rate (recall@5) / MRR | 81.0% / 0.750 | same |
| P50 / P95 latency | 6.0 s / 16.0 s | same |
| Cost per question | $0.0112 | same |
| Valid plans, critic off → on (n=6 each) | 2/6 → 4/6 | `evals/results/critic_ab.json` |
| Safety suite (injection, PII, out of scope, fake codes) | 9/9 live, 8/8 offline in CI | `evals/results/safety_live.json` |

A ragaudit golden set (65 questions) is split dev/test by chunk. The test split is never used for tuning.

## What didn't work, and what I learned

- **The first eval scored 28.6%, and it was wrong.** The Gemini judge wrapped its JSON in code fences 14
  times out of 21, and the eval harness counted each of those as a failure. Stripping the fence gave 66.7%
  with no other change. I read the judge's raw output before trusting the score.
- **A safety test passed by accident.** My first prompt-injection case routed to the course database, so
  the poisoned passage never reached the agent. Now every injection case also asserts that the poison was
  retrieved.
- **State optimisation made things worse.** Pruning old tool outputs cut the orchestrator's context by
  34%, but cost per conversation rose 31%. Rewriting history broke Gemini's implicit prefix cache. I
  published the negative result and changed the defaults to follow it
  (`docs/STATE_OPTIMISATION.md`).
- **ADK's call cap doesn't cover sub-agents.** `max_llm_calls=3` allowed 9 calls across three
  delegations. One real question once made 87 model calls. Waypoint enforces its own request-wide budget,
  and the repro is in `docs/upstream-issues/`.
- **The critic helps, at a price.** Valid plans doubled (2/6 → 4/6). Latency rose 47% and cost 44%. The
  plans that are still invalid need courses outside the data, which a critic can't fix.

## Does the pattern repeat?

I extracted the reusable parts into **fde-kit**, a local package that is publish-ready but not on PyPI. It
contains:
- the request-wide budget and metrics plugin,
- the cost table,
- the legacy-adapter and MCP template,
- the ragaudit bridge,
- a scripted test model.

Then I built a second, clearly labelled **demo** in a different domain with it: Parks Canada visitor
numbers from open government data. Its real-world mess included mixed file encodings, numbers stored as
text, and status codes where numbers should be. It went from choosing the dataset to 6/6 on a live eval in
6.5 minutes of agent time. That isn't a claim about human setup time.

## Launch

Deployed on Cloud Run, scaling to zero: <https://waypoint-48349810745.us-central1.run.app>. On the first
live check, the 3 demo questions were answered correctly in 6.5–11.1 s. Two answers cited the calendar,
and the timetable answer was labelled as sample data. Not launched to students yet. Thumbs up/down, an
"ask an advisor" link and usage tracking are built and tested, so the launch numbers will be real. Users: [TBD]. Questions answered without escalation: [TBD].
Thumbs-up rate: [TBD].

## What I'd do next

1. Run discovery with an advisor and a few students before launch, and let it reorder this list.
2. Widen data coverage (STA, CSC and the other departments), which is the main cause of invalid plans.
3. Launch to a small group and report the usage numbers.
4. Re-measure state optimisation on longer conversations (15+ turns, 10+ conversations per arm).
5. Publish fde-kit and file the three upstream issues after re-checking them on the latest ADK.
