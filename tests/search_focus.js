// The account list refreshes after every action, after a message is opened and every two minutes. It must not redraw
// the search box: a redraw dropped the focus while the person was typing, and the rest of the word ran as shortcuts.
"use strict";
const { load } = require("./dom_sandbox");
const { t: { S, loadAccounts }, els, pending, ok, settle } = load(["S", "loadAccounts"]);
const fail = (why) => { console.log(`SEARCH-FOCUS FAIL ${why}`); process.exit(1); };
(async () => {
  await settle();
  const first = String(els["#accounts"].innerHTML || "");
  if (!first.includes('id="q"')) fail("search box not drawn");
  if (typeof els["#q"].oninput !== "function") fail("search box not wired");
  els["#accounts"].innerHTML = "UNCHANGED";   // whatever is there now is the live search box
  S.q = "semi";
  const again = loadAccounts();
  const req = pending.splice(0).find((p) => p.url.includes("/api/accounts"));
  if (!req) fail("accounts not reloaded");
  req.resolve(ok([{ id: "A", email: "a@x.com", ok: true, unread: 3 }]));
  await again; await settle();
  if (els["#accounts"].innerHTML !== "UNCHANGED") fail("the refresh redrew the search box");
  if (!String(els["#acc-links"].innerHTML).includes("a@x.com")) fail("account links not refreshed");
  console.log("SEARCH-FOCUS OK");
  process.exit(0);
})().catch((e) => { console.log("SEARCH-FOCUS ERROR " + e.stack); process.exit(2); });
