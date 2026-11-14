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

// ---- Stage 3: course map and course links ----
import { edgePath, layoutMap, routeEdges, routeNote, unlockableNext, visibleCodes } from "../../waypoint/api/static/js/map.js";
import { linkCourseCodes } from "../../waypoint/api/static/js/citations.js";

const node = (code, column, extra = {}) => ({ code, title: code, level: 2, in_data: true, status: null, column, ...extra });
const NODES = [
  node("A100", "before", { status: "done" }),
  node("B200", "now", { status: "now" }),
  node("C200", "lower"),
  node("D300", "upper"),
  node("E400", "upper"),
  node("F100", "lower"),
  node("X101", "lower", { in_data: false }),
];
const EDGES = [
  { from: "A100", to: "B200" },
  { from: "B200", to: "C200" },
  { from: "C200", to: "D300" },
  { from: "D300", to: "E400" },
  { from: "X101", to: "E400" },
];

test("visibleCodes: a student with courses sees them plus two hops onward; no courses shows everything in the data", () => {
  assert.deepEqual([...visibleCodes(NODES, EDGES)].sort(), ["A100", "B200", "C200", "D300"]);
  assert.ok(visibleCodes(NODES, EDGES, { showAll: true }).has("F100"));
  assert.ok(!visibleCodes(NODES, EDGES, { showAll: true }).has("X101")); // outside the data: only drawn when needed
  const empty = NODES.map((n) => ({ ...n, status: null }));
  assert.equal(visibleCodes(empty, EDGES).size, 6);
  assert.ok(visibleCodes(NODES, EDGES, { keep: "E400" }).has("E400")); // a selected course is always shown
});

test("layoutMap: columns left to right, rows sorted, headers carry counts", () => {
  const layout = layoutMap(NODES, new Set(["A100", "B200", "C200", "D300", "E400"]));
  const a = layout.positions.get("A100");
  const b = layout.positions.get("B200");
  assert.ok(a.x < b.x && b.x < layout.positions.get("C200").x && layout.positions.get("C200").x < layout.positions.get("D300").x);
  assert.ok(layout.positions.get("D300").y < layout.positions.get("E400").y);
  assert.equal(layout.headers.find((h) => h.id === "upper").count, 2);
  assert.ok(!layout.positions.has("F100"));
});

test("edgePath: a curve between columns, a loop inside one column", () => {
  assert.match(edgePath({ x: 24, y: 36 }, { x: 304, y: 108 }), /^M214 63C/);
  assert.match(edgePath({ x: 24, y: 36 }, { x: 24, y: 108 }), /^M214 63C258 63 258 135 214 135$/);
});

test("unlockableNext: only courses whose in-data prerequisites are all the student's", () => {
  assert.deepEqual([...unlockableNext(NODES, EDGES)], ["C200"]);
});

test("routeEdges / routeNote: edges follow the steps; every state has an honest note", () => {
  const route = { state: "route", steps: [{ code: "A100" }, { code: "B200" }, { code: "C200" }] };
  assert.deepEqual(routeEdges(route), [{ from: "A100", to: "B200" }, { from: "B200", to: "C200" }]);
  assert.deepEqual(routeEdges({ state: "no_route", steps: [] }), []);
  assert.match(routeNote("no_route"), /None of this course's prerequisite links/);
  assert.match(routeNote("no_links"), /no prerequisite links/);
  assert.match(routeNote("route"), /only the prerequisite links in its data/i);
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
