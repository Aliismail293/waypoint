// Waypoint UI. Sign-in uses Google Identity Services: the button returns an ID token (JWT) to this page, which sends it
// to the API as a Bearer token. The token lives only in memory. Plain ES modules, no build step (see docs/DESIGN.md).
import { claimFor, escapeHtml, renderMarkdown } from "./js/citations.js";
import { pipSvg } from "./js/pip.js";
import { showCourse, wireCourse } from "./js/course-view.js";
import { invalidateMap, showMap, wireMap } from "./js/map-view.js";
import { invalidatePlans, showPlan, wirePlan } from "./js/plan-view.js";
import { showTimetable, wireTimetable } from "./js/timetable-view.js";
import { onProfileChange, openSettings, startOnboarding } from "./js/profile-view.js";
import { api, authHeaders, canAsk, errorFrom, postJson, session } from "./js/session.js";
import { initialStream, readEvents, reduce, stepsSummary } from "./js/stream.js";

const app = { sessionId: null, advisorUrl: "#", busy: false, turn: null };
const $ = (id) => document.getElementById(id);

const CHECK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0E7A43" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
const SPIN = '<svg class="spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#4338CA" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.2-8.6"/></svg>';
const THUMB_UP = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
const THUMB_DOWN = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg>';

// ---- Tabs (hash routes: #/chat, #/map[/CODE], #/course/CODE; plan, timetable and history are "coming soon") ----
const TABS = { chat: "Chat", map: "Course map", plan: "My plan", timetable: "Timetable", history: "History" };
const VIEWS = ["chat", "soon", "map", "course", "plan", "timetable"];

function route() {
  const [, tab = "chat", arg] = location.hash.match(/^#\/(\w+)(?:\/([A-Za-z0-9]+))?/) || [];
  const current = tab === "course" ? "course" : tab in TABS ? tab : "chat";
  const view = ["chat", "map", "course", "plan", "timetable"].includes(current) ? current : "soon";
  for (const v of VIEWS) $(`view-${v}`).hidden = v !== view;
  if (view !== "chat") closeReader();
  if (view === "soon") {
    $("soon-title").textContent = `${TABS[current]} is coming soon`;
    $("soon-pip").innerHTML = pipSvg("idle", 84);
  }
  if (view === "map" && canAsk()) showMap(arg ? arg.toUpperCase() : null);
  if (view === "course" && canAsk() && arg) showCourse(arg.toUpperCase());
  if (view === "plan" && canAsk()) showPlan();
  if (view === "timetable" && canAsk()) showTimetable();
  const active = current === "course" ? "map" : current; // course pages hang off the map tab
  document.querySelectorAll(".tabs a").forEach((a) => {
    if (a.dataset.tab === active) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  document.title = `${current === "course" && arg ? arg.toUpperCase() : TABS[current]} · Waypoint`;
}

// ---- Auth ----
async function signedIn(label) {
  $("signin").hidden = true;
  $("who").hidden = false;
  $("who").textContent = label.slice(0, 1).toUpperCase();
  $("who").title = `Signed in as ${label}. Open your profile`;
  $("message").placeholder = "Ask about programs, courses, prerequisites or a plan…";
  setBusy(false);
  route(); // a deep link (#/course/MAT224H5) can load now that the API is reachable
  await checkProfile();
}

function onSignIn(response) {
  session.idToken = response.credential;
  const payload = JSON.parse(atob(response.credential.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  signedIn(payload.given_name || payload.email || "You");
}

function setBusy(busy) {
  app.busy = busy;
  const off = busy || !canAsk();
  $("send").disabled = off;
  $("message").disabled = off;
  document.querySelectorAll(".example").forEach((b) => (b.disabled = off));
}

// "- **2026 Fall**: MAT224H5, STA256H5" lines in a planner answer -> terms for a new plan.
function planFromAnswer(text) {
  const terms = [];
  for (const m of text.matchAll(/\*\*(\d{4} (?:Fall|Winter|Summer))\*\*:?\s*([^\n]*)/g)) {
    const courses = m[2].match(/\b[A-Z]{3}\d{3}[HY]\d\b/g) || [];
    if (courses.length) terms.push({ term: m[1], courses });
  }
  return terms;
}

// ---- Chat turns ----
function scrollToEnd(el) {
  el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "end" });
}

function setPip(turn, pip) {
  if (turn.pip === pip) return;
  turn.pip = pip;
  turn.pipEl.innerHTML = pipSvg(pip, 84);
}

function numberOf(stream) {
  const map = new Map(stream.sources.map((s) => [s.chunk_id, s.n]));
  let next = stream.sources.length;
  for (const c of stream.final?.citations || []) if (!map.has(c.chunk_id)) map.set(c.chunk_id, ++next);
  return map;
}

function stepIcon(state) {
  if (state === "done") return CHECK;
  return state === "running" ? SPIN : "";
}

function stepsHtml(steps) {
  return steps
    .map((s) => {
      const secs = s.ms ? ` · ${(s.ms / 1000).toFixed(1)} s` : "";
      return `<li class="${escapeHtml(s.state)}">${stepIcon(s.state)}${escapeHtml(s.label)}${secs}</li>`;
    })
    .join("");
}

function newTurn(message) {
  const user = document.createElement("div");
  user.className = "turn-user";
  user.textContent = message;
  const row = document.createElement("div");
  row.className = "turn-pip";
  row.innerHTML = `<div class="pip-slot">${pipSvg("thinking", 84)}</div><div class="card glass"><div class="working-title">Pip is working on it</div><ol class="steps" aria-label="Steps"></ol><div class="answer"></div></div>`;
  $("empty").hidden = true;
  $("log").append(user, row);
  const turn = {
    message,
    stream: initialStream(),
    pip: "thinking",
    row,
    pipEl: row.querySelector(".pip-slot"),
    card: row.querySelector(".card"),
    stepsEl: row.querySelector(".steps"),
    answerEl: row.querySelector(".answer"),
  };
  turns.set(row, turn);
  scrollToEnd(row);
  return turn;
}

function renderTurn(turn) {
  const { stream } = turn;
  setPip(turn, stream.pip === "idle" && !stream.final && !stream.error ? "thinking" : stream.pip);
  turn.stepsEl.innerHTML = stepsHtml(stream.steps);
  if (stream.sources.length) showSources(turn, stream.sources);
  if (stream.text) turn.answerEl.innerHTML = renderMarkdown(stream.text, numberOf(stream));
}

function finishTurn(turn) {
  const { stream, card } = turn;
  const numbers = numberOf(stream);
  card.querySelector(".working-title")?.remove();
  turn.stepsEl.remove();
  turn.answerEl.innerHTML = renderMarkdown(stream.text || "(no answer)", numbers);
  const summary = document.createElement("button");
  summary.type = "button";
  summary.className = "steps-toggle";
  summary.setAttribute("aria-expanded", "false");
  summary.innerHTML = `${CHECK}${escapeHtml(stepsSummary(stream))} · show how Pip got this`;
  const list = document.createElement("ol");
  list.className = "steps steps-list";
  list.hidden = true;
  list.innerHTML = stepsHtml(stream.steps);
  summary.addEventListener("click", () => {
    list.hidden = !list.hidden;
    summary.setAttribute("aria-expanded", String(!list.hidden));
  });
  card.prepend(summary, list);
  card.append(actionsFor(turn));
  const metrics = stream.final?.metrics;
  if (metrics) {
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${(metrics.total_ms / 1000).toFixed(1)} s · ${metrics.model_calls} model calls`;
    card.append(meta);
  }
  setPip(turn, "idle");
}

function actionsFor(turn) {
  const ids = { session_id: turn.stream.final?.session_id || "", answer_id: turn.stream.final?.answer_id || "" };
  const bar = document.createElement("div");
  bar.className = "actions";
  const codes = [...new Set(turn.stream.text.match(/\b[A-Z]{3}\d{3}[HY]\d\b/g) || [])];
  const mapBtn = codes.length ? `<a class="btn primary" href="#/map/${escapeHtml(codes[0])}">See it on the course map</a>` : "";
  const planBtn = codes.length ? `<button type="button" class="btn" data-add-plan="${escapeHtml(codes[0])}">Add ${escapeHtml(codes[0])} to my plan</button>` : "";
  const draft = planFromAnswer(turn.stream.text);
  const draftBtn = draft.length ? `<button type="button" class="btn" data-save-plan>Save as a plan</button>` : "";
  bar.innerHTML =
    mapBtn + planBtn + draftBtn +
    `<a class="btn" id="advisor-${ids.answer_id}" href="${escapeHtml(app.advisorUrl)}" target="_blank" rel="noopener">Ask an advisor</a>` +
    `<span class="feedback"><button type="button" data-rating="1" aria-label="Good answer" aria-pressed="false">${THUMB_UP}</button>` +
    `<button type="button" data-rating="-1" aria-label="Bad answer" aria-pressed="false">${THUMB_DOWN}</button></span>`;
  const say = (html) => {
    let line = bar.parentElement.querySelector(".plan-note");
    if (!line) {
      line = document.createElement("div");
      line.className = "meta plan-note";
      line.setAttribute("role", "status");
      bar.after(line);
    }
    line.innerHTML = html;
  };
  const planLink = (plan) => `<a href="#/plan">View ${escapeHtml(plan.name)}</a>`;
  bar.querySelector("[data-add-plan]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget; // currentTarget is cleared once the handler awaits
    button.disabled = true;
    try {
      const plan = await api("POST", "/api/plans/add-course", { code: button.dataset.addPlan });
      invalidatePlans();
      const term = plan.terms.find((t) => t.courses.includes(button.dataset.addPlan))?.term;
      say(`Added to ${escapeHtml(plan.name)}${term ? ` · ${escapeHtml(term)}` : ""}. ${planLink(plan)}`);
    } catch (err) {
      button.disabled = false;
      say(escapeHtml(err.message));
    }
  });
  bar.querySelector("[data-save-plan]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const plan = await api("POST", "/api/plans", { name: "Plan from chat", terms: draft });
      invalidatePlans();
      say(`Saved as ${escapeHtml(plan.name)}. ${planLink(plan)}`);
    } catch (err) {
      button.disabled = false;
      say(escapeHtml(err.message));
    }
  });
  bar.querySelectorAll(".feedback button").forEach((button) =>
    button.addEventListener("click", () => {
      bar.querySelectorAll(".feedback button").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
      postJson("/api/feedback", { ...ids, rating: Number(button.dataset.rating) }).catch(() => {});
    }),
  );
  // The link opens normally; the escalation is recorded alongside (it must not block the navigation).
  bar.querySelector(`#advisor-${ids.answer_id}`).addEventListener("click", () => {
    postJson("/api/escalate", ids).catch(() => {});
  });
  return bar;
}

async function ask(message) {
  if (app.busy || !canAsk()) return;
  const turn = newTurn(message);
  app.turn = turn;
  setBusy(true);
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ message, session_id: app.sessionId }),
    });
    if (!response.ok) throw await errorFrom(response);
    await readEvents(response, (name, data) => {
      turn.stream = reduce(turn.stream, name, data);
      if (name === "error") throw new Error(data.message);
      renderTurn(turn);
    });
    if (!turn.stream.final) throw new Error("The answer stream ended early. Please try again.");
    app.sessionId = turn.stream.final.session_id;
    turn.stream.sources = mergeSources(turn.stream);
    showSources(turn, turn.stream.sources);
    finishTurn(turn);
  } catch (err) {
    turn.card.className = "card glass error";
    turn.card.textContent = err.message;
    setPip(turn, "idle");
  } finally {
    setBusy(false);
    scrollToEnd(turn.row);
  }
}

// The sources event lists everything retrieved; the final event may cite a chunk that event did not include.
function mergeSources(stream) {
  const known = new Set(stream.sources.map((s) => s.chunk_id));
  const extra = (stream.final?.citations || [])
    .filter((c) => !known.has(c.chunk_id))
    .map((c, i) => ({ n: stream.sources.length + i + 1, code: "", title: c.heading.split(" > ").pop(), chunk_id: c.chunk_id, heading: c.heading, url: c.url }));
  return [...stream.sources, ...extra];
}

// ---- Sources panel ----
let shownAll = false;

function showSources(turn, sources) {
  app.turn = turn;
  const rows = shownAll ? sources : sources.slice(0, 3);
  $("sources-none").hidden = sources.length > 0;
  $("sources-count").textContent = sources.length ? `${sources.length} · Academic Calendar` : "";
  $("sources-list").innerHTML = rows
    .map(
      (s) =>
        `<button type="button" class="source-row" data-source="${s.n}"><span class="n">${s.n}</span>` +
        `<span class="t"><strong>${escapeHtml(s.code || s.title)}</strong>${s.code ? `<small>${escapeHtml(s.title)}</small>` : ""}</span></button>`,
    )
    .join("");
  const more = $("sources-more");
  more.hidden = sources.length <= 3;
  more.textContent = shownAll ? "Show top 3" : `+${sources.length - 3} more sources`;
  more.setAttribute("aria-expanded", String(shownAll));
  $("sources-list").querySelectorAll(".source-row").forEach((el) => el.classList.toggle("active", reader.open && Number(el.dataset.source) === reader.n));
}

$("sources-more").addEventListener("click", () => {
  shownAll = !shownAll;
  if (app.turn) showSources(app.turn, app.turn.stream.sources);
});

// ---- Source reader ----
const reader = { open: false, n: 0, turn: null, detail: null, trigger: null };

async function openReader(turn, n, trigger) {
  const sources = turn.stream.sources;
  const source = sources.find((s) => s.n === n);
  if (!source) return;
  reader.open = true;
  reader.n = n;
  reader.turn = turn;
  reader.trigger = trigger || reader.trigger;
  const panel = $("reader");
  panel.hidden = false;
  $("reader-pos").textContent = `Source ${sources.indexOf(source) + 1} of ${sources.length}`;
  $("reader-prev").disabled = sources.indexOf(source) === 0;
  $("reader-next").disabled = sources.indexOf(source) === sources.length - 1;
  $("reader-title").textContent = source.code ? `${source.code} · ${source.title}` : source.title;
  $("reader-crumb").textContent = ["Academic Calendar", ...source.heading.split(" > ")].join(" / ");
  $("reader-text").textContent = "Loading the calendar text…";
  $("reader-why").textContent = "";
  $("reader-thanks").hidden = true;
  setOfficial(source.url);
  if (!turn.plan) showSources(turn, sources);
  panel.querySelector("#reader-close").focus();
  try {
    const claim = claimFor(turn.stream.text, source.chunk_id);
    const response = await postJson("/api/source", { chunk_id: source.chunk_id, claim });
    if (!response.ok) throw await errorFrom(response);
    const detail = await response.json();
    if (reader.n !== n || !reader.open) return; // the student moved on while this loaded
    reader.detail = detail;
    renderReaderText(detail);
    $("reader-why").textContent = detail.why;
    $("reader-crumb").textContent = detail.breadcrumb.join(" / ");
    setOfficial(detail.url);
  } catch (err) {
    if (reader.n !== n) return;
    $("reader-text").textContent = `Not in Waypoint's data yet. ${err.message}`;
  }
}

function setOfficial(url) {
  const link = $("reader-official");
  const ok = typeof url === "string" && url.startsWith("https://");
  link.hidden = !ok;
  if (ok) link.href = url;
}

function renderReaderText(detail) {
  const el = $("reader-text");
  el.textContent = "";
  const h = detail.highlight;
  if (!h) {
    el.textContent = detail.text;
    return;
  }
  const mark = document.createElement("mark");
  mark.textContent = detail.text.slice(h.start, h.end);
  el.append(detail.text.slice(0, h.start), mark, detail.text.slice(h.end));
}

function closeReader() {
  if (!reader.open) return;
  reader.open = false;
  $("reader").hidden = true;
  document.querySelectorAll(".source-row.active, .cite.active").forEach((el) => el.classList.remove("active"));
  reader.trigger?.focus?.();
}

function step(delta) {
  const sources = reader.turn.stream.sources;
  const i = sources.findIndex((s) => s.n === reader.n) + delta;
  if (i >= 0 && i < sources.length) openReader(reader.turn, sources[i].n);
}

$("reader-close").addEventListener("click", closeReader);
$("reader-prev").addEventListener("click", () => step(-1));
$("reader-next").addEventListener("click", () => step(1));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeReader();
});
$("reader-mismatch").addEventListener("click", async () => {
  const final = reader.turn.stream.final || {};
  const source = reader.turn.stream.sources.find((s) => s.n === reader.n);
  await postJson("/api/source-feedback", { chunk_id: source.chunk_id, session_id: final.session_id || "", answer_id: final.answer_id || "" }).catch(() => {});
  $("reader-thanks").hidden = false;
});

// Citation badges inside answers and rows in the Sources panel both open the reader.
document.addEventListener("click", (e) => {
  const badge = e.target.closest(".cite, .source-row");
  if (!badge) return;
  const turn = badge.closest(".turn-pip") ? turnFor(badge) : app.turn;
  if (turn) openReader(turn, Number(badge.dataset.source), badge);
});
// The critic report on the plan page opens its citations in the same Source reader.
window.addEventListener("waypoint:open-source", (e) => {
  const { sources, chunkId } = e.detail;
  const turn = { plan: true, stream: { sources, text: "", final: {} } };
  const source = sources.find((s) => s.chunk_id === chunkId);
  if (source) openReader(turn, source.n);
});
const turns = new WeakMap();
function turnFor(el) {
  return turns.get(el.closest(".turn-pip")) || app.turn;
}

// ---- Boot ----
// First sign-in: no profile yet, so offer onboarding (skippable). The map and course pages read the profile.
async function checkProfile() {
  try {
    const profile = await api("GET", "/api/profile");
    if (!profile.onboarded) await startOnboarding();
  } catch {
    // the profile is an extra: chat works without it
  }
}

onProfileChange(() => {
  invalidateMap();
  invalidatePlans();
});
$("who").addEventListener("click", () => openSettings());
window.addEventListener("waypoint:ask", (e) => {
  if (location.hash !== "#/chat") location.hash = "#/chat";
  ask(e.detail);
});

async function init() {
  $("empty-pip").innerHTML = pipSvg("idle", 84);
  const config = await (await fetch("/api/config")).json();
  app.advisorUrl = config.advisor_url;
  $("advisor-link").href = config.advisor_url;
  if (config.dev_mode) {
    session.devMode = true;
    signedIn("Dev");
    return;
  }
  if (!config.google_client_id) {
    $("signin").textContent = "Sign-in isn't configured on this server.";
    return;
  }
  const start = () => {
    google.accounts.id.initialize({ client_id: config.google_client_id, callback: onSignIn });
    google.accounts.id.renderButton($("signin"), { theme: "outline", size: "large", text: "signin_with" });
  };
  if (window.google?.accounts?.id) start();
  else window.addEventListener("load", start);
}

$("ask").addEventListener("submit", (e) => {
  e.preventDefault();
  const message = $("message").value.trim();
  if (!message) return;
  $("message").value = "";
  ask(message);
});
$("message").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("ask").requestSubmit();
  }
});
document.querySelectorAll(".example").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));
window.addEventListener("hashchange", route);
wireMap();
wireCourse();
wirePlan();
wireTimetable();
route();
init();
