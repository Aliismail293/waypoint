// Small pure helpers for the chat view, kept apart from the DOM code so they can be unit-tested.
import { escapeHtml } from "./citations.js";

// An honest one-line description of the data Waypoint actually has loaded, from the server.
export function coverageText(coverage) {
  const loaded = (coverage?.campuses || []).filter((c) => c.courses > 0);
  if (!loaded.length) return "Waypoint has no calendar data loaded yet.";
  const parts = loaded.map((c) => `${c.name}: ${c.courses} courses in ${c.subjects.join(", ")}`);
  const blocked = (coverage.campuses || []).filter((c) => c.courses === 0).map((c) => c.name);
  const missing = blocked.length ? ` ${blocked.join(" and ")} aren't loaded yet.` : "";
  return `Waypoint has ${parts.join("; ")}.${missing}`;
}


export function sourceChip(s) {
  return `<button type="button" class="source-chip" data-source="${s.n}"><span class="n">${s.n}</span>${escapeHtml(s.code || s.title)}</button>`;
}

