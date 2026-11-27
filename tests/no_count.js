// "AI suggest" can answer protect or keep: those move no existing mail, so the dialog does not ask the server to
// count (it refused protect with "bad sender action", shown as "Could not count"). Block still counts.
"use strict";
const { load } = require("./dom_sandbox");
const { t: { askDialog }, pending, settle } = load(["askDialog"]);
const fail = (why) => { console.log(`NO-COUNT FAIL ${why}`); process.exit(1); };
(async () => {
  await settle();
  const ai = { action: "protect", keep_words: [], reason: "", sampled: 3, model: "m", subjects: [] };
  for (const a of ["protect", "keep"]) {
    askDialog(a, { addr: "bank@x.com", name: "Bank", accounts: [{ id: "A" }] }, { ...ai, action: a });
    if (pending.splice(0).some((p) => p.url === "/api/sender/preview")) fail(`${a} asked for a count`);
  }
  askDialog("block", { addr: "spam@x.com", name: "Spam", accounts: [{ id: "A" }] }, null);
  if (!pending.splice(0).some((p) => p.url === "/api/sender/preview")) fail("block no longer counts");
  console.log("NO-COUNT OK");
  process.exit(0);
})().catch((e) => { console.log("NO-COUNT ERROR " + e.stack); process.exit(2); });
