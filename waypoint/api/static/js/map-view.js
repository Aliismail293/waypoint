// The Course map screen (V3): big level columns with prerequisite-depth sub-columns, a campus/subject filter, path
// highlighting, and a status popover that recolours the card at once and saves to the profile.
import { escapeHtml } from "./citations.js";
import { HIGHLIGHT, NODE_H, NODE_W, cardKind, edgeKey, edgePath, findCourses, layoutMap, orBadges, pathInto, setStatus, subjectLabel } from "./map.js";
import { api, getJson } from "./session.js";

const el = (id) => document.getElementById(id);
const ui = { data: null, selected: null, campus: "all", subjects: [], loaded: false, error: "" };
const STATUS_LABEL = { done: "Taken", now: "In progress", planned: "Planned" };

async function load() {
  const query = new URLSearchParams({ campus: ui.campus, subject: ui.subjects.join(",") });
  el("map-canvas").textContent = "Loading the course map…";
  try {
    ui.data = await getJson(`/api/v3/map?${query}`);
    ui.loaded = true;
  } catch (err) {
    el("map-canvas").textContent = `Couldn't load the map: ${err.message}`;
    return false;
  }
  fillFilters();
  return true;
}

function fillFilters() {
  const { campuses, subjects } = ui.data;
  el("map-campus").innerHTML =
    `<option value="all">All campuses</option>` +
    campuses.map((c) => `<option value="${escapeHtml(c.code)}"${c.code === ui.campus ? " selected" : ""}>${escapeHtml(c.name)}</option>`).join("");
  el("map-subject").innerHTML =
    `<option value="">Show all</option>` + subjects.map((s) => `<option value="${escapeHtml(s.code)}">${escapeHtml(subjectLabel(s))}</option>`).join("");
  el("map-chips").innerHTML = ui.subjects
    .map((code) => {
      const s = subjects.find((x) => x.code === code);
      return `<button type="button" class="chip on" data-drop="${escapeHtml(code)}" aria-label="Remove ${escapeHtml(code)} from the view">${escapeHtml(s ? subjectLabel(s) : code)} ×</button>`;
    })
    .join("");
  el("map-list").innerHTML = ui.data.nodes.filter((n) => !n.ghost).map((n) => `<option value="${n.code}">${escapeHtml(n.title)}</option>`).join("");
}

function draw() {
  const { nodes, edges } = ui.data;
  const layout = layoutMap(nodes);
  const path = ui.selected ? pathInto(edges, ui.selected) : null;
  const drawn = edges.filter((e) => layout.positions.has(e.from) && layout.positions.has(e.to));
  const paths = drawn
    .map((e) => {
      const lit = path?.edges.has(edgeKey(e));
      const cls = ["link", e.kind === "one_of" ? "or" : "", path ? (lit ? "hl" : "dim") : ""].join(" ").trim();
      const stroke = lit ? ` stroke="${HIGHLIGHT}" stroke-width="3"` : "";
      return { lit, html: `<path class="${cls}"${stroke} d="${edgePath(layout.positions.get(e.from), layout.positions.get(e.to))}"/>` };
    })
    .sort((a, b) => Number(a.lit) - Number(b.lit)) // highlighted edges are painted last, on top
    .map((p) => p.html)
    .join("");
  const badges = orBadges(drawn, layout.positions)
    .map((b) => `<text class="or-badge" x="${b.x}" y="${b.y}" text-anchor="middle">or</text>`)
    .join("");
  const headers = layout.headers
    .map((h) => `<div class="map-col" style="left:${h.x}px;width:${h.w}px">${h.label} · ${h.count}</div>`)
    .join("");
  const buttons = nodes
    .filter((n) => layout.positions.has(n.code))
    .map((n) => {
      const p = layout.positions.get(n.code);
      const dim = path && !path.nodes.has(n.code) ? " dim" : "";
      const sub = n.ghost ? `${n.subject} · add to view` : n.status ? STATUS_LABEL[n.status] : n.title;
      return (
        `<button type="button" class="map-node ${cardKind(n)}${n.code === ui.selected ? " selected" : ""}${dim}" data-code="${n.code}" ` +
        `aria-pressed="${n.code === ui.selected}" style="left:${p.x}px;top:${p.y}px;width:${NODE_W}px;height:${NODE_H}px">` +
        `<span class="c">${escapeHtml(n.code)}</span><span class="s">${escapeHtml(sub)}</span></button>`
      );
    })
    .join("");
  el("map-canvas").style.cssText = `width:${layout.width}px;height:${layout.height}px`;
  el("map-canvas").innerHTML =
    headers + `<svg width="${layout.width}" height="${layout.height}" viewBox="0 0 ${layout.width} ${layout.height}" aria-hidden="true">${paths}${badges}</svg>` + buttons;
  const shown = nodes.filter((n) => !n.ghost).length;
  el("map-count").textContent = `${shown} course${shown === 1 ? "" : "s"} shown`;
  el("map-error").textContent = ui.error;
  if (!shown) el("map-canvas").insertAdjacentHTML("afterbegin", `<p class="muted pad">No courses in Waypoint's data match this filter.</p>`);
  popover(layout);
}

function popover(layout) {
  const old = el("map-pop");
  if (old) old.remove();
  const node = ui.data.nodes.find((n) => n.code === ui.selected);
  if (!node || node.ghost) return;
  const p = layout.positions.get(node.code);
  if (!p) return;
  const row = (s, label) => `<button type="button" class="btn${node.status === s ? " primary" : ""}" data-status="${s}" aria-pressed="${node.status === s}">${label}</button>`;
  const info = node.prereq_text || "No prerequisites listed.";
  const left = Math.min(p.x + NODE_W + 12, Math.max(layout.width - 280, 0));
  el("map-canvas").insertAdjacentHTML(
    "beforeend",
    `<div id="map-pop" class="glass map-pop" role="dialog" aria-label="${escapeHtml(node.code)} options" style="left:${left}px;top:${p.y}px">` +
      `<div class="map-title">${escapeHtml(node.code)}</div><div class="muted">${escapeHtml(node.title)}</div>` +
      `<div class="status-row">${row("done", "Taken")}${row("now", "In progress")}` +
      `<span class="planned-pick"><input id="map-term" class="glass" value="${escapeHtml(defaultTerm())}" aria-label="Term to plan ${escapeHtml(node.code)} in" maxlength="12">${row("planned", "Planned")}</span>` +
      `<button type="button" class="btn quiet" data-status="">Clear</button></div>` +
      `<details class="info"><summary aria-label="Prerequisites of ${escapeHtml(node.code)}"><span class="i-badge">i</span> Prerequisites</summary><p>${escapeHtml(info)}</p></details>` +
      `<div class="pop-links"><a href="#/course/${node.code}">Open course page</a> · <button type="button" class="linklike" id="map-ask">Ask Pip</button></div>` +
      `<p class="muted pop-note" id="map-pop-note" role="status"></p></div>`,
  );
}

function defaultTerm() {
  const d = new Date();
  const m = d.getMonth() + 1;
  const y = d.getFullYear();
  return m >= 11 ? `${y + 1} Winter` : m >= 6 ? `${y} Fall` : m >= 3 ? `${y} Summer` : `${y} Winter`;
}

async function saveStatus(code, status) {
  if (status === "planned") {
    const term = el("map-term")?.value.trim() || defaultTerm();
    await api("POST", "/api/plans/add-course", { code, term });
    return;
  }
  const profile = await api("GET", "/api/profile");
  const courses = profile.courses.filter((c) => c.code !== code);
  if (status) courses.push({ code, status });
  await api("PUT", "/api/profile", { ...profile, courses });
  if (!status) await dropFromPlans(code);
}

async function dropFromPlans(code) {
  const { plans } = await getJson("/api/plans");
  for (const plan of plans.filter((p) => p.terms.some((t) => t.courses.includes(code)))) {
    const terms = plan.terms.map((t) => ({ term: t.term, courses: t.courses.filter((c) => c !== code) }));
    await api("PUT", `/api/plans/${plan.id}`, { name: plan.name, terms });
  }
}

async function choose(code, status) {
  const before = ui.data.nodes;
  ui.data.nodes = setStatus(before, code, status || null); // recolour now, save after
  ui.error = "";
  draw();
  try {
    await saveStatus(code, status);
    window.dispatchEvent(new CustomEvent("waypoint:status-changed"));
  } catch (err) {
    ui.data.nodes = before;
    ui.error = err.message;
    draw();
  }
}

export function select(code) {
  ui.selected = code || null;
  if (ui.data) draw();
}

export async function showMap(code) {
  if (!ui.loaded && !(await load())) return;
  if (code && !ui.data.nodes.some((n) => n.code === code)) {
    ui.error = `${code} isn't in this view. Change the filter or pick another course.`;
    code = null;
  }
  select(code || ui.selected);
}

// The profile changed (onboarding, settings, delete): the next visit reloads statuses.
export function invalidateMap() {
  ui.loaded = false;
}

async function refilter() {
  ui.selected = null;
  if (await load()) draw();
}

export function wireMap() {
  const canvas = el("map-canvas");
  canvas.addEventListener("click", async (e) => {
    const status = e.target.closest("[data-status]");
    if (status && ui.selected) return choose(ui.selected, status.dataset.status);
    if (e.target.closest("#map-ask")) {
      return window.dispatchEvent(new CustomEvent("waypoint:ask", { detail: `What do I need to take to get to ${ui.selected}, starting from the courses I have?` }));
    }
    if (e.target.closest("#map-pop")) return;
    const node = e.target.closest(".map-node");
    if (!node) {
      if (ui.selected) {
        ui.selected = null; // click empty space: reset the highlight
        draw();
      }
      return;
    }
    const data = ui.data.nodes.find((n) => n.code === node.dataset.code);
    if (data?.ghost) {
      if (!ui.subjects.includes(data.subject)) ui.subjects.push(data.subject);
      return refilter();
    }
    ui.selected = node.dataset.code;
    draw();
  });
  el("map-campus").addEventListener("change", (e) => {
    ui.campus = e.target.value;
    ui.subjects = [];
    refilter();
  });
  el("map-subject").addEventListener("change", (e) => {
    if (e.target.value && !ui.subjects.includes(e.target.value)) ui.subjects.push(e.target.value);
    else if (!e.target.value) ui.subjects = [];
    refilter();
  });
  el("map-chips").addEventListener("click", (e) => {
    const chip = e.target.closest("[data-drop]");
    if (!chip) return;
    ui.subjects = ui.subjects.filter((c) => c !== chip.dataset.drop);
    refilter();
  });
  el("map-find").addEventListener("submit", async (e) => {
    e.preventDefault();
    const typed = el("map-goal").value;
    if (!typed.trim() || !ui.data) return;
    const [match] = findCourses(ui.data.nodes, typed);
    ui.error = match ? "" : `${typed.trim().toUpperCase()} isn't in this view. Try "Show all", or another subject.`;
    ui.selected = match ? match.code : null;
    draw();
  });
}
