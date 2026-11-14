// Onboarding (first sign-in, skippable) and the profile settings dialog. Both edit the same profile:
// courses with a status (taken / in progress), a program and a graduation year. Never grades or a student number.
import { escapeHtml } from "./citations.js";
import { pipSvg } from "./pip.js";
import { api, getJson } from "./session.js";

const el = (id) => document.getElementById(id);
const SENTINEL = "";
let programs = [];
let onChange = () => {};

export function onProfileChange(callback) {
  onChange = callback;
}

// ---- course chips with autocomplete ----
function courseEditor(root, initial) {
  const state = { courses: new Map(initial.map((c) => [c.code, c.status])), mode: "done" };
  root.innerHTML =
    `<div class="chips" aria-live="polite"></div>` +
    `<div class="add-row"><label class="sr" for="${root.id}-input">Course code</label>` +
    `<input id="${root.id}-input" type="text" list="${root.id}-list" autocomplete="off" placeholder="Type a code like MAT224…" maxlength="12">` +
    `<datalist id="${root.id}-list"></datalist>` +
    `<div class="seg" role="group" aria-label="Status"><button type="button" data-mode="done" aria-pressed="true">Taken</button>` +
    `<button type="button" data-mode="now" aria-pressed="false">In progress</button></div>` +
    `<button type="button" class="btn" data-add>Add</button></div><p class="form-error" role="alert"></p>`;
  const input = root.querySelector("input");
  const list = root.querySelector("datalist");
  const error = root.querySelector(".form-error");

  const paint = () => {
    root.querySelector(".chips").innerHTML = [...state.courses]
      .map(
        ([code, status]) =>
          `<span class="chip-status ${status}"><button type="button" class="flip" data-flip="${code}" aria-label="${code}: ${status === "done" ? "taken" : "in progress"}. Change status">${escapeHtml(code)} · ${status === "done" ? "done" : "now"}</button>` +
          `<button type="button" class="x" data-remove="${code}" aria-label="Remove ${escapeHtml(code)}">×</button></span>`,
      )
      .join("");
  };
  const add = async () => {
    const typed = input.value.trim().toUpperCase().replace(/\s+/g, "");
    if (!typed) return;
    error.textContent = "";
    const mode = state.mode; // the status chosen when Add was pressed, not when the lookup returns
    // Every code is checked against the catalog, so a typo or a course outside Waypoint's data never becomes a chip.
    const found = await getJson(`/api/courses?q=${encodeURIComponent(typed)}`).catch(() => ({ courses: [] }));
    const matches = found.courses.filter((c) => c.code.startsWith(typed));
    const exact = matches.find((c) => c.code === typed);
    if (!exact && matches.length !== 1) {
      error.textContent = matches.length ? "Pick a course from the list." : `${typed} isn't in Waypoint's data yet.`;
      return;
    }
    const code = (exact || matches[0]).code;
    state.courses.set(code, mode);
    input.value = "";
    paint();
  };
  let timer;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      if (q.length < 2) return;
      const found = await getJson(`/api/courses?q=${encodeURIComponent(q)}`).catch(() => ({ courses: [] }));
      list.innerHTML = found.courses.map((c) => `<option value="${c.code}">${escapeHtml(c.title)}</option>`).join("");
    }, 150);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      add();
    }
  });
  root.addEventListener("click", (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.add !== undefined) add();
    if (t.dataset.remove) state.courses.delete(t.dataset.remove);
    if (t.dataset.flip) state.courses.set(t.dataset.flip, state.courses.get(t.dataset.flip) === "done" ? "now" : "done");
    if (t.dataset.mode) {
      state.mode = t.dataset.mode;
      root.querySelectorAll("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b === t)));
    }
    paint();
  });
  paint();
  return { courses: () => [...state.courses].map(([code, status]) => ({ code, status })), pending: () => input.value.trim() !== "", add };
}

const yearOptions = () => {
  const y = new Date().getFullYear();
  return [y + 1, y + 2, y + 3, y + 4];
};

function programSelect(id, value) {
  const options = [`<option value="${SENTINEL}">Not sure / not listed</option>`]
    .concat(programs.map((p) => `<option value="${escapeHtml(p.code)}"${p.code === value ? " selected" : ""}>${escapeHtml(p.name)} (${escapeHtml(p.code)})</option>`))
    .join("");
  return `<select id="${id}">${options}</select>`;
}

function yearButtons(value) {
  return [...yearOptions(), null]
    .map((y) => `<button type="button" data-year="${y ?? ""}" aria-pressed="${(y ?? null) === (value ?? null)}">${y ?? "Not sure"}</button>`)
    .join("");
}

async function loadPrograms() {
  if (!programs.length) programs = (await getJson("/api/programs").catch(() => ({ programs: [] }))).programs;
}

async function save(profile) {
  const saved = await api("PUT", "/api/profile", { ...profile, onboarded: true });
  onChange(saved);
  return saved;
}

// ---- onboarding ----
const STEPS = ["Program", "Courses", "Goal"];
const SPEECH = [
  "Hi, I'm <strong>Pip</strong>. Tell me about your program and I'll tailor every answer to you.",
  "Tell me what you've already taken, and what you're taking now.",
  "Last one: when do you hope to graduate? I'll use it when we plan terms.",
];

export async function startOnboarding() {
  await loadPrograms();
  const dialog = el("onboard");
  const draft = { program: "", courses: [], grad_year: null };
  let step = 0;
  let editor = null;

  const draw = () => {
    const dots = STEPS.map((name, i) => {
      const state = i < step ? "done" : i === step ? "current" : "todo";
      return `<li class="${state}"${i === step ? ' aria-current="step"' : ""}><span>${i < step ? "✓" : i + 1}</span>${name}</li>`;
    }).join("");
    const body =
      step === 0
        ? `<label for="ob-program" class="q">Which program are you in?</label>${programSelect("ob-program", draft.program)}<p class="muted">Waypoint's data covers Mathematical Sciences for now.</p>`
        : step === 1
          ? `<span class="q" id="ob-courses-label">Courses you've taken or are taking</span><div id="ob-courses" aria-labelledby="ob-courses-label"></div>`
          : `<span class="q">When do you want to graduate?</span><div class="years" role="group" aria-label="Graduation year">${yearButtons(draft.grad_year)}</div>`;
    el("onboard-body").innerHTML =
      `<ol class="steps-dots" aria-label="Setup progress">${dots}</ol>` +
      `<div class="pip-says"><div class="pip-slot">${pipSvg("waving", 120)}</div><div class="bubble">${SPEECH[step]}</div></div>` +
      `<div class="ob-form">${body}</div>` +
      `<p class="privacy"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>` +
      `<span>Saved to your account only, and you can delete it any time. Waypoint never logs in to ACORN or reads transcripts, so don't add grades or your student number.</span></p>` +
      `<p class="form-error" id="ob-error" role="alert"></p>` +
      `<div class="ob-nav"><button type="button" class="btn" id="ob-back"${step === 0 ? " hidden" : ""}>Back</button>` +
      `<button type="button" class="btn quiet" id="ob-skip">Skip for now</button>` +
      `<button type="button" class="btn primary" id="ob-next">${step === STEPS.length - 1 ? "Finish" : "Continue"}</button></div>`;
    editor = step === 1 ? courseEditor(el("ob-courses"), draft.courses) : null;
    const focus = el("onboard-body").querySelector("select, input, [data-year]");
    focus?.focus();
  };

  const collect = async () => {
    if (step === 0) draft.program = el("ob-program").value;
    if (step === 1) {
      if (editor.pending()) await editor.add();
      draft.courses = editor.courses();
    }
  };
  const finish = async (profile) => {
    try {
      await save(profile);
      dialog.close();
    } catch (err) {
      el("ob-error").textContent = err.message;
    }
  };

  el("onboard-body").onclick = async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.year !== undefined) {
      draft.grad_year = t.dataset.year ? Number(t.dataset.year) : null;
      el("onboard-body").querySelectorAll("[data-year]").forEach((b) => b.setAttribute("aria-pressed", String(b === t)));
    } else if (t.id === "ob-back") {
      await collect();
      step -= 1;
      draw();
    } else if (t.id === "ob-next") {
      await collect();
      if (step < STEPS.length - 1) {
        step += 1;
        draw();
      } else await finish(draft);
    } else if (t.id === "ob-skip") await finish({ program: "", courses: [], grad_year: null });
  };
  draw();
  if (!dialog.open) dialog.showModal();
}

// ---- settings ----
export async function openSettings() {
  await loadPrograms();
  const profile = await getJson("/api/profile");
  let year = profile.grad_year;
  el("settings-body").innerHTML =
    `<h2 id="settings-title">Your profile</h2>` +
    `<p class="muted">Used to tailor Pip's answers. Course codes, program and graduation year only: no grades, no student number.</p>` +
    `<label for="st-program" class="q">Program</label>${programSelect("st-program", profile.program)}` +
    `<span class="q">Courses</span><div id="st-courses"></div>` +
    `<span class="q">Graduation year</span><div class="years" role="group" aria-label="Graduation year">${yearButtons(year)}</div>` +
    `<p class="form-error" id="st-error" role="alert"></p><p class="form-ok" id="st-ok" role="status"></p>` +
    `<div class="ob-nav"><button type="button" class="btn danger" id="st-delete">Delete my data</button>` +
    `<button type="button" class="btn" id="st-close">Close</button><button type="button" class="btn primary" id="st-save">Save</button></div>`;
  const editor = courseEditor(el("st-courses"), profile.courses);
  let armed = false;
  el("settings-body").onclick = async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    el("st-error").textContent = "";
    if (t.dataset.year !== undefined) {
      year = t.dataset.year ? Number(t.dataset.year) : null;
      el("settings-body").querySelectorAll("[data-year]").forEach((b) => b.setAttribute("aria-pressed", String(b === t)));
    } else if (t.id === "st-close") el("settings").close();
    else if (t.id === "st-save") {
      try {
        if (editor.pending()) await editor.add();
        await save({ program: el("st-program").value, courses: editor.courses(), grad_year: year });
        el("st-ok").textContent = "Saved.";
      } catch (err) {
        el("st-error").textContent = err.message;
      }
    } else if (t.id === "st-delete") {
      if (!armed) {
        armed = true;
        t.textContent = "Really delete everything?";
        return;
      }
      await api("DELETE", "/api/profile");
      onChange({ courses: [], program: "", grad_year: null, onboarded: false });
      el("settings").close();
    }
  };
  el("settings").showModal();
}
