// Archiving or deleting a row other than the open one keeps the open message open and marks nothing else read;
// removing the open one opens the message that followed it.
"use strict";
const { load } = require("./dom_sandbox");
const { t: { S, act }, pending, ok, settle } = load(["S", "act"]);
const fail = (why) => { console.log(`SELECTION FAIL ${why}`); process.exit(1); };
const item = (n) => ({ id: `aaaa0${n}`, threadId: `t${n}`, account: "A", email: "a@x.com", ts: 1, labels: [], from: "x",
  fromAddr: "x", subject: `s${n}`, snippet: "", unread: true, folder: "Inbox" });
const run = async (sel, row, action) => {
  S.items = [1, 2, 3, 4, 5, 6].map(item); S.sel = sel;
  const done = act(row, action);
  pending.splice(0).find((p) => p.url === "/api/action").resolve(ok({ ok: true }));
  await done;
  const sent = pending.splice(0);
  const msg = { email: "a@x.com", threadId: "t", subject: "", from: "", to: "", date: "", body: "", attachments: [] };
  for (const p of sent) p.resolve(ok(p.url.includes("/api/accounts") ? [] : p.url.startsWith("/api/message") ? msg : {}));
  await settle();
  return sent;
};
(async () => {
  await settle();
  let sent = await run(4, 1, "delete");                 // reading row 4 (t5), delete row 1 (t2)
  if (S.items[S.sel]?.threadId !== "t5") fail(`selection moved to ${S.items[S.sel]?.threadId}`);
  if (sent.some((p) => p.url.startsWith("/api/message") || (p.body && p.body.action === "read"))) fail("another message was opened or marked read");
  sent = await run(4, 4, "archive");                    // archive the open one: the next (t6) opens
  if (S.items[S.sel]?.threadId !== "t6") fail(`after archiving the open one, ${S.items[S.sel]?.threadId} is selected`);
  console.log("SELECTION OK");
  process.exit(0);
})().catch((e) => { console.log("SELECTION ERROR " + e.stack); process.exit(2); });
