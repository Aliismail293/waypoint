# A MALFORMED_FUNCTION_CALL / MALFORMED_RESPONSE turn becomes a silent, empty final response

**Draft, not filed.** google-adk 2.13.0, Gemini 3.5 Flash on Vertex AI.

## Summary

Sometimes Gemini ends a turn with `finish_reason=MALFORMED_FUNCTION_CALL` (or `MALFORMED_RESPONSE`) and no
content. ADK maps that to an `LlmResponse` with `error_code` set and `content=None`, and the runner yields it as
the agent's **final response**. There is no exception and no retry. Callers that read the final response's
text (the usual pattern) return an empty answer to the user.

## Repro

`repro_malformed_final.py` (offline) builds the response exactly as ADK's Gemini class does
(`LlmResponse.create(GenerateContentResponse(candidates=[Candidate(finish_reason=MALFORMED_FUNCTION_CALL)]))`):

```
$ python repro_malformed_final.py
final=True error_code='MALFORMED_FUNCTION_CALL' content=None text_parts=[]
```

**Expected:** one of these:
- An exception the caller can handle.
- An opt-in retry, since this finish reason is usually transient and an immediate retry of the same
  request usually succeeds.
- A final event that is clearly not a normal answer.

**Actual:** `is_final_response()` is `True` and nothing signals failure, except `error_code`, which most
examples never check.

## Impact

In live runs of an orchestrator agent, about 1 in 5 turns right after an `AgentTool` result ended this way
during one debugging session. Users saw blank answers.

## Workaround

A `Gemini` subclass that re-issues the request when the last response's `error_code` or `finish_reason` is
`MALFORMED_RESPONSE` / `MALFORMED_FUNCTION_CALL` (non-streaming, at most 2 retries).

## Possible fixes

- A retry option for retryable finish reasons, alongside `HttpRetryOptions`, which only covers HTTP status
  codes.
- Or have `is_final_response()` return `False` for events whose `error_code` is set and that have no
  content, or raise a typed error.
