# Plugins can't annotate the model-call span: after_model_callback runs after `generate_content` ends

**Draft, not filed.** google-adk 2.13.0, OpenTelemetry SDK 1.39.

## Summary

To attach per-call attributes (cost, cache hit rate, a custom label) to ADK's `generate_content` span, the
natural place is `after_model_callback`. By the time the callback runs, the `generate_content` span has
already ended. `trace.get_current_span()` returns the enclosing `invoke_agent` span, so the attributes land
on the agent span instead.

## Repro

`repro_after_model_span.py` (offline, in-memory span exporter):

```
$ python repro_after_model_span.py
current span in after_model_callback: 'invoke_agent root', is_recording=True
generate_content fake-model              has repro.cost_usd: False
invoke_agent root                        has repro.cost_usd: True
invoke_workflow root                     has repro.cost_usd: False
```

**Expected:** inside `after_model_callback`, the current span is the call's `generate_content` span, or the
callback context exposes that span.
**Actual:** the span has ended, and the attribute goes on `invoke_agent`.

## Impact

Per-model-call cost can't be put on the model-call span. An agent that calls the model several times has to
keep running totals on its `invoke_agent` span instead.

## Workaround

Keep per-span running totals (cost, tokens, call count) keyed by span id, and overwrite them on the
`invoke_agent` span after each call.

## Possible fixes

- Run `after_model_callback` while the `generate_content` span is still current.
- Or expose the span on `CallbackContext` (e.g. `callback_context.model_span`).
