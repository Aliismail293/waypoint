# RunConfig.max_llm_calls doesn't count model calls made by AgentTool children

**Draft, not filed.** google-adk 2.13.0, Python 3.12.

## Summary

`RunConfig.max_llm_calls` is documented as a limit on LLM calls for a run. When the root agent delegates
through `AgentTool`, each child run gets its own counter with the same limit, and the child's calls are never
added to the parent's count. So the cap doesn't bound the cost of a run: a root that delegates *N* times can
make roughly (*N* + 1) × `max_llm_calls` model calls.

## Repro

`repro_max_llm_calls.py` (offline, scripted model): the root delegates to a child agent 3 times, and each child
run makes 2 model calls.

```
$ python repro_max_llm_calls.py
LlmCallsLimitExceededError: Max number of llm calls limit of `3` exceeded
max_llm_calls=3 -> LlmCallsLimitExceededError raised; model calls made: 9
```

**Expected:** the error is raised before a 4th model call is made, anywhere in the run.
**Actual:** 9 model calls are made: 3 from the root and 6 inside the children. The error comes only on the
root's own 4th call. A single child run that goes over the cap is stopped, so the limit works per
invocation, not per run.

## Impact

In a production multi-agent app (an orchestrator with 4 `AgentTool` specialists), one user question made 87
model calls (about 186k input tokens) with `max_llm_calls=20`. The orchestrator re-delegated to the same
specialists several times, and each delegation had a fresh budget of 20.

## Workaround

A plugin that counts every model call in the request (plugins see child agents too, because AgentTool
passes them on) and short-circuits in `before_model_callback` once over budget. The count is held in a
`ContextVar` set per request.

## Possible fixes

- Share the parent's `_invocation_cost_manager` (or a run-level counter) with the child invocation that
  `AgentTool` creates, so `max_llm_calls` bounds the whole run.
- Or document the per-invocation semantics clearly and add a run-level option, e.g.
  `RunConfig(max_llm_calls_total=...)`.
