// Pure helpers of the Programs tab.
import assert from "node:assert/strict";
import test from "node:test";

import { STATUS, summaryText } from "../../waypoint/api/static/js/programs-view.js";

test("summaryText: lists only the statuses that occur, in a fixed order", () => {
  assert.equal(summaryText({ done: 2, in_progress: 0, planned: 1, missing: 3, check: 1 }), "2 done · 1 in your plan · 3 missing · 1 check the calendar");
  assert.equal(summaryText({}), "no requirements parsed");
});

test("every checklist status has a label and a colour kind", () => {
  for (const key of ["done", "in_progress", "planned", "missing", "check"]) assert.equal(STATUS[key].length, 2);
});
