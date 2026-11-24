// Server-Sent Events from POST /api/chat/stream (DESIGN_SPEC section 6) and the reducer that turns them into UI state.
// Pip is `thinking` from the first status event until the first token, `talking` until the answer is final, then `idle`.
// A `plan` event carries a plan built and checked in code: {terms: [{term, courses}], valid, leftover, ...}.

export function initialStream() {
  return { steps: [], sources: [], text: "", pip: "idle", started: false, final: null, error: null, done: null, plan: null };
}

// Parses as many complete events as `buffer` holds. Returns the events and the unparsed remainder.
export function parseSSE(buffer) {
  const events = [];
  let rest = buffer;
  let end;
  while ((end = rest.indexOf("\n\n")) >= 0) {
    const block = rest.slice(0, end);
    rest = rest.slice(end + 2);
    const name = (block.match(/^event: (.*)$/m) || [])[1];
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data: "))
      .map((line) => line.slice(6))
      .join("\n");
    if (name && data) events.push({ name, data: JSON.parse(data) });
  }
  return { events, rest };
}

export function reduce(state, name, data) {
  switch (name) {
    case "status": {
      const key = data.id || data.step;
      const steps = state.steps.slice();
      const i = steps.findIndex((s) => s.key === key);
      const step = { key, step: data.step, label: data.label, state: data.state, ms: data.ms ?? null };
      if (i >= 0) steps[i] = step;
      else steps.push(step);
      return { ...state, steps, started: true, pip: state.text ? "talking" : "thinking" };
    }
    case "sources":
      return { ...state, sources: data };
    case "plan": // the fast planner's checked plan, before any answer text
      return { ...state, plan: data, started: true };
    case "token":
      return { ...state, text: state.text + data.text, started: true, pip: "talking" };
    case "final":
      return { ...state, final: data, text: data.answer ?? state.text, pip: "idle" };
    case "done":
      return { ...state, done: data, pip: "idle" };
    case "error":
      return { ...state, error: data.message, pip: "idle" };
    default:
      return state;
  }
}

// "N steps · X s" for the collapsed toggle on a finished answer.
export function stepsSummary(state) {
  const seconds = state.done ? state.done.total_ms / 1000 : (state.final?.metrics?.total_ms ?? 0) / 1000;
  const n = state.steps.filter((s) => s.state === "done").length || state.steps.length;
  return `${n} step${n === 1 ? "" : "s"} · ${seconds.toFixed(1)} s`;
}

// Where the answer stream comes from. Firebase Hosting buffers Server-Sent Events, so a page served from one of the
// server's CORS origins (the web.app site) streams straight from Cloud Run; everywhere else it is the same origin.
export function streamBase(config, origin) {
  return config.stream_url && (config.stream_origins || []).includes(origin) ? config.stream_url : "";
}

export async function readEvents(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSSE(buffer);
    buffer = parsed.rest;
    for (const event of parsed.events) onEvent(event.name, event.data);
  }
}
