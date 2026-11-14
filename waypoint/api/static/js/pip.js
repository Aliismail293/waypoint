// Pip, the Waypoint robot: a 20x18 pixel sprite (DESIGN_SPEC section 4). One function builds the SVG for a state;
// the animation is CSS only (frame groups toggle opacity), and prefers-reduced-motion freezes it on frame A.
export const PIP_STATES = ["idle", "thinking", "talking", "waving"];

const rect = (x, y, w, h, fill) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${fill}"/>`;
const HEAD = "#6366F1", FACE = "#FFFFFF", INK = "#141A2E", LIMB = "#3730A3", AMBER = "#F59E0B";

const LABELS = {
  idle: "Pip, the Waypoint robot",
  thinking: "Pip is thinking",
  talking: "Pip is answering",
  waving: "Pip is waving hello",
};

function eyes(state) {
  const h = state === "thinking" ? 1 : 2; // squinting while thinking
  const open = rect(5, 4, 2, h, INK) + rect(9, 4, 2, h, INK);
  // idle: a face-coloured lid slides over the eyes for a moment every ~4 s
  return state === "idle" ? open + `<g class="pip-blink">${rect(5, 4, 2, 2, FACE)}${rect(9, 4, 2, 2, FACE)}</g>` : open;
}

function mouth(state) {
  if (state === "talking" || state === "waving") {
    return (
      `<g class="pip-ma">${rect(6, 7, 4, 1, INK)}</g>` +
      `<g class="pip-mb">${rect(6, 6, 4, 2, INK)}${rect(7, 7, 2, 1, FACE)}</g>`
    );
  }
  return state === "thinking" ? rect(6, 7, 3, 1, INK) : `<path d="M6 7h4v1h-4z" fill="${INK}"/>`;
}

function rightArm(state) {
  if (state === "thinking") {
    return (
      `<g class="pip-fa">${rect(13, 5, 2, 6, LIMB)}${rect(12, 3, 2, 2, HEAD)}</g>` +
      `<g class="pip-fb">${rect(13, 4, 2, 7, LIMB)}${rect(13, 2, 2, 2, HEAD)}</g>`
    );
  }
  if (state === "waving") {
    return (
      `<g class="pip-fa">${rect(13, 4, 2, 6, LIMB)}${rect(14, 2, 2, 2, HEAD)}</g>` +
      `<g class="pip-fb">${rect(13, 4, 2, 6, LIMB)}${rect(12, 2, 2, 2, HEAD)}</g>`
    );
  }
  return rect(12, 10, 2, 3, LIMB);
}

function thoughtMark(state) {
  if (state !== "thinking") return "";
  return (
    `<g class="pip-fb">${rect(16, 0, 2, 1, AMBER)}${rect(17, 1, 1, 1, AMBER)}${rect(16, 2, 1, 1, AMBER)}` +
    `${rect(16, 4, 1, 1, AMBER)}</g>`
  );
}

export function pipSvg(state = "idle", size = 84) {
  if (!PIP_STATES.includes(state)) state = "idle";
  const body =
    rect(7, 0, 2, 1, AMBER) + rect(7, 1, 2, 1, LIMB) + rect(3, 2, 10, 8, HEAD) + rect(4, 3, 8, 6, FACE) +
    eyes(state) + mouth(state) + rect(4, 10, 8, 4, HEAD) + rect(7, 11, 2, 1, AMBER) + rect(2, 10, 2, 3, LIMB) +
    rightArm(state) + rect(5, 14, 2, 2, LIMB) + rect(9, 14, 2, 2, LIMB) + thoughtMark(state);
  const inner = state === "talking" ? `<g class="pip-bob">${body}</g>` : body;
  const height = Math.round((size * 18) / 20);
  return (
    `<svg class="pip pip-${state}" data-state="${state}" viewBox="0 0 20 18" width="${size}" height="${height}" ` +
    `shape-rendering="crispEdges" role="img" aria-label="${LABELS[state]}"><g transform="translate(1,1)">${inner}</g></svg>`
  );
}
