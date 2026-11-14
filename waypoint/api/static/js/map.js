// Course map: which courses to show, where they go, and the SVG path of each link (DESIGN_SPEC section 5).
// Everything here is drawn from the links the server sends, which come from the prerequisites table: nothing is inferred.
export const COLUMNS = [
  { id: "before", label: "BEFORE" },
  { id: "now", label: "NOW" },
  { id: "lower", label: "100 / 200-LEVEL" },
  { id: "upper", label: "300 / 400-LEVEL" },
];
export const NODE_W = 190;
export const NODE_H = 54;
const PITCH_X = 280;
const PITCH_Y = 72;
const TOP = 36;
const LEFT = 24;

// Courses within `hops` links downstream of the student's courses, plus the student's own, plus `keep` and its
// prerequisites. A student with no courses yet sees every course that is in Waypoint's data.
export function visibleCodes(nodes, edges, { showAll = false, keep = null, hops = 2 } = {}) {
  const mine = nodes.filter((n) => n.status).map((n) => n.code);
  const all = nodes.filter((n) => n.in_data).map((n) => n.code);
  if (showAll || mine.length === 0) return new Set(all);
  const unlocks = new Map();
  const prereqs = new Map();
  for (const e of edges) {
    if (!unlocks.has(e.from)) unlocks.set(e.from, []);
    unlocks.get(e.from).push(e.to);
    if (!prereqs.has(e.to)) prereqs.set(e.to, []);
    prereqs.get(e.to).push(e.from);
  }
  const seen = new Set(mine);
  let frontier = mine;
  for (let i = 0; i < hops; i++) {
    frontier = frontier.flatMap((c) => unlocks.get(c) || []).filter((c) => !seen.has(c));
    frontier.forEach((c) => seen.add(c));
  }
  if (keep) {
    seen.add(keep);
    (prereqs.get(keep) || []).forEach((c) => seen.add(c));
  }
  const known = new Set(nodes.map((n) => n.code));
  return new Set([...seen].filter((c) => known.has(c)));
}

export function layoutMap(nodes, visible) {
  const byColumn = Object.fromEntries(COLUMNS.map((c) => [c.id, []]));
  for (const n of nodes) if (visible.has(n.code)) byColumn[n.column].push(n);
  const positions = new Map();
  let rows = 1;
  COLUMNS.forEach((col, i) => {
    const list = byColumn[col.id].sort((a, b) => a.code.localeCompare(b.code));
    rows = Math.max(rows, list.length);
    list.forEach((n, row) => positions.set(n.code, { x: LEFT + i * PITCH_X, y: TOP + row * PITCH_Y }));
  });
  return {
    positions,
    width: LEFT * 2 + (COLUMNS.length - 1) * PITCH_X + NODE_W,
    height: TOP + rows * PITCH_Y,
    headers: COLUMNS.map((c, i) => ({ ...c, x: LEFT + i * PITCH_X, count: byColumn[c.id].length })),
  };
}

// A curve from the right edge of one course to the left edge of the next; links inside one column loop out and back.
export function edgePath(from, to) {
  const y1 = from.y + NODE_H / 2;
  const y2 = to.y + NODE_H / 2;
  if (from.x === to.x) {
    const x = from.x + NODE_W;
    return `M${x} ${y1}C${x + 44} ${y1} ${x + 44} ${y2} ${x} ${y2}`;
  }
  const [a, b] = from.x < to.x ? [from, to] : [to, from];
  const ya = a.y + NODE_H / 2;
  const yb = b.y + NODE_H / 2;
  const x1 = a.x + NODE_W;
  const mid = (x1 + b.x) / 2;
  return `M${x1} ${ya}C${mid} ${ya} ${mid} ${yb} ${b.x} ${yb}`;
}

// Courses whose in-data prerequisites are all among the student's courses: "unlocks next".
export function unlockableNext(nodes, edges) {
  const have = new Set(nodes.filter((n) => n.status).map((n) => n.code));
  const needs = new Map();
  for (const e of edges) needs.set(e.to, [...(needs.get(e.to) || []), e.from]);
  return new Set(
    nodes
      .filter((n) => n.in_data && !n.status && (needs.get(n.code) || []).length && needs.get(n.code).every((c) => have.has(c)))
      .map((n) => n.code),
  );
}

export function routeEdges(route) {
  const steps = route.steps || [];
  return steps.slice(1).map((s, i) => ({ from: steps[i].code, to: s.code }));
}

const NOTES = {
  route: "Waypoint draws only the prerequisite links in its data. Some courses have other requirements too (grades, program enrolment): open the course page to check them all.",
  no_route: "None of this course's prerequisite links in Waypoint's data lead back to the courses you've added.",
  no_links: "Waypoint's data lists no prerequisite links for this course, so there's no route to draw.",
  already_taken: "You've already taken this course.",
  unknown_course: "That course isn't in Waypoint's data yet.",
};
export const routeNote = (state) => NOTES[state] || NOTES.route;
