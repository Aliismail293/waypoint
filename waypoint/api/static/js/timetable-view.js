// Timetable builder: pick a section per course for a term of your plan, see Mon-Fri, and fix clashes in one click.
// Clash detection and the suggested switch are computed by the server from the sample timetable (plain code).
import { escapeHtml } from "./citations.js";
import { pipSvg } from "./pip.js";
import { activePlan, flushPlanSave, loadPlans } from "./plan-view.js";
import { api } from "./session.js";
import { DAYS, PX_PER_HOUR, GRID_END, GRID_START, blockStyle, gridBlocks, hourLabels } from "./timetable.js";

const el = (id) => document.getElementById(id);
const ui = { term: null, view: null };
const SWATCH = ["#6366F1", "#0E9F6E", "#D97706", "#DB2777", "#0284C7", "#7C3AED"];

function termsWithCourses(plan) {
  return plan.terms.filter((t) => t.courses.length).map((t) => t.term);
}

async function loadTerm() {
  const plan = activePlan();
  ui.view = await api("GET", `/api/plans/${plan.id}/timetable?term=${encodeURIComponent(ui.term)}`);
  draw();
}

function draw() {
  const root = el("tt-root");
  const plan = activePlan();
  const v = ui.view;
  const terms = termsWithCourses(plan);
  const blocks = gridBlocks(v.chosen, v.clashes);
  const rows = (GRID_END - GRID_START) / 60;
  const colour = new Map([...new Set(v.chosen.map((s) => s.course))].map((c, i) => [c, SWATCH[i % SWATCH.length]]));

  const pickers = v.courses
    .map((c) => {
      if (!c.available) {
        return `<div class="picker"><span class="pl">${escapeHtml(c.code)}</span><p class="muted">No sections for this course in the sample timetable for ${escapeHtml(v.term)}.</p></div>`;
      }
      return c.types
        .map(
          (t) =>
            `<div class="picker"><label for="pick-${c.code}-${t.type}" class="pl"><i style="background:${colour.get(c.code) || "#6366F1"}"></i>${escapeHtml(c.code)} · ${escapeHtml(t.type)}</label>` +
            `<select id="pick-${c.code}-${t.type}" data-pick>${t.options.map((o) => `<option value="${escapeHtml(o.section_id)}"${o.section_id === t.selected ? " selected" : ""}>${escapeHtml(o.label)}</option>`).join("")}</select></div>`,
        )
        .join("");
    })
    .join("");

  const fix = v.fixes[0];
  const clashCard = v.clashes.length
    ? `<div class="glass pip-card bad"><div class="head"><div class="pip-slot">${pipSvg("talking", 48)}</div><strong>${new Set(v.clashes.map((c) => `${c.a}|${c.b}`)).size} clash${v.clashes.length === 1 ? "" : "es"} found</strong></div>` +
      `<p>${escapeHtml(v.explanation)}</p>` +
      (fix ? `<button type="button" class="btn primary" id="tt-fix" data-from="${escapeHtml(fix.from)}" data-to="${escapeHtml(fix.to)}">Switch ${escapeHtml(fix.course)} to ${escapeHtml(fix.to_label.split(" · ")[0])}</button>` : "") +
      `</div>`
    : `<div class="glass pip-card"><div class="head"><div class="pip-slot">${pipSvg("idle", 48)}</div><strong>No clashes</strong></div><p>${escapeHtml(v.explanation)}</p></div>`;

  const hours = hourLabels().map((h) => `<div class="hour" style="top:${h.top}px">${h.label}</div>`).join("");
  const lines = Array.from({ length: rows }, (_, i) => `<div class="hline" style="top:${i * PX_PER_HOUR}px"></div>`).join("");
  const cols = DAYS.map((d, i) => {
    const mine = blocks
      .filter((b) => b.day === i)
      .map(
        (b) =>
          `<div class="tt-block${b.clash ? " clash" : ""}" style="${blockStyle(b)}" title="${escapeHtml(b.course)} ${escapeHtml(b.label)}${b.room ? ` · ${escapeHtml(b.room)}` : ""}"><strong>${escapeHtml(b.course)}</strong><br>${escapeHtml(b.label)}</div>`,
      )
      .join("");
    return `<div class="tt-col" role="group" aria-label="${d}">${mine}</div>`;
  }).join("");

  root.innerHTML =
    `<aside class="tt-side"><div class="glass pane"><div class="tt-head"><h1>${escapeHtml(v.term)}</h1><span class="badge warn">Sample data</span></div>` +
    (terms.length > 1 ? `<label for="tt-term" class="sr">Term</label><select id="tt-term">${terms.map((t) => `<option${t === v.term ? " selected" : ""}>${escapeHtml(t)}</option>`).join("")}</select>` : "") +
    pickers +
    `<p class="muted">${escapeHtml(v.data_source)}. Times, rooms and instructors are invented; they are not real UTM offerings.</p></div>${clashCard}</aside>` +
    `<div class="glass tt-grid"><div class="tt-days"><div></div>${DAYS.map((d) => `<div class="day">${d.slice(0, 3)}</div>`).join("")}</div>` +
    `<div class="tt-body"><div class="hours" style="height:${rows * PX_PER_HOUR}px">${hours}</div><div class="tt-cols" style="height:${rows * PX_PER_HOUR}px">${lines}${cols}</div></div></div>`;
}

async function choose(sectionIds) {
  const plan = activePlan();
  ui.view = await api("PUT", `/api/plans/${plan.id}/timetable`, { term: ui.term, section_ids: sectionIds });
  draw();
}

export async function showTimetable() {
  const root = el("tt-root");
  await flushPlanSave();
  await loadPlans(true);
  const plan = activePlan();
  const terms = plan ? termsWithCourses(plan) : [];
  if (!plan || !terms.length) {
    root.innerHTML =
      `<section class="glass soon"><div>${pipSvg("idle", 84)}</div><h1>Build a plan first</h1>` +
      `<p>The timetable builder picks sections for the courses in a term of your plan.</p><a class="btn primary" href="#/plan">Go to My plan</a></section>`;
    return;
  }
  if (!terms.includes(ui.term)) ui.term = terms[0];
  root.innerHTML = '<p class="muted">Loading sections…</p>';
  try {
    await loadTerm();
  } catch (err) {
    root.innerHTML = `<p class="form-error">${escapeHtml(err.message)}</p>`;
  }
}

export function wireTimetable() {
  const root = el("tt-root");
  root.addEventListener("change", async (e) => {
    if (e.target.id === "tt-term") {
      ui.term = e.target.value;
      await loadTerm();
    } else if (e.target.dataset.pick !== undefined) {
      const ids = [...root.querySelectorAll("[data-pick]")].map((s) => s.value);
      await choose(ids);
    }
  });
  root.addEventListener("click", async (e) => {
    const t = e.target.closest("#tt-fix");
    if (!t) return;
    const ids = ui.view.chosen.map((s) => (s.section_id === t.dataset.from ? t.dataset.to : s.section_id));
    await choose(ids);
  });
}

