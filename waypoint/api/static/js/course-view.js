// The Course page: calendar fields, prerequisites checked against the student's profile, sample sections.
import { escapeHtml } from "./citations.js";
import { getJson } from "./session.js";

const el = (id) => document.getElementById(id);
const BADGE = { done: ["Taken", "ok"], now: ["In progress", "warn"] };
const PREREQ = {
  met: ["You meet the prerequisites listed in the calendar", "ok"],
  unmet: ["You're missing some prerequisites", "bad"],
  unclear: ["The calendar lists conditions Waypoint can't check (grades, program): confirm with an advisor", "warn"],
};

const EMPTY = (url) => `Not in Waypoint's data yet · <a href="${escapeHtml(url)}" target="_blank" rel="noopener">open the official calendar</a>`;

function field(label, value, url) {
  return `<div class="field"><h3>${label}</h3><p>${value ? escapeHtml(value) : EMPTY(url)}</p></div>`;
}

function sectionsTable(sections, label) {
  if (!sections.length) return `<p class="muted">No sections for this course in the sample timetable. <span class="badge warn">Sample data</span></p>`;
  const rows = sections
    .map((s) => {
      const when = s.meetings.map((m) => `${m.day.slice(0, 3)} ${m.start}–${m.end}${m.room ? ` · ${escapeHtml(m.room)}` : ""}`).join("<br>") || "TBA";
      return `<tr><td>${escapeHtml(s.term)}</td><td>${escapeHtml(s.section_type)} ${escapeHtml(s.section_number)}</td><td>${when}</td><td>${escapeHtml(s.instructor || "TBA")}</td><td>${escapeHtml(s.delivery)}</td></tr>`;
    })
    .join("");
  return (
    `<p class="muted"><span class="badge warn">Sample data</span> ${escapeHtml(label)}</p>` +
    `<div class="table-wrap"><table><thead><tr><th>Term</th><th>Section</th><th>Meets</th><th>Instructor</th><th>Delivery</th></tr></thead><tbody>${rows}</tbody></table></div>`
  );
}

export function renderCourse(page) {
  const cal = page.calendar;
  const [statusText, statusKind] = BADGE[page.status] || [null, null];
  const check = page.prerequisite_check;
  const [checkText, checkKind] = PREREQ[check.status] || PREREQ.unclear;
  const required = check.required
    .map((r) => {
      const state = r.status === "done" ? ["taken", "ok"] : r.status === "now" ? ["in progress", "warn"] : ["not on your list", "plain"];
      const link = r.in_data ? `<a href="#/course/${r.code}">${escapeHtml(r.code)}</a>` : `${escapeHtml(r.code)} <span class="muted">(not in Waypoint's data)</span>`;
      return `<li>${link} <span class="badge ${state[1]}">${state[0]}</span></li>`;
    })
    .join("");
  const unlocks = page.unlocks.length
    ? `<p>${page.unlocks.map((u) => `<a class="chip" href="#/course/${u.code}">${escapeHtml(u.code)}</a>`).join(" ")}</p>`
    : `<p class="muted">No courses in Waypoint's data list this one as a prerequisite.</p>`;
  return (
    `<section class="glass hero"><div><h1>${escapeHtml(page.code)}</h1><p class="sub">${escapeHtml(page.title)}</p></div>` +
    `<div class="badges"><span class="badge plain">${page.credits ?? "?"} credit</span>` +
    (cal.distribution ? `<span class="badge plain">${escapeHtml(cal.distribution)}</span>` : "") +
    (statusText ? `<span class="badge ${statusKind}">${statusText}</span>` : "") +
    `<a class="btn" href="#/map/${page.code}">See it on the course map</a></div></section>` +
    `<div class="course-grid"><div class="course-main">` +
    `<section class="glass pane"><h2>Prerequisites</h2><p><span class="badge ${checkKind}">${checkText}</span></p>` +
    (check.text ? `<p class="serif">${escapeHtml(check.text)}</p>` : `<p class="muted">The calendar lists no prerequisites.</p>`) +
    (required ? `<ul class="plain-list">${required}</ul>` : "") +
    `<h3>What it unlocks</h3>${unlocks}</section>` +
    `<section class="glass pane"><h2>From the calendar</h2>` +
    field("Description", cal.description, cal.source_url) +
    field("Corequisites", cal.corequisites, cal.source_url) +
    field("Exclusions", cal.exclusions, cal.source_url) +
    field("Recommended preparation", cal.recommended_preparation, cal.source_url) +
    field("Delivery", cal.delivery, cal.source_url) +
    `<p><a class="btn primary" href="${escapeHtml(cal.source_url)}" target="_blank" rel="noopener">Open on the official calendar</a></p></section>` +
    `<section class="glass pane"><h2>Sections</h2>${sectionsTable(page.sections, page.sections_source)}</section>` +
    `</div><aside class="glass pane ask-pip"><h2>Ask Pip about this course</h2>` +
    page.suggested_questions.map((q) => `<button type="button" class="example" data-q="${escapeHtml(q)}">${escapeHtml(q)}</button>`).join("") +
    `</aside></div>`
  );
}

export async function showCourse(code) {
  const root = el("course-root");
  root.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const page = await getJson(`/api/course/${encodeURIComponent(code)}`);
    document.title = `${page.code} · Waypoint`;
    root.innerHTML = renderCourse(page);
  } catch (err) {
    root.innerHTML = `<section class="glass pane"><h1>${escapeHtml(code)}</h1><p>${escapeHtml(err.message)}</p><p>${EMPTY("https://utm.calendar.utoronto.ca/")}</p></section>`;
  }
}

export function wireCourse() {
  el("course-root").addEventListener("click", (e) => {
    const q = e.target.closest("[data-q]");
    if (q) window.dispatchEvent(new CustomEvent("waypoint:ask", { detail: q.dataset.q }));
  });
}
