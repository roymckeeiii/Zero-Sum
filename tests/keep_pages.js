// The inbox refreshes itself every 2 minutes. Once "Load more" has been used, that refresh must not throw the extra
// pages away (and with them the selected message); it still refreshes the account counts. A fresh search or the
// refresh key starts from the first page again, and the timer then refreshes the list as before.
"use strict";
const { load } = require("./dom_sandbox");
const { t: { S, loadList }, pending, intervals, ok, settle } = load(["S", "loadList"]);
const it = (id, ts) => ({ id, threadId: id, account: "A", email: "a@x.com", ts, labels: [], from: "x", fromAddr: "x", subject: id, snippet: "" });
const fail = (why) => { console.log(`KEEP-PAGES FAIL ${why}`); process.exit(1); };
const lists = () => pending.filter((p) => p.url.startsWith("/api/messages"));
(async () => {
  await settle();
  if (intervals.length !== 1) fail(`expected one timer, got ${intervals.length}`);
  S.tab = "inbox";
  const first = loadList();
  pending.splice(0).find((p) => p.url.startsWith("/api/messages")).resolve(ok({ items: [it("m1", 3), it("m2", 2)], errors: [], next: "P2" }));
  await first;
  const more = loadList(true, S.next);
  pending.splice(0).find((p) => p.url.startsWith("/api/messages")).resolve(ok({ items: [it("m3", 1)], errors: [], next: "" }));
  await more;
  S.sel = 2;
  if (S.items.length !== 3) fail(`load more did not append (${S.items.length})`);
  intervals[0]();                                     // two minutes later
  if (lists().length) fail("the timer reloaded the list and dropped the loaded pages");
  if (!pending.some((p) => p.url === "/api/accounts")) fail("the timer stopped refreshing the account counts");
  await settle();
  if (S.items.length !== 3 || S.sel !== 2) fail(`pages or selection lost: ${S.items.length} items, sel ${S.sel}`);
  const fresh = loadList();                           // a new search: back to page one
  pending.splice(0).find((p) => p.url.startsWith("/api/messages")).resolve(ok({ items: [it("m1", 3)], errors: [], next: "P2" }));
  await fresh;
  intervals[0]();
  if (!lists().length) fail("on the first page the timer no longer refreshes the list");
  await settle();
  console.log("KEEP-PAGES OK");
  process.exit(0);
})().catch((e) => { console.log("KEEP-PAGES ERROR " + e.stack); process.exit(2); });
