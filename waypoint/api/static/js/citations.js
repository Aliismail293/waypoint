// Answer text -> safe HTML, with calendar citations shown as numbered badges. Adjacent citations are grouped:
// [2][3][4]...[11] becomes one badge "2–11" instead of ten (DESIGN_SPEC section 5).

export function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const ID = "cal:[a-z0-9-]+#[a-z0-9-]+";
const RUN = new RegExp(`(?:\\[${ID}\\]\\s*)+`, "g");
const ONE = new RegExp(`\\[(${ID})\\]`, "g");

// [1,2,3,5,7,8] -> [[1,2,3],[5],[7,8]]: runs of consecutive numbers.
export function groupNumbers(numbers) {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b);
  const groups = [];
  for (const n of sorted) {
    const last = groups[groups.length - 1];
    if (last && n === last[last.length - 1] + 1) last.push(n);
    else groups.push([n]);
  }
  return groups;
}

export const badgeLabel = (group) => (group.length > 1 ? `${group[0]}–${group[group.length - 1]}` : String(group[0]));

function badges(run, numberOf) {
  const numbers = [...run.matchAll(ONE)].map((m) => numberOf.get(m[1])).filter(Boolean);
  if (!numbers.length) return "";
  return (
    '<span class="cites">' +
    groupNumbers(numbers)
      .map((g) => {
        const aria = g.length > 1 ? `Open sources ${g[0]} to ${g[g.length - 1]}` : `Open source ${g[0]}`;
        return `<button type="button" class="cite" data-source="${g[0]}" aria-label="${aria}">${badgeLabel(g)}</button>`;
      })
      .join("") +
    "</span>"
  );
}

// A streamed answer can stop mid-marker ("[cal:abc#d"); hide the unfinished tail until it completes.
export function stripPartialCitation(text) {
  return text.replace(/\[c(?:a(?:l(?::[^\]]*)?)?)?$/, "");
}

// Minimal markdown: text is escaped first, then bold, bullets and headings become tags.
export function renderMarkdown(text, numberOf = new Map()) {
  const html = [];
  let inList = false;
  for (const raw of escapeHtml(stripPartialCitation(text)).split("\n")) {
    const line = raw.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(RUN, (run) => badges(run, numberOf));
    const bullet = line.match(/^\s*[*-]\s+(.*)$/);
    if (bullet) {
      if (!inList) { html.push("<ul>"); inList = true; }
      html.push(`<li>${bullet[1]}</li>`);
      continue;
    }
    if (inList) { html.push("</ul>"); inList = false; }
    const heading = line.match(/^#{1,4}\s+(.*)$/);
    if (heading) html.push(`<h4>${heading[1]}</h4>`);
    else if (line.trim()) html.push(`<p>${line}</p>`);
  }
  if (inList) html.push("</ul>");
  return html.join("");
}

// The sentence/line of the answer that cites `chunkId`, with markers and markdown removed: sent to the Source reader
// so it can highlight the supporting line. (Answer text only; never the student's question.)
export function claimFor(answer, chunkId) {
  const marker = `[${chunkId}]`;
  const line = answer.split("\n").find((l) => l.includes(marker)) || "";
  return line
    .replace(new RegExp(`\\[${ID}\\]`, "g"), "")
    .replace(/\*\*/g, "")
    .replace(/^\s*[*#-]+\s*/, "")
    .trim()
    .slice(0, 400);
}

export const COURSE_CODE = /\b[A-Z]{3}\d{3}[HY]\d\b/g;
