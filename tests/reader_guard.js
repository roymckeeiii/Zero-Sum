// The reader shows the message last opened: an older message that answers after a newer one (j j pressed quickly)
// must not land in the reader, where Archive / Delete / e / # act on the newer selection, not on what is shown.
"use strict";
const { load } = require("./dom_sandbox");
const { t: { S, open }, els, pending, ok, settle } = load(["S", "open"]);
const msg = (id) => ({ id, threadId: id, kind: "text", body: `body of ${id}`, from: "x", to: "y", subject: `subject ${id}`, date: "", email: "a@x.com" });
(async () => {
  await settle();
  const item = (id) => ({ id, threadId: id, account: "A", email: "a@x.com", ts: 1, labels: [], from: "x", fromAddr: "x", subject: id, snippet: "", unread: false });
  S.items = [item("aaaaaa1"), item("bbbbbb2")];
  const first = open(0), second = open(1);           // j, j: the second message is the one selected
  const reads = pending.splice(0).filter((p) => p.url.includes("/api/message?"));
  if (reads.length !== 2) throw new Error(`expected 2 message reads, got ${reads.length}`);
  reads[1].resolve(ok(msg("bbbbbb2"))); await second;
  reads[0].resolve(ok(msg("aaaaaa1"))); await first; // the older answer comes last
  const shown = String(els["#reader"].innerHTML);
  const good = S.sel === 1 && shown.includes("subject bbbbbb2") && !shown.includes("subject aaaaaa1");
  console.log(good ? "READER-GUARD OK" : `READER-GUARD FAIL sel=${S.sel} shown=${shown.slice(0, 120)}`);
  process.exit(good ? 0 : 1);
})().catch((e) => { console.log("READER-GUARD ERROR " + e.stack); process.exit(2); });
