// Component tests for the chat UI's pure modules. Run: node --test tests/js
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { badgeLabel, claimFor, groupNumbers, renderMarkdown, stripPartialCitation } from "../../waypoint/api/static/js/citations.js";
import { PIP_STATES, pipSvg } from "../../waypoint/api/static/js/pip.js";
import { initialStream, parseSSE, reduce, stepsSummary } from "../../waypoint/api/static/js/stream.js";

const run = (events) => events.reduce((s, [name, data]) => reduce(s, name, data), initialStream());

test("Pip: thinking from the first status event until the first token, talking until final, then idle", () => {
  let s = initialStream();
  assert.equal(s.pip, "idle");
  s = reduce(s, "status", { step: "route", label: "Understanding your question", state: "running" });
  assert.equal(s.pip, "thinking");
  s = reduce(s, "status", { step: "search", label: "Searching", state: "running" });
  assert.equal(s.pip, "thinking");
  s = reduce(s, "sources", [{ n: 1, chunk_id: "cal:a#b" }]);
  assert.equal(s.pip, "thinking"); // sources arrive before the text, Pip keeps thinking
  s = reduce(s, "token", { text: "The " });
  assert.equal(s.pip, "talking");
  s = reduce(s, "status", { step: "late", label: "x", state: "done" });
  assert.equal(s.pip, "talking"); // a late status never moves Pip back to thinking
  s = reduce(s, "final", { answer: "The answer", metrics: { total_ms: 1000 } });
  assert.equal(s.pip, "idle");
  assert.equal(s.text, "The answer");
});

test("Pip: an error returns to idle", () => {
  assert.equal(run([["status", { step: "a", label: "a", state: "running" }], ["error", { message: "boom" }]]).pip, "idle");
});

test("stream reducer: a step is updated in place by id, not duplicated", () => {
  const s = run([
    ["status", { step: "get_course", id: "c1", label: "Looking up MAT224H5…", state: "running" }],
    ["status", { step: "get_course", id: "c2", label: "Looking up MAT223H5…", state: "running" }],
    ["status", { step: "get_course", id: "c1", label: "Looked up MAT224H5", state: "done", ms: 900 }],
  ]);
  assert.equal(s.steps.length, 2);
  assert.deepEqual([s.steps[0].state, s.steps[0].ms, s.steps[1].state], ["done", 900, "running"]);
});

test("stream reducer: sources are kept as soon as they arrive; tokens accumulate", () => {
  const s = run([["sources", [{ n: 1 }, { n: 2 }]], ["token", { text: "a" }], ["token", { text: "b" }]]);
  assert.equal(s.sources.length, 2);
  assert.equal(s.text, "ab");
});

test("stepsSummary: 'N steps · X s'", () => {
  const s = run([
    ["status", { step: "a", id: "1", label: "a", state: "done" }],
    ["status", { step: "b", id: "2", label: "b", state: "done" }],
    ["done", { total_ms: 9700 }],
  ]);
  assert.equal(stepsSummary(s), "2 steps · 9.7 s");
});

test("parseSSE: handles events split across chunks, and keeps the unfinished rest", () => {
  const first = parseSSE('event: token\ndata: {"text":"Hi"}\n\nevent: sta');
  assert.deepEqual(first.events, [{ name: "token", data: { text: "Hi" } }]);
  const second = parseSSE(first.rest + 'tus\ndata: {"label":"x"}\n\n');
  assert.deepEqual(second.events, [{ name: "status", data: { label: "x" } }]);
  assert.equal(second.rest, "");
});

test("Pip sprite: every state renders, with the right frames and labels", () => {
  for (const state of PIP_STATES) {
    const svg = pipSvg(state, 84);
    assert.match(svg, new RegExp(`data-state="${state}"`));
    assert.match(svg, /shape-rendering="crispEdges"/);
    assert.match(svg, /viewBox="0 0 20 18"/);
  }
  assert.match(pipSvg("thinking"), /pip-fa/); // scratching arm frames
  assert.match(pipSvg("talking"), /pip-ma/); // mouth frames
  assert.match(pipSvg("talking"), /pip-bob/);
  assert.match(pipSvg("waving"), /pip-fa/);
  assert.match(pipSvg("waving"), /pip-ma/);
  assert.match(pipSvg("idle"), /pip-blink/);
  assert.doesNotMatch(pipSvg("idle"), /pip-fa|pip-ma/);
  assert.match(pipSvg("nonsense"), /data-state="idle"/); // unknown state falls back to idle
  assert.match(pipSvg("idle", 120), /width="120" height="108"/);
});

test("reduced motion: the stylesheet stops every Pip animation and keeps frame A", () => {
  const css = readFileSync(new URL("../../waypoint/api/static/style.css", import.meta.url), "utf8");
  const block = css.slice(css.indexOf("prefers-reduced-motion"));
  for (const cls of [".pip-fa", ".pip-fb", ".pip-ma", ".pip-mb", ".pip-bob", ".pip-blink"]) assert.ok(block.includes(cls), cls);
  assert.match(block, /animation:\s*none/);
});

test("groupNumbers / badgeLabel: consecutive citations collapse into a range", () => {
  assert.deepEqual(groupNumbers([2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 1]), [[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]]);
  assert.deepEqual(groupNumbers([1, 2, 3, 5, 7, 8, 5]), [[1, 2, 3], [5], [7, 8]]);
  assert.equal(badgeLabel([2, 3, 4, 11].slice(0, 3)), "2–4");
  assert.equal(badgeLabel([5]), "5");
});

test("renderMarkdown: a run of adjacent citations becomes ONE badge button", () => {
  const ids = Array.from({ length: 10 }, (_, i) => `cal:m#c${i + 2}`);
  const numberOf = new Map(ids.map((id, i) => [id, i + 2]));
  numberOf.set("cal:m#c1", 1);
  const html = renderMarkdown(`Unlocks 10 courses ${ids.map((i) => `[${i}]`).join("")}. Prereqs [cal:m#c1]`, numberOf);
  assert.equal((html.match(/<button/g) || []).length, 2);
  assert.match(html, />2–11</);
  assert.match(html, /data-source="2"/);
  assert.match(html, />1</);
});

test("renderMarkdown: escapes HTML, drops unknown ids, hides a half-streamed marker", () => {
  const html = renderMarkdown("<script>alert(1)</script> **bold** [cal:x#y] [cal:ab", new Map());
  assert.ok(!html.includes("<script>"));
  assert.match(html, /<strong>bold<\/strong>/);
  assert.ok(!html.includes("cal:"));
  assert.equal(stripPartialCitation("text [ca"), "text ");
  assert.equal(stripPartialCitation("text [c"), "text ");
  assert.equal(stripPartialCitation("keep [1]"), "keep [1]");
});

test("renderMarkdown: bullet lists", () => {
  assert.match(renderMarkdown("* one\n* two", new Map()), /<ul><li>one<\/li><li>two<\/li><\/ul>/);
});

test("claimFor: the answer line that cites the chunk, cleaned", () => {
  const answer = "Intro line\n* **MAT223H5** is a prerequisite [cal:m#a][cal:m#b]\nOther [cal:m#c]";
  assert.equal(claimFor(answer, "cal:m#b"), "MAT223H5 is a prerequisite");
  assert.equal(claimFor(answer, "cal:m#zzz"), "");
});

// ---- V3: course map (level bands, prerequisite-depth sub-columns, path highlight), chat bits ----
import { HIGHLIGHT, NODE_H, NODE_W, cardKind, edgePath, findCourses, layoutMap, orBadges, pathInto, setStatus, subjectLabel } from "../../waypoint/api/static/js/map.js";
import { coverageText, sourceChip } from "../../waypoint/api/static/js/chat-bits.js";
import { linkCourseCodes } from "../../waypoint/api/static/js/citations.js";

const node = (code, band, sub, extra = {}) => ({ code, title: `Title ${code}`, band, sub, subject: code.slice(0, 3), ghost: false, status: null, ...extra });
const NODES = [
  node("MAT135H5", 1, 0),
  node("MAT136H5", 1, 1),
  node("MAT223H5", 2, 0),
  node("MAT224H5", 2, 1),
  node("MAT299H5", 2, 0),
  node("MAT301H5", 3, 0),
  node("CSC110Y5", 1, 0, { ghost: true, subject: "CSC" }),
];
const EDGES = [
  { from: "MAT135H5", to: "MAT136H5", kind: "one_of", group: 1 },
  { from: "MAT223H5", to: "MAT224H5", kind: "all", group: 0 },
  { from: "MAT224H5", to: "MAT301H5", kind: "all", group: 0 },
  { from: "MAT136H5", to: "MAT301H5", kind: "one_of", group: 1 },
  { from: "CSC110Y5", to: "MAT224H5", kind: "one_of", group: 2 },
];

test("layoutMap: ghosts first, then level bands; sub-columns left to right inside a band", () => {
  const { positions, headers } = layoutMap(NODES);
  assert.deepEqual(headers.map((h) => h.id), ["ghost", "band1", "band2", "band3"]);
  const x = (c) => positions.get(c).x;
  assert.ok(x("CSC110Y5") < x("MAT135H5") && x("MAT135H5") < x("MAT136H5") && x("MAT136H5") < x("MAT223H5"));
  assert.ok(x("MAT223H5") < x("MAT224H5") && x("MAT224H5") < x("MAT301H5"));
  assert.equal(x("MAT223H5"), x("MAT299H5")); // no prerequisite in the band: same first sub-column
  assert.ok(positions.get("MAT223H5").y < positions.get("MAT299H5").y); // rows sorted by code
  assert.equal(headers.find((h) => h.id === "band2").count, 3);
});

test("layoutMap: an empty band gets no column and an empty map has a size", () => {
  const { headers, width, height } = layoutMap([node("MAT135H5", 1, 0), node("MAT401H5", 4, 0)]);
  assert.deepEqual(headers.map((h) => h.id), ["band1", "band4"]);
  assert.ok(width > 0 && height > 0);
  assert.deepEqual(layoutMap([]).headers, []);
});

test("edgePath: a curve to the right, a loop inside one sub-column, a reverse curve for a ghost on the left", () => {
  assert.match(edgePath({ x: 20, y: 40 }, { x: 210, y: 104 }), new RegExp(`^M${20 + NODE_W} ${40 + NODE_H / 2}C`));
  assert.match(edgePath({ x: 20, y: 40 }, { x: 20, y: 104 }), /^M170 65C210 65 210 129 170 129$/);
  assert.match(edgePath({ x: 400, y: 40 }, { x: 20, y: 40 }), /^M400 65C/);
});

test("pathInto: every prerequisite path into a course, and only those edges", () => {
  const path = pathInto(EDGES, "MAT301H5");
  assert.deepEqual([...path.nodes].sort(), ["CSC110Y5", "MAT135H5", "MAT136H5", "MAT223H5", "MAT224H5", "MAT301H5"]);
  assert.equal(path.edges.size, 5);
  const small = pathInto(EDGES, "MAT136H5");
  assert.deepEqual([...small.nodes].sort(), ["MAT135H5", "MAT136H5"]);
  assert.deepEqual([...small.edges], ["MAT135H5>MAT136H5"]);
  assert.deepEqual([...pathInto(EDGES, "MAT299H5").nodes], ["MAT299H5"]);
});

test("pathInto survives a cycle in the data", () => {
  const loop = [{ from: "A", to: "B" }, { from: "B", to: "A" }];
  assert.deepEqual([...pathInto(loop, "A").nodes].sort(), ["A", "B"]);
});

test("orBadges: one badge per `or` group, at the midpoint of its first edge", () => {
  const { positions } = layoutMap(NODES);
  const badges = orBadges(EDGES, positions);
  assert.deepEqual(badges.map((b) => b.id).sort(), ["MAT136H5#1", "MAT224H5#2", "MAT301H5#1"]);
  assert.equal(orBadges(EDGES.filter((e) => e.kind === "all"), positions).length, 0);
});

test("cardKind / setStatus: choosing a status recolours the card at once", () => {
  assert.equal(cardKind(NODES[0]), "other");
  assert.equal(cardKind(NODES[6]), "ghost");
  const changed = setStatus(NODES, "MAT135H5", "done");
  assert.equal(cardKind(changed[0]), "taken");
  assert.equal(cardKind(setStatus(changed, "MAT135H5", "now")[0]), "now");
  assert.equal(cardKind(setStatus(changed, "MAT135H5", "planned")[0]), "planned");
  assert.equal(cardKind(setStatus(changed, "MAT135H5", null)[0]), "other");
  assert.equal(NODES[0].status, null); // not mutated
});

test("findCourses / subjectLabel: code prefix or title words; full name plus code", () => {
  assert.deepEqual(findCourses(NODES, "mat22").map((n) => n.code), ["MAT223H5", "MAT224H5"]);
  assert.deepEqual(findCourses(NODES, "title mat301").map((n) => n.code), ["MAT301H5"]);
  assert.deepEqual(findCourses(NODES, "csc"), []); // ghosts aren't search results
  assert.equal(subjectLabel({ code: "ANT", name: "Anthropology" }), "Anthropology \u00b7 ANT");
  assert.equal(subjectLabel({ code: "ANT", name: "" }), "ANT");
  assert.equal(HIGHLIGHT, "#F59E0B");
});

test("coverageText: says exactly what is loaded and what is not", () => {
  const coverage = { campuses: [{ name: "UTM (Mississauga)", courses: 59, subjects: ["MAT"] }, { name: "UTSC (Scarborough)", courses: 0, subjects: [] }] };
  assert.equal(coverageText(coverage), "Waypoint has UTM (Mississauga): 59 courses in MAT. UTSC (Scarborough) aren't loaded yet.");
  assert.match(coverageText({ campuses: [] }), /no calendar data/);
});

test("sourceChip: a compact chip with its number and the course code (or title)", () => {
  assert.match(sourceChip({ n: 2, code: "MAT224H5", title: "Linear Algebra II" }), /data-source="2"><span class="n">2<\/span>MAT224H5<\/button>/);
  assert.match(sourceChip({ n: 1, code: "", title: "Degree <b>requirements</b>" }), /Degree &lt;b&gt;/);
});

test("linkCourseCodes: links codes in text, never inside tags", () => {
  const html = linkCourseCodes('<p>Take MAT224H5 <button data-x="MAT101H5">1</button></p>');
  assert.match(html, /<a class="course-link" href="#\/course\/MAT224H5">MAT224H5<\/a>/);
  assert.ok(html.includes('data-x="MAT101H5"'));
  assert.ok(!html.includes('data-x="<a'));
});

test("renderMarkdown links course codes", () => {
  assert.match(renderMarkdown("**MAT224H5** needs MAT223H5", new Map()), /href="#\/course\/MAT223H5"/);
});

// ---- Stage 5: history grouping ----
import { dateGroup, groupTurns } from "../../waypoint/api/static/js/history-view.js";

test("dateGroup: today, yesterday, this week, earlier", () => {
  const now = new Date(2026, 10, 14, 15, 0);
  assert.equal(dateGroup(new Date(2026, 10, 14, 1, 0).toISOString(), now), "TODAY");
  assert.equal(dateGroup(new Date(2026, 10, 13, 23, 0).toISOString(), now), "YESTERDAY");
  assert.equal(dateGroup(new Date(2026, 10, 10, 12, 0).toISOString(), now), "EARLIER THIS WEEK");
  assert.equal(dateGroup(new Date(2026, 9, 1, 12, 0).toISOString(), now), "EARLIER");
});

test("groupTurns: pinned answers come first, then the rest by date", () => {
  const now = new Date(2026, 10, 14, 15, 0);
  const t = (id, d, pinned = false) => ({ id, ts: new Date(2026, 10, d, 12, 0).toISOString(), pinned });
  const groups = groupTurns([t("a", 14), t("b", 14, true), t("c", 13), t("d", 1)], now);
  assert.deepEqual(groups.map((g) => [g.label, g.turns.map((x) => x.id)]), [["PINNED", ["b"]], ["TODAY", ["a"]], ["YESTERDAY", ["c"]], ["EARLIER", ["d"]]]);
});
