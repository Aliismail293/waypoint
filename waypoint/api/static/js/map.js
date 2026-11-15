// Course map (V3): big columns by level, sub-columns by prerequisite depth, path highlighting and filters.
// The server sends each node's band (1-4) and sub-column (depth inside its band) and the real prerequisite edges;
// this module only places them and works out highlights. Nothing is inferred: no edge in the data, no line.
export const BANDS = [
  { band: 1, label: "100-LEVEL" },
  { band: 2, label: "200-LEVEL" },
  { band: 3, label: "300-LEVEL" },
  { band: 4, label: "400-LEVEL" },
];
export const NODE_W = 150;
export const NODE_H = 50;
const PITCH_X = 190; // between sub-columns
const PITCH_Y = 64;
const BAND_GAP = 44;
const TOP = 40;
const LEFT = 20;
export const HIGHLIGHT = "#F59E0B"; // amber, 3px (spec 4.1)

// Ghost cards (prerequisites outside the filter) sit in their own column at the left edge.
export function layoutMap(nodes) {
  const ghosts = nodes.filter((n) => n.ghost);
  const real = nodes.filter((n) => !n.ghost);
  const positions = new Map();
  let x = LEFT;
  const headers = [];
  let rows = 1;
  if (ghosts.length) {
    ghosts.sort((a, b) => a.code.localeCompare(b.code)).forEach((n, row) => positions.set(n.code, { x, y: TOP + row * PITCH_Y }));
    headers.push({ id: "ghost", label: "OUTSIDE THIS VIEW", x, w: NODE_W, count: ghosts.length });
    rows = Math.max(rows, ghosts.length);
    x += NODE_W + BAND_GAP;
  }
  for (const { band, label } of BANDS) {
    const inBand = real.filter((n) => n.band === band);
    if (!inBand.length) continue;
    const subs = Math.max(...inBand.map((n) => n.sub)) + 1;
    for (let s = 0; s < subs; s++) {
      const col = inBand.filter((n) => n.sub === s).sort((a, b) => a.code.localeCompare(b.code));
      rows = Math.max(rows, col.length);
      col.forEach((n, row) => positions.set(n.code, { x: x + s * PITCH_X, y: TOP + row * PITCH_Y }));
    }
    const w = (subs - 1) * PITCH_X + NODE_W;
    headers.push({ id: `band${band}`, label, x, w, count: inBand.length });
    x += w + BAND_GAP;
  }
  return { positions, headers, width: x - BAND_GAP + LEFT, height: TOP + rows * PITCH_Y + 10 };
}

// A curve from the right edge of one course to the left edge of the next; links inside one sub-column loop out.
export function edgePath(from, to) {
  const y1 = from.y + NODE_H / 2;
  const y2 = to.y + NODE_H / 2;
  if (from.x === to.x) {
    const x = from.x + NODE_W;
    return `M${x} ${y1}C${x + 40} ${y1} ${x + 40} ${y2} ${x} ${y2}`;
  }
  if (from.x > to.x) {
    // a prerequisite drawn to the right of its course (ghost columns, or a chain across bands): go right-to-left
    const x1 = from.x;
    const mid = (x1 + to.x + NODE_W) / 2;
    return `M${x1} ${y1}C${mid} ${y1} ${mid} ${y2} ${to.x + NODE_W} ${y2}`;
  }
  const x1 = from.x + NODE_W;
  const mid = (x1 + to.x) / 2;
  return `M${x1} ${y1}C${mid} ${y1} ${mid} ${y2} ${to.x} ${y2}`;
}

export const edgeKey = (e) => `${e.from}>${e.to}`;

// Everything on any prerequisite path into `code` (the course itself included) and the edges between them.
export function pathInto(edges, code) {
  const into = new Map();
  for (const e of edges) into.set(e.to, [...(into.get(e.to) || []), e]);
  const nodes = new Set([code]);
  const used = new Set();
  const stack = [code];
  while (stack.length) {
    for (const e of into.get(stack.pop()) || []) {
      used.add(edgeKey(e));
      if (!nodes.has(e.from)) {
        nodes.add(e.from);
        stack.push(e.from);
      }
    }
  }
  return { nodes, edges: used };
}

// The "or" badge sits at the midpoint of the first edge of each `one_of` group.
export function orBadges(edges, positions) {
  const seen = new Set();
  const badges = [];
  for (const e of edges) {
    if (e.kind !== "one_of") continue;
    const id = `${e.to}#${e.group}`;
    const a = positions.get(e.from);
    const b = positions.get(e.to);
    if (seen.has(id) || !a || !b) continue;
    seen.add(id);
    badges.push({ id, x: (a.x + NODE_W + b.x) / 2, y: (a.y + b.y) / 2 + NODE_H / 2 });
  }
  return badges;
}

export const subjectLabel = (s) => (s.name ? `${s.name} · ${s.code}` : s.code);

// "Find a route to…": code prefix, or words from the title.
export function findCourses(nodes, query, limit = 8) {
  const q = query.trim().toLowerCase().replace(/\s+/g, "");
  if (!q) return [];
  return nodes
    .filter((n) => !n.ghost && (n.code.toLowerCase().startsWith(q) || n.title.toLowerCase().replace(/\s+/g, "").includes(q)))
    .slice(0, limit);
}

// Legend colour class of a card; a status chosen in the popover recolours it immediately.
export function cardKind(n) {
  if (n.ghost) return "ghost";
  return { done: "taken", now: "now", planned: "planned" }[n.status] || "other";
}

export function setStatus(nodes, code, status) {
  return nodes.map((n) => (n.code === code ? { ...n, status } : n));
}
