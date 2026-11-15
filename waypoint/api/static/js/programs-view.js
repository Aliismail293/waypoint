// The Programs tab: pick a campus and programs, then a checklist of the calendar's requirements against your courses.
// Admission requirements are shown apart from course requirements. Anything Waypoint can't evaluate says
// "Check the calendar" with the exact text, never a guess.
import { escapeHtml, linkCourseCodes } from "./citations.js";
import { api, getJson } from "./session.js";

const el = (id) => document.getElementById(id);
const ui = { campus: "", q: "", list: null, profile: null, open: null, checklist: null, version: 0, error: "" };

export const STATUS = {
  done: ["Done", "ok"],
  in_progress: ["In progress", "warn"],
  planned: ["In your plan", "plan"],
  missing: ["Missing", "bad"],
  check: ["Check the calendar", "plain"],
};

export function summaryText(summary) {
  const parts = [];
  for (const key of ["done", "in_progress", "planned", "missing", "check"]) if (summary[key]) parts.push(`${summary[key]} ${STATUS[key][0].toLowerCase()}`);
  return parts.join(" · ") || "no requirements parsed";
}

function requirementHtml(r, url) {
  const [label, kind] = STATUS[r.status];
  const missing = r.missing.length ? ` <span class="muted">needs ${r.missing.map((c) => `<a href="#/course/${c}">${escapeHtml(c)}</a>`).join(", ")}</span>` : "";
  const check = r.status === "check" ? ` <a href="${escapeHtml(url)}" target="_blank" rel="noopener">open the calendar</a>` : "";
  return `<li class="req ${kind}"><span class="badge ${kind}">${label}</span><span>${linkCourseCodes(escapeHtml(r.text))}${missing}${check}</span></li>`;
}

function checklistHtml(c) {
  const v = c.versions[ui.version] || c.versions[0];
  const need = c.credits.required;
  const needText = need ? (need[0] === need[1] ? need[0] : `${need[0]}-${need[1]}`) : "?";
  const versions =
    c.versions.length > 1
      ? `<div class="chiprow" role="group" aria-label="Calendar version">${c.versions
          .map((x, i) => `<button type="button" class="chip${i === ui.version ? " on" : ""}" data-version="${i}" aria-pressed="${i === ui.version}">${escapeHtml(x.label)}</button>`)
          .join("")}</div>`
      : "";
  const groups = v
    ? v.groups.map((g) => `<h3>${escapeHtml(g.heading)}</h3><ul class="reqs">${g.requirements.map((r) => requirementHtml(r, c.calendar_url)).join("")}</ul>`).join("")
    : `<p class="muted">The calendar text for this program's requirements isn't in a form Waypoint can list. <a href="${escapeHtml(c.calendar_url)}" target="_blank" rel="noopener">Check the calendar</a>.</p>`;
  const rules = c.rules.length
    ? `<h3>Program rules</h3><ul class="plain-list">${c.rules.map((r) => `<li>${escapeHtml(r.text)} <a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">calendar</a></li>`).join("")}</ul>`
    : "";
  const notes = c.notes.length ? `<h3>Notes from the calendar</h3><ul class="plain-list">${c.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>` : "";
  const admission = c.admission.text
    ? `<section class="glass pane"><h2>To get into the program</h2><p class="muted">Admission requirements are separate from course prerequisites and often include minimum grades. Waypoint doesn't check these.</p><pre class="serif wrap">${escapeHtml(c.admission.text)}</pre><a href="${escapeHtml(c.admission.url)}" target="_blank" rel="noopener">Open on the official calendar</a></section>`
    : "";
  return (
    `<section class="glass pane"><h2>${escapeHtml(c.name)}</h2><p class="muted">${escapeHtml(c.code)} · ${c.calendar_year ? escapeHtml(c.calendar_year) + " calendar" : "calendar year not stated"}</p>` +
    `<p><strong>${c.credits.done} of ${needText} credits done</strong> <span class="muted">· ${summaryText(v ? v.summary : {})}</span></p>${versions}${groups}${rules}${notes}</section>` +
    admission
  );
}

function draw() {
  const root = el("programs-root");
  const chosen = ui.profile?.programs || [];
  const campuses = ui.list?.campuses || [];
  const rows = (ui.list?.programs || [])
    .map((p) => {
      const on = chosen.includes(p.code);
      return (
        `<li class="prog"><button type="button" class="prog-open${ui.open === p.code ? " on" : ""}" data-open="${p.code}">${escapeHtml(p.name)}<small>${escapeHtml(p.code)} · ${escapeHtml(p.type)}</small></button>` +
        `<button type="button" class="btn${on ? " primary" : ""}" data-pick="${p.code}" aria-pressed="${on}">${on ? "Selected" : "Select"}</button></li>`
      );
    })
    .join("");
  root.innerHTML =
    `<div class="page-head"><h1>Programs</h1>` +
    `<label class="sr" for="prog-campus">Campus</label><select id="prog-campus" class="glass"><option value="">All campuses</option>${campuses.map((c) => `<option value="${c.code}"${c.code === ui.campus ? " selected" : ""}>${escapeHtml(c.name)}</option>`).join("")}</select>` +
    `<label class="sr" for="prog-q">Search programs</label><input id="prog-q" type="search" class="glass" placeholder="Search majors, minors, specialists" value="${escapeHtml(ui.q)}" autocomplete="off"></div>` +
    (ui.error ? `<p class="form-error" role="alert">${escapeHtml(ui.error)}</p>` : "") +
    `<div class="prog-layout"><aside class="glass pane"><ul class="prog-list">${rows || '<li class="muted">No programs in Waypoint\'s data match this.</li>'}</ul>` +
    (chosen.length ? "" : `<p class="muted">Pick your program to see your credit target in My plan.</p>`) +
    `</aside><div class="prog-main">${ui.checklist ? checklistHtml(ui.checklist) : '<section class="glass pane"><p class="muted">Open a program to see its checklist.</p></section>'}</div></div>`;
}

async function loadList() {
  const q = new URLSearchParams({ campus: ui.campus, q: ui.q });
  ui.list = await getJson(`/api/v3/programs?${q}`);
}

async function openProgram(code) {
  ui.open = code;
  ui.version = 0;
  try {
    ui.checklist = await getJson(`/api/v3/programs/${encodeURIComponent(code)}`);
    ui.error = "";
  } catch (err) {
    ui.checklist = null;
    ui.error = err.message;
  }
  draw();
}

async function pick(code) {
  const chosen = new Set(ui.profile.programs || []);
  if (chosen.has(code)) chosen.delete(code);
  else chosen.add(code);
  const programs = [...chosen];
  try {
    ui.profile = await api("PUT", "/api/profile", { ...ui.profile, programs, program: programs[0] || "", onboarded: true });
    ui.error = "";
    window.dispatchEvent(new CustomEvent("waypoint:status-changed"));
  } catch (err) {
    ui.error = err.message;
  }
  draw();
}

export async function showPrograms() {
  try {
    ui.profile = await getJson("/api/profile");
    await loadList();
    ui.open = ui.open || ui.profile.programs?.[0] || null;
    if (ui.open) ui.checklist = await getJson(`/api/v3/programs/${encodeURIComponent(ui.open)}`).catch(() => null);
  } catch (err) {
    el("programs-root").innerHTML = `<p class="form-error">Couldn't load programs: ${escapeHtml(err.message)}</p>`;
    return;
  }
  draw();
}

export function wirePrograms() {
  const root = el("programs-root");
  root.addEventListener("click", (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.open) openProgram(t.dataset.open);
    else if (t.dataset.pick) pick(t.dataset.pick);
    else if (t.dataset.version !== undefined) {
      ui.version = Number(t.dataset.version);
      draw();
    }
  });
  root.addEventListener("change", async (e) => {
    if (e.target.id === "prog-campus") {
      ui.campus = e.target.value;
      await loadList();
      draw();
    }
  });
  let timer;
  root.addEventListener("input", (e) => {
    if (e.target.id !== "prog-q") return;
    clearTimeout(timer);
    timer = setTimeout(async () => {
      ui.q = e.target.value;
      await loadList();
      draw();
      el("prog-q").focus();
      el("prog-q").setSelectionRange(ui.q.length, ui.q.length);
    }, 200);
  });
}
