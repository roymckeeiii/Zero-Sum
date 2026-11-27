// Archive in the Inbox offers Undo, as Delete does: the toast's button puts the thread back in the inbox (action
// "inbox") in the same account, and the list is reloaded so it shows up again. Only mail that was in the inbox.
"use strict";
const { load } = require("./dom_sandbox");
const { t: { S, act }, els, pending, ok, settle } = load(["S", "act"]);
(async () => {
  await settle();
  const item = (id, account) => ({ id, threadId: `t${id}`, account, email: `${account}@x.com`, ts: 1, labels: [], from: "x", fromAddr: "x",
    subject: `<b>${id}</b>`, snippet: "", unread: false, folder: "Inbox" });
  S.items = [item("aaaaaa1", "A"), item("bbbbbb2", "B")];
  S.sel = -1;
  const done = act(1, "archive");
  const req = pending.splice(0).find((p) => p.url === "/api/action");
  if (!req || req.body.action !== "archive") throw new Error("archive was not sent");
  req.resolve(ok({ ok: true })); await done; await settle();
  const fail = (why) => { console.log(`INBOX-UNDO FAIL ${why}`); process.exit(1); };
  if (S.items.length !== 1) fail(`archived thread still listed (${S.items.length})`);
  const toast = els["#toast"], text = String(toast.innerHTML || "");
  if (!/Archived/.test(text) || !text.includes("Undo")) fail(`no undo offered: ${text}`);
  if (text.includes("<b>bbbbbb2</b>")) fail("subject not escaped in the toast");
  const btn = els["#toast button"];
  if (typeof btn.onclick !== "function") fail("undo button not wired");
  btn.onclick();
  const undo = pending.splice(0).find((p) => p.url === "/api/action");
  if (!undo) fail("undo sent nothing");
  const b = undo.body;
  if (b.action !== "inbox" || b.account !== "B" || b.threadId !== "tbbbbbb2") fail(`wrong undo ${JSON.stringify(b)}`);
  undo.resolve(ok({ ok: true })); await settle();
  // a search result that was already archived: archiving it changes nothing, so no undo (it would add INBOX)
  S.items = [{ ...item("cccccc3", "A"), folder: "Archived" }]; S.sel = -1;
  const again = act(0, "archive");
  pending.splice(0).find((p) => p.url === "/api/action").resolve(ok({ ok: true })); await again; await settle();
  if (String(els["#toast"].innerHTML).includes("Undo")) fail("undo offered for mail that was not in the inbox");
  // Delete moves only that message (another message of the same thread stays listed), and its Undo is the journal
  // entry the server recorded: exactly that message comes out of Trash
  S.items = [item("dddddd4", "A"), { ...item("eeeeee5", "A"), threadId: "tdddddd4" }]; S.sel = -1;
  const del = act(0, "delete");
  const dreq = pending.splice(0).find((p) => p.url === "/api/action");
  if (!dreq || dreq.body.action !== "delete" || dreq.body.id !== "dddddd4") fail(`delete not sent for the message ${JSON.stringify(dreq && dreq.body)}`);
  dreq.resolve(ok({ ok: true, count: 1, undo: 42 })); await del; await settle();
  if (S.items.length !== 1 || S.items[0].id !== "eeeeee5") fail(`delete removed the wrong rows: ${S.items.map((x) => x.id)}`);
  if (!String(els["#toast"].innerHTML).includes("Undo")) fail("no undo offered for a delete");
  els["#toast button"].onclick();
  const dundo = pending.splice(0).find((p) => p.url === "/api/undo" || p.url === "/api/action");
  if (!dundo || dundo.url !== "/api/undo" || dundo.body.id !== 42) fail(`delete undo was not the journal entry ${JSON.stringify(dundo)}`);
  dundo.resolve(ok({ ok: true, restored: 1 })); await settle();
  console.log("INBOX-UNDO OK");
  process.exit(0);
})().catch((e) => { console.log("INBOX-UNDO ERROR " + e.stack); process.exit(2); });
