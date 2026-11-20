// Times from the server are shown on the viewer's clock (run with TZ=America/New_York by test_analytics.py).
"use strict";
const assert = require("assert");
const { load } = require("./dom_sandbox");

const { t } = load(["stamp"]);
assert.strictEqual(t.stamp("2026-11-19T03:05:00+00:00"), "2026-11-18 22:05");     // the evening before, in New York
assert.strictEqual(t.stamp("2026-11-19T04:05:00+01:00"), "2026-11-18 22:05");     // whatever offset the server used
assert.strictEqual(t.stamp("2026-07-01T12:00:00Z"), "2026-07-01 08:00");          // summer time
assert.strictEqual(t.stamp(""), "");
assert.strictEqual(t.stamp("not a date"), "not a date");
console.log("LOCAL-TIMES OK");
