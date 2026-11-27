// A Quarantine group whose From has no plain address cannot be acted on by sender: no sender buttons, a hint instead.
"use strict";
const { load } = require("./dom_sandbox");
const { t: { S, renderQuarantine }, els, settle } = load(["S", "renderQuarantine"]);
const fail = (why) => { console.log(`QGROUPS FAIL ${why}`); process.exit(1); };
(async () => {
  await settle();
  const item = { account: "A", email: "a@x.com", id: "aaaa01", subject: "s", snippet: "", ts: 1 };
  S.quar = { label: "Quarantine", total: 2, shown: 2, errors: [], senders: [
    { addr: "", name: "(no sender)", count: 1, accounts: [{ id: "A", email: "a@x.com" }], items: [item], actionable: false },
    { addr: "deals@shop.com", name: "Deals", count: 1, accounts: [{ id: "A", email: "a@x.com" }], items: [item], actionable: true }] };
  renderQuarantine();
  const html = String(els["#q-body"].innerHTML);
  const [first, second] = html.split('<div class="qgroup">').slice(1);
  if (first.includes('data-qa=')) fail("sender buttons shown for a group without an address");
  if (!first.includes("tick its emails")) fail("no hint for the group without an address");
  if (!second.includes('data-qa="release"') || !second.includes('data-qa="delete"')) fail("sender buttons missing for a plain address");
  console.log("QGROUPS OK");
  process.exit(0);
})().catch((e) => { console.log("QGROUPS ERROR " + e.stack); process.exit(2); });
