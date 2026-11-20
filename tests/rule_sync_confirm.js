// "Sync now" looks before it deletes: it asks for the plan, lists every delete in a confirmation, and sends only the
// keys of the deletes the user saw. Cancel sends nothing.
"use strict";
const { load } = require("./dom_sandbox");
const { els, pending, confirms, settle, tick, ok } = load(["S"]);
const plan = { applied: false, masterChanged: null, ops: [
  { op: "create", account: "b@x.com", rule: "Travel", detail: "" },
  { op: "delete", account: "b@x.com", rule: "Receipts", detail: "no longer shared with this account", key: "acc2:t1" }] };
const fail = (m) => { console.log("RULE-SYNC FAIL " + m); process.exit(1); };
async function press(answer) {
  confirms.length = 0; confirms.answer = answer;
  const btn = { disabled: false };
  const done = els["#r-sync"].onclick({ target: btn });
  await tick();
  const reads = pending.splice(0);
  const pre = reads.find((p) => p.url === "/api/rules/preview");
  if (!pre || reads.some((p) => p.url === "/api/rules/sync")) fail("Sync now must ask for the plan first, and only that");
  pre.resolve(ok(plan)); await tick(); await tick();
  const posts = pending.splice(0).filter((p) => p.url === "/api/rules/sync");
  for (const p of posts) p.resolve(ok({ ...plan, applied: true }));
  for (let i = 0; i < 4; i++) {   // the Rules tab reloads afterwards: answer it with nothing to draw
    for (const p of pending.splice(0)) p.resolve(ok(p.url === "/api/rules/backups" ? { backups: [] } : { error: "not drawn in this test" }));
    await tick();
  }
  await done;
  return posts;
}
(async () => {
  await settle();
  let posts = await press(false);
  if (confirms.length !== 1 || !confirms[0].includes("Receipts on b@x.com")) fail(`the delete must be shown: ${confirms}`);
  if (posts.length) fail("Cancel must not sync");
  posts = await press(true);
  if (posts.length !== 1 || JSON.stringify(posts[0].body) !== JSON.stringify({ confirm: ["acc2:t1"] })) fail(`sent ${JSON.stringify(posts.map((p) => p.body))}`);
  console.log("RULE-SYNC OK");
})().catch((e) => { console.log("RULE-SYNC ERROR " + e.stack); process.exit(2); });
