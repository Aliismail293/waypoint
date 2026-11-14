// The Enrolment check tab (replaces the sample timetable): for next term's planned courses, what to look at before
// enrolling on ACORN. Waypoint never connects to ACORN; it only produces a text list to search there.
import { escapeHtml } from "./citations.js";
import { getJson } from "./session.js";

const el = (id) => document.getElementById(id);

function courseHtml(c) {
  if (!c.known) {
    return `<li class="glass pane"><h3>${escapeHtml(c.code)}</h3><p class="muted">Not in Waypoint's data yet · check the official calendar.</p></li>`;
  }
  const status = c.status === "discontinued" ? '<span class="badge bad">Not offered anymore</span>' : '<span class="badge ok">In the current calendar</span>';
  const bullets = (items) => items.map((x) => `<li>${escapeHtml(x)}</li>`).join("");
  const elsewhere = c.other_campuses.length
    ? `<h4>At other campuses</h4><ul class="plain-list">${c.other_campuses.map((o) => `<li><a href="#/course/${o.code}">${escapeHtml(o.code)}</a> (${escapeHtml(o.campus)}) <span class="muted">${escapeHtml(o.label)}</span></li>`).join("")}</ul>`
    : "";
  return (
    `<li class="glass pane enrol-course"><div class="enrol-head"><h3><a href="#/course/${c.code}">${escapeHtml(c.code)}</a> <small>${escapeHtml(c.title)}</small></h3>${status}</div>` +
    (c.conflicts.length ? `<h4>Heads-up</h4><ul class="headsup">${bullets(c.conflicts)}</ul>` : "") +
    (c.notes.length ? `<h4>Enrolment notes from the calendar</h4><ul class="plain-list">${bullets(c.notes)}</ul>` : `<p class="muted">The calendar lists no enrolment restrictions for this course.</p>`) +
    (c.program_rules.length ? `<h4>Program rules</h4><ul class="plain-list">${bullets(c.program_rules)}</ul>` : "") +
    elsewhere +
    `<p class="muted">Offered this year: ${escapeHtml(c.offered)}</p></li>`
  );
}

export function renderEnrolment(data) {
  if (!data.term) {
    return `<div class="page-head"><h1>Enrolment check</h1></div><section class="glass pane"><p>${escapeHtml(data.message)}</p><a class="btn primary" href="#/plan">Open My plan</a></section>`;
  }
  return (
    `<div class="page-head"><h1>Enrolment check</h1><span class="pill plain">${escapeHtml(data.term)}</span></div>` +
    `<p class="muted">Check these before you enrol on ACORN. Waypoint never connects to ACORN and doesn't know what's offered.</p>` +
    (data.courses.length ? `<ul class="enrol-list">${data.courses.map(courseHtml).join("")}</ul>` : `<section class="glass pane"><p>No courses are planned for ${escapeHtml(data.term)}.</p></section>`) +
    `<p class="muted">${escapeHtml(data.cross_campus_note || "")}</p>` +
    `<section class="glass pane"><h2>ACORN checklist</h2><p class="muted">A text list to search on ACORN, one course at a time.</p><pre id="acorn-text" class="acorn">${escapeHtml(data.acorn)}</pre>` +
    `<button type="button" class="btn primary" id="acorn-copy">Copy the list</button> <span id="acorn-status" role="status" class="muted"></span></section>`
  );
}

export async function showEnrolment() {
  const root = el("enrolment-root");
  root.innerHTML = '<p class="muted">Checking your plan…</p>';
  try {
    root.innerHTML = renderEnrolment(await getJson("/api/v3/enrolment"));
  } catch (err) {
    root.innerHTML = `<p class="form-error">Couldn't load the enrolment check: ${escapeHtml(err.message)}</p>`;
  }
}

export function wireEnrolment() {
  el("enrolment-root").addEventListener("click", async (e) => {
    if (!e.target.closest("#acorn-copy")) return;
    const text = el("acorn-text").textContent;
    try {
      await navigator.clipboard.writeText(text);
      el("acorn-status").textContent = "Copied.";
    } catch {
      el("acorn-status").textContent = "Select the text above and copy it.";
    }
  });
}
