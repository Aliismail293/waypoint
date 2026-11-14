// My plan: terms of course cards, credits progress, and the critic report. Every change is saved and re-checked by
// the server (plain code, not a model); the page debounces rapid edits (drags, adds) into one save.
import { escapeHtml } from "./citations.js";
import { pipSvg } from "./pip.js";
import { api, getJson } from "./session.js";

const el = (id) => document.getElementById(id);
export const planState = { plans: [], id: null, loaded: false };
let saveTimer = null;
let saving = false;
let note = "";

const ICON = {
  ok: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0E7A43" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
  warn: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#B45309" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 8v5M12 17h.01"/><circle cx="12" cy="12" r="10"/></svg>',
  error: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#9B1C1C" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 8v5M12 17h.01"/><circle cx="12" cy="12" r="10"/></svg>',
};
const STATUS = { ok: ["Critic check passed", "ok"], notes: ["Critic has notes", "warn"], errors: ["Critic found problems", "bad"], empty: ["No courses yet", "plain"] };

export const activePlan = () => planState.plans.find((p) => p.id === planState.id) || planState.plans[0] || null;

export function applyPlan(plan) {
  const i = planState.plans.findIndex((p) => p.id === plan.id);
  if (i >= 0) planState.plans[i] = plan;
  else planState.plans.push(plan);
  planState.id = plan.id;
}

export async function loadPlans(force = false) {
  if (planState.loaded && !force) return;
  planState.plans = (await getJson("/api/plans")).plans;
  planState.loaded = true;
  if (!planState.plans.some((p) => p.id === planState.id)) planState.id = planState.plans[0]?.id || null;
}

export const invalidatePlans = () => {
  planState.loaded = false;
};

// ---- saving ----
function termsPayload(plan) {
  return plan.terms.map((t) => ({ term: t.term, courses: t.courses }));
}

async function doSave() {
  saveTimer = null;
  const plan = activePlan();
  try {
    const saved = await api("PUT", `/api/plans/${plan.id}`, { name: plan.name, terms: termsPayload(plan) });
    applyPlan(saved);
    note = "";
  } catch (err) {
    note = err.message;
    await loadPlans(true).catch(() => {});
  }
  saving = false;
  draw();
}

function scheduleSave() {
  clearTimeout(saveTimer);
  saving = true;
  draw();
  saveTimer = setTimeout(doSave, 400);
}

// Leaving the page (or loading plans elsewhere) must not drop an edit that is still waiting for its debounce.
export async function flushPlanSave() {
  if (saveTimer !== null) {
    clearTimeout(saveTimer);
    await doSave();
  }
}

function mutate(change) {
  const plan = activePlan();
  if (!plan) return;
  change(plan);
  scheduleSave();
}

// ---- drawing ----
const termLabel = (t) => escapeHtml(t.term);

function cardHtml(plan, code, term) {
  const review = plan.review;
  const bad = review.items.some((i) => i.level === "error" && i.codes.includes(code));
  const taken = (planState.profileStatus || {})[code];
  const status = taken === "now" ? ["In progress", "now"] : taken === "done" ? ["Taken", "done"] : bad ? ["Check prerequisites", "bad"] : ["Planned", "plan"];
  const others = plan.terms.filter((t) => t.term !== term).map((t) => `<option value="${escapeHtml(t.term)}">${termLabel(t)}</option>`).join("");
  return (
    `<div class="plan-card ${status[1]}" draggable="true" data-code="${code}" data-term="${escapeHtml(term)}">` +
    `<div class="top"><a href="#/course/${code}" class="code">${code}</a><button type="button" class="x" data-remove="${code}" aria-label="Remove ${code} from ${escapeHtml(term)}">×</button></div>` +
    `<div class="title">${escapeHtml(plan.titles[code] || "")}</div>` +
    `<div class="state">${status[0]}</div>` +
    (others ? `<label class="sr" for="mv-${code}">Move ${code} to</label><select class="move" id="mv-${code}" data-move="${code}"><option value="">Move to…</option>${others}</select>` : "") +
    `</div>`
  );
}

function termHtml(plan, t) {
  return (
    `<section class="glass term" data-term="${escapeHtml(t.term)}"><h2>${termLabel(t)}</h2>` +
    t.courses.map((c) => cardHtml(plan, c, t.term)).join("") +
    `<form class="add-course" data-term="${escapeHtml(t.term)}"><label class="sr" for="add-${t.term.replace(/\W/g, "")}">Add a course to ${escapeHtml(t.term)}</label>` +
    `<input id="add-${t.term.replace(/\W/g, "")}" list="plan-list" placeholder="+ Add course" autocomplete="off" maxlength="12"></form></section>`
  );
}

function reviewHtml(plan) {
  const r = plan.review;
  const rows = r.items
    .map((item) => {
      const sources = item.chunk_ids
        .map((id) => plan.sources.find((s) => s.chunk_id === id))
        .filter(Boolean)
        .map((s) => `<button type="button" class="cite" data-chunk="${escapeHtml(s.chunk_id)}" aria-label="Open source ${escapeHtml(s.code || s.title)}">${escapeHtml(s.code || "source")}</button>`)
        .join("");
      return `<li class="${item.level}">${ICON[item.level]}<span>${escapeHtml(item.text)} ${sources ? `<span class="cites">${sources}</span>` : ""}</span></li>`;
    })
    .join("");
  const state = saving ? "thinking" : r.status === "ok" ? "idle" : "talking";
  const errors = r.items.filter((i) => i.level === "error").length;
  const notes = r.items.filter((i) => i.level === "warn").length;
  return (
    `<div class="critic-head"><div class="pip-slot">${pipSvg(state, 56)}</div><div><div class="critic-title">Critic report</div>` +
    `<div class="muted">${saving ? "Checking your changes…" : `Checked by code · ${errors} problem${errors === 1 ? "" : "s"} · ${notes} note${notes === 1 ? "" : "s"}`}</div></div></div>` +
    `<ul class="report">${rows}</ul>` +
    `<button type="button" class="btn primary" id="plan-ask">${errors ? "Ask Pip to explain and fix" : "Ask Pip to fill the gap"}</button>` +
    `<p class="muted">The checks are plain code. Pip only explains them. Always confirm a plan with an academic advisor before you enrol.</p>`
  );
}

function draw() {
  const root = el("plan-root");
  const plan = activePlan();
  if (!plan) {
    root.innerHTML =
      `<section class="glass soon"><div>${pipSvg("idle", 84)}</div><h1>No plan yet</h1>` +
      `<p>Make your first plan here, or ask Pip in chat to draft one and press "Save as a plan".</p>` +
      `<button type="button" class="btn primary" id="plan-create">Create Plan A</button><a class="btn" href="#/chat">Ask Pip for a draft</a></section>`;
    return;
  }
  const r = plan.review;
  const [statusText, statusKind] = saving ? ["Checking…", "plain"] : STATUS[r.status] || STATUS.notes;
  const credits = r.credits;
  const required = credits?.required;
  const pct = credits && required ? Math.min(100, Math.round((credits.counted / required[0]) * 100)) : 0;
  const creditLine = credits
    ? `<span>Credits counted (taken plus planned)</span><strong>${credits.counted} of ${required ? (required[0] === required[1] ? required[0] : `${required[0]}–${required[1]}`) : "?"}</strong>`
    : `<span>Credits counted</span><strong>0</strong>`;
  const picker = planState.plans.length > 1 ? `<label class="sr" for="plan-pick">Choose a plan</label><select id="plan-pick">${planState.plans.map((p) => `<option value="${p.id}"${p.id === plan.id ? " selected" : ""}>${escapeHtml(p.name)}</option>`).join("")}</select>` : "";
  root.innerHTML =
    `<div class="page-head"><h1>${escapeHtml(plan.name)}</h1><span class="pill ${statusKind}">${statusText}</span>${picker}` +
    `<div class="head-actions"><button type="button" class="btn" id="plan-dup">Duplicate as Plan ${String.fromCharCode(65 + planState.plans.length)}</button>` +
    `<button type="button" class="btn danger" id="plan-del">Delete plan</button>` +
    `<a class="btn primary" href="#/history">Export for advisor</a></div></div>` +
    (note ? `<p class="form-error" role="alert">${escapeHtml(note)}</p>` : "") +
    `<div class="glass credits"><div class="row">${creditLine}</div><div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><div style="width:${pct}%"></div></div></div>` +
    `<div class="plan-layout"><div class="plan-main"><div class="terms">${plan.terms.map((t) => termHtml(plan, t)).join("")}</div>` +
    `<div><button type="button" class="btn" id="plan-term">+ Add term</button></div>` +
    `<p class="muted">Drag a course to another term, or use its Move menu. Pip re-checks the plan after every change.</p></div>` +
    `<aside class="glass critic" aria-label="Critic report">${reviewHtml(plan)}</aside></div><datalist id="plan-list"></datalist>`;
}

// ---- events ----
function nextTermAfter(plan) {
  const last = plan.terms[plan.terms.length - 1]?.term;
  if (!last) return "2026 Fall";
  const [year, season] = last.split(" ");
  return season === "Fall" ? `${Number(year) + 1} Winter` : `${year} Fall`;
}

async function addCourse(term, raw) {
  const typed = raw.trim().toUpperCase().replace(/\s+/g, "");
  if (!typed) return;
  const found = await getJson(`/api/courses?q=${encodeURIComponent(typed)}`).catch(() => ({ courses: [] }));
  const matches = found.courses.filter((c) => c.code.startsWith(typed));
  const exact = matches.find((c) => c.code === typed);
  if (!exact && matches.length !== 1) {
    note = matches.length ? "Pick a course from the list." : `${typed} isn't in Waypoint's data yet.`;
    draw();
    return;
  }
  const code = (exact || matches[0]).code;
  const plan = activePlan();
  if (plan.terms.some((t) => t.courses.includes(code))) {
    note = `${code} is already in this plan.`;
    draw();
    return;
  }
  note = "";
  plan.titles[code] = (exact || matches[0]).title;
  mutate((p) => p.terms.find((t) => t.term === term).courses.push(code));
}

function moveCourse(code, to) {
  mutate((p) => {
    for (const t of p.terms) t.courses = t.courses.filter((c) => c !== code);
    p.terms.find((t) => t.term === to).courses.push(code);
  });
}

export function wirePlan() {
  const root = el("plan-root");
  root.addEventListener("click", async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    const plan = activePlan();
    if (t.dataset.remove) mutate((p) => p.terms.forEach((term) => (term.courses = term.courses.filter((c) => c !== t.dataset.remove))));
    else if (t.id === "plan-term") mutate((p) => p.terms.push({ term: nextTermAfter(p), courses: [] }));
    else if (t.id === "plan-create") {
      applyPlan(await api("POST", "/api/plans", { name: "Plan A", terms: [{ term: "2026 Fall", courses: [] }] }));
      draw();
    } else if (t.id === "plan-dup") {
      try {
        applyPlan(await api("POST", `/api/plans/${plan.id}/duplicate`, {}));
        note = "";
      } catch (err) {
        note = err.message;
      }
      draw();
    } else if (t.id === "plan-del") {
      if (t.dataset.armed !== "1") {
        t.dataset.armed = "1";
        t.textContent = "Really delete?";
        return;
      }
      await api("DELETE", `/api/plans/${plan.id}`);
      planState.plans = planState.plans.filter((p) => p.id !== plan.id);
      planState.id = planState.plans[0]?.id || null;
      draw();
    } else if (t.id === "plan-ask") {
      const text = plan.review.plan_text || "(no courses yet)";
      window.dispatchEvent(new CustomEvent("waypoint:ask", { detail: `Here is my plan: ${text}. Check it and tell me what to fix or add to finish my program.` }));
    } else if (t.dataset.chunk) {
      window.dispatchEvent(new CustomEvent("waypoint:open-source", { detail: { sources: plan.sources, chunkId: t.dataset.chunk } }));
    }
  });
  root.addEventListener("change", (e) => {
    if (e.target.id === "plan-pick") {
      planState.id = e.target.value;
      draw();
    } else if (e.target.dataset.move && e.target.value) moveCourse(e.target.dataset.move, e.target.value);
  });
  root.addEventListener("submit", (e) => {
    const form = e.target.closest(".add-course");
    if (!form) return;
    e.preventDefault();
    addCourse(form.dataset.term, form.querySelector("input").value);
  });
  let timer;
  root.addEventListener("input", (e) => {
    if (e.target.tagName !== "INPUT") return;
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = e.target.value.trim();
      if (q.length < 2) return;
      const found = await getJson(`/api/courses?q=${encodeURIComponent(q)}`).catch(() => ({ courses: [] }));
      el("plan-list").innerHTML = found.courses.map((c) => `<option value="${c.code}">${escapeHtml(c.title)}</option>`).join("");
    }, 150);
  });
  // Drag and drop between terms (the Move menu is the keyboard route to the same change).
  root.addEventListener("dragstart", (e) => {
    const card = e.target.closest(".plan-card");
    if (card) e.dataTransfer.setData("text/plain", card.dataset.code);
  });
  root.addEventListener("dragover", (e) => {
    if (e.target.closest(".term")) e.preventDefault();
  });
  root.addEventListener("drop", (e) => {
    const term = e.target.closest(".term");
    const code = e.dataTransfer.getData("text/plain");
    if (term && code) {
      e.preventDefault();
      moveCourse(code, term.dataset.term);
    }
  });
}

export async function showPlan() {
  await flushPlanSave();
  const root = el("plan-root");
  if (!planState.loaded) root.innerHTML = '<p class="muted">Loading your plans…</p>';
  try {
    const profile = await getJson("/api/profile");
    planState.profileStatus = Object.fromEntries(profile.courses.map((c) => [c.code, c.status]));
    await loadPlans(true);
  } catch (err) {
    root.innerHTML = `<p class="form-error">Couldn't load your plans: ${escapeHtml(err.message)}</p>`;
    return;
  }
  draw();
}
