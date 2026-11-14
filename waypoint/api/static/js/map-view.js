// The Course map screen: columns by level, links from the data, an animated route to the selected course.
import { escapeHtml } from "./citations.js";
import { NODE_H, NODE_W, edgePath, layoutMap, routeEdges, routeNote, unlockableNext, visibleCodes } from "./map.js";
import { getJson } from "./session.js";

const el = (id) => document.getElementById(id);
const ui = { data: null, selected: null, route: null, showAll: false, loaded: false };

const STATUS_LABEL = { done: "taken", now: "in progress" };

function nodeKind(n, next) {
  if (n.status === "done") return "taken";
  if (n.status === "now") return "now";
  return next.has(n.code) ? "next" : "other";
}

function draw() {
  const { nodes, edges } = ui.data;
  const visible = visibleCodes(nodes, edges, { showAll: ui.showAll, keep: ui.selected });
  const layout = layoutMap(nodes, visible);
  const next = unlockableNext(nodes, edges);
  const route = new Set(routeEdges(ui.route || {}).map((e) => `${e.from}>${e.to}`));
  const near = new Set(edges.filter((e) => e.from === ui.selected || e.to === ui.selected).map((e) => `${e.from}>${e.to}`));
  const drawn = edges.filter((e) => layout.positions.has(e.from) && layout.positions.has(e.to));

  const paths = drawn
    .map((e) => {
      const key = `${e.from}>${e.to}`;
      const cls = route.has(key) ? "link route" : near.has(key) ? "link near" : "link";
      return `<path class="${cls}" d="${edgePath(layout.positions.get(e.from), layout.positions.get(e.to))}"/>`;
    })
    .sort((a, b) => (a.includes("route") ? 1 : 0) - (b.includes("route") ? 1 : 0))
    .join("");
  const headers = layout.headers
    .map((h) => `<div class="map-col" style="left:${h.x}px">${h.label}${h.count ? ` · ${h.count}` : ""}</div>`)
    .join("");
  const buttons = nodes
    .filter((n) => layout.positions.has(n.code))
    .map((n) => {
      const p = layout.positions.get(n.code);
      const kind = nodeKind(n, next);
      const sub = n.status ? STATUS_LABEL[n.status] : n.title;
      return (
        `<button type="button" class="map-node ${kind}${n.code === ui.selected ? " selected" : ""}" data-code="${n.code}" ` +
        `aria-pressed="${n.code === ui.selected}" style="left:${p.x}px;top:${p.y}px;width:${NODE_W}px;height:${NODE_H}px">` +
        `<span class="c">${escapeHtml(n.code)}</span><span class="s">${escapeHtml(sub)}</span></button>`
      );
    })
    .join("");
  el("map-canvas").style.cssText = `width:${layout.width}px;height:${layout.height}px`;
  el("map-canvas").innerHTML =
    headers + `<svg width="${layout.width}" height="${layout.height}" viewBox="0 0 ${layout.width} ${layout.height}" aria-hidden="true">${paths}</svg>` + buttons;
  el("map-count").textContent = `${layout.positions.size} of ${nodes.filter((n) => n.in_data).length} courses shown`;
  el("map-all").textContent = ui.showAll ? "Show fewer courses" : "Show all courses";
  el("map-all").setAttribute("aria-pressed", String(ui.showAll));
}

function panel() {
  const code = ui.selected;
  const node = ui.data.nodes.find((n) => n.code === code);
  const side = el("map-side");
  if (!node) {
    side.innerHTML =
      '<div class="eyebrow">YOUR ROUTE TO</div><p class="muted">Click a course, or type a code above, and Waypoint draws the route from the courses you have.</p>';
    return;
  }
  const r = ui.route;
  const steps = (r?.steps || [])
    .map((s) => {
      const dot = s.status === "done" ? "taken" : s.status === "now" ? "now" : "target";
      const label = s.status ? STATUS_LABEL[s.status] : s.code === code ? `unlocked after ${r.steps[r.steps.length - 2]?.code || "its prerequisites"}` : "to take";
      return `<li class="${s.code === code ? "last" : ""}"><span class="dot ${dot}"></span>${escapeHtml(s.code)} · ${label}</li>`;
    })
    .join("");
  const direct = (r?.prerequisites || []).length
    ? `<div><div class="eyebrow">DIRECT PREREQUISITES IN THE DATA</div><ul class="plain-list">${r.prerequisites
        .map((p) => `<li>${escapeHtml(p.code)} <span class="badge ${p.status === "done" ? "ok" : p.status === "now" ? "warn" : "plain"}">${STATUS_LABEL[p.status] || "not on your list"}</span></li>`)
        .join("")}</ul></div>`
    : "";
  const sourceLine = node.in_data ? `<a href="#/course/${code}">Open course page</a>` : "";
  side.innerHTML =
    `<div class="eyebrow">YOUR ROUTE TO</div><div><div class="map-title">${escapeHtml(code)}</div><div>${escapeHtml(node.title || "Outside Waypoint's data")}</div></div>` +
    (steps ? `<ol class="route-steps">${steps}</ol>` : "") +
    `<p class="muted">${routeNote(r?.state)}</p>${direct}` +
    `<a class="btn primary" href="#/plan">Add to my plan</a>` +
    (sourceLine ? `<a class="btn" href="#/course/${code}">Open course page</a>` : "") +
    `<button type="button" class="btn quiet" id="map-ask">Ask Pip about this route</button>`;
  el("map-ask").addEventListener("click", () =>
    window.dispatchEvent(new CustomEvent("waypoint:ask", { detail: `What do I need to take to get to ${code}, starting from the courses I have?` })),
  );
}

export async function select(code) {
  ui.selected = code;
  ui.route = null;
  if (code) {
    try {
      ui.route = await getJson(`/api/route/${encodeURIComponent(code)}`);
    } catch {
      ui.route = { state: "unknown_course", steps: [], missing: [] };
    }
  }
  draw();
  panel();
}

export async function showMap(code) {
  if (!ui.loaded) {
    el("map-canvas").textContent = "Loading the course map…";
    try {
      ui.data = await getJson("/api/map");
      ui.loaded = true;
    } catch (err) {
      el("map-canvas").textContent = `Couldn't load the map: ${err.message}`;
      return;
    }
  }
  el("map-list").innerHTML = ui.data.nodes.filter((n) => n.in_data).map((n) => `<option value="${n.code}">${escapeHtml(n.title)}</option>`).join("");
  await select(code || ui.selected);
}

// The profile changed (onboarding, settings, delete): the next visit reloads statuses.
export function invalidateMap() {
  ui.loaded = false;
  ui.route = null;
}

export function wireMap() {
  el("map-canvas").addEventListener("click", (e) => {
    const node = e.target.closest(".map-node");
    if (node) {
      location.hash = `#/map/${node.dataset.code}`;
    }
  });
  el("map-all").addEventListener("click", () => {
    ui.showAll = !ui.showAll;
    if (ui.data) draw();
  });
  el("map-find").addEventListener("submit", (e) => {
    e.preventDefault();
    const typed = el("map-goal").value.trim().toUpperCase().replace(/\s+/g, "");
    if (!typed || !ui.data) return;
    const match = ui.data.nodes.find((n) => n.code === typed) || ui.data.nodes.find((n) => n.code.startsWith(typed));
    el("map-error").textContent = match ? "" : `${typed} isn't in Waypoint's data yet.`;
    if (match) location.hash = `#/map/${match.code}`;
  });
}
