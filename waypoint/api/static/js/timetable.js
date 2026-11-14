// Timetable grid maths: meetings -> positioned blocks, side by side where they overlap (DESIGN_SPEC section 5).
export const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];
export const GRID_START = 8 * 60;
export const GRID_END = 21 * 60;
export const PX_PER_HOUR = 48;
export const COURSE_COLOURS = [
  { bg: "#E0E7FF", edge: "#6366F1" },
  { bg: "#D1FAE5", edge: "#0E9F6E" },
  { bg: "#FEF3C7", edge: "#D97706" },
  { bg: "#FCE7F3", edge: "#DB2777" },
  { bg: "#E0F2FE", edge: "#0284C7" },
  { bg: "#EDE9FE", edge: "#7C3AED" },
];

export const toMinutes = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};

// One block per meeting. Meetings on the same day that overlap share the day's width.
export function gridBlocks(chosen, clashes = []) {
  const clashing = new Set(clashes.flatMap((c) => [c.a, c.b]));
  const colour = new Map([...new Set(chosen.map((s) => s.course))].map((c, i) => [c, COURSE_COLOURS[i % COURSE_COLOURS.length]]));
  const blocks = [];
  for (const section of chosen) {
    for (const m of section.meetings) {
      const day = DAYS.indexOf(m.day);
      if (day < 0) continue; // a weekend meeting has no column
      blocks.push({
        day,
        start: toMinutes(m.start),
        end: toMinutes(m.end),
        course: section.course,
        label: `${section.type.slice(0, 3).toUpperCase()}${section.number}`,
        sectionId: section.section_id,
        room: m.room,
        clash: clashing.has(section.section_id),
        ...colour.get(section.course),
        col: 0,
        cols: 1,
      });
    }
  }
  for (let d = 0; d < DAYS.length; d++) {
    const today = blocks.filter((b) => b.day === d).sort((a, b) => a.start - b.start || a.end - b.end);
    let group = [];
    let groupEnd = -1;
    const flush = () => {
      const cols = Math.max(1, ...group.map((b) => b.col + 1));
      group.forEach((b) => (b.cols = cols));
      group = [];
    };
    for (const b of today) {
      if (group.length && b.start >= groupEnd) flush();
      const used = new Set(group.filter((g) => g.end > b.start).map((g) => g.col));
      let col = 0;
      while (used.has(col)) col++;
      b.col = col;
      group.push(b);
      groupEnd = Math.max(groupEnd, b.end);
    }
    flush();
  }
  return blocks;
}

export const blockStyle = (b) => {
  const top = ((b.start - GRID_START) / 60) * PX_PER_HOUR;
  const height = ((b.end - b.start) / 60) * PX_PER_HOUR;
  const width = 100 / b.cols;
  return `top:${top}px;height:${height - 2}px;left:calc(${b.col * width}% + 2px);width:calc(${width}% - 4px);background:${b.bg};border-left-color:${b.edge}`;
};

export const hourLabels = () => {
  const out = [];
  for (let m = GRID_START; m < GRID_END; m += 60) out.push({ label: `${Math.floor(m / 60)}:00`, top: ((m - GRID_START) / 60) * PX_PER_HOUR });
  return out;
};
