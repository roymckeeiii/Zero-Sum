// The daily chart's days are calendar days in the zone the server grouped by (the viewer's), so a bar lands on the
// day the server counted it under, "today" is the viewer's today, and a daylight-saving change neither skips nor
// repeats a day. Run by test_analytics.py.
"use strict";
const assert = require("assert");
const { load } = require("./dom_sandbox");

const { t } = load(["dailyChart", "dayIn", "viewerZone"]);
const realNow = Date.now;
const at = (iso) => { Date.now = () => Date.parse(iso); };
const days = (list) => Array.from(list, (x) => x.day);   // an array of this realm, for deepStrictEqual
try {
  at("2026-11-19T03:00:00Z");   // 22:00 on the 18th in New York, already the 19th in UTC
  let c = t.dailyChart([{ day: "2026-11-18", email: "a@x.com", count: 4 }], 3, "America/New_York");
  assert.deepStrictEqual(days(c.list), ["2026-11-16", "2026-11-17", "2026-11-18"]);
  assert.strictEqual(c.list[2].total, 4);                     // tonight's mail is on today's bar, not missing
  c = t.dailyChart([{ day: "2026-11-19", email: "a@x.com", count: 2 }], 2, "UTC");
  assert.deepStrictEqual(days(c.list), ["2026-11-18", "2026-11-19"]);
  assert.strictEqual(c.list[1].total, 2);
  at("2026-11-19T12:00:00Z");
  assert.deepStrictEqual(days(t.dailyChart([], 2, "Pacific/Kiritimati").list), ["2026-11-19", "2026-11-20"]);   // UTC+14

  at("2026-03-09T04:30:00Z");   // 00:30 in New York, the night after clocks went forward (a 23-hour day)
  assert.deepStrictEqual(days(t.dailyChart([], 4, "America/New_York").list), ["2026-03-06", "2026-03-07", "2026-03-08", "2026-03-09"]);
  at("2026-11-02T04:30:00Z");   // 23:30 in New York, on the 25-hour day clocks went back
  assert.deepStrictEqual(days(t.dailyChart([], 3, "America/New_York").list), ["2026-10-30", "2026-10-31", "2026-11-01"]);

  at("2026-11-19T03:00:00Z");   // a zone the browser does not know falls back to UTC, as the server does
  assert.deepStrictEqual(days(t.dailyChart([], 1, "Not/AZone").list), ["2026-11-19"]);
  assert.strictEqual(typeof t.viewerZone(), "string");
  console.log("DAILY-CHART OK");
} finally {
  Date.now = realNow;
}
