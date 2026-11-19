// Rules tab: a rule that forwards, sends or calls a webhook shows where to, and copying those actions to an account
// takes a second, confirmed OK ("allow sending"); without it nothing is sent to the server.
"use strict";
const { load } = require("./dom_sandbox");
const { t: { loadRules }, els, pending, confirms, settle, tick, ok } = load(["loadRules"]);
const fail = (m) => { console.log("RULE-OUTBOUND FAIL " + m); process.exit(1); };
(async () => {
  await settle();
  const p = loadRules(); await tick();
  const state = { master: { id: "acc1", email: "m@x.com" }, accounts: [{ id: "acc2", email: "b@x.com" }], auto: false, log: [],
    rules: [{ id: "m1", name: "Invoices", enabled: true, actions: ["FORWARD"], cells: { acc2: "pending" },
      outbound: ["forward to accountant@example.com"], outboundAllowed: { acc2: false } }] };
  for (let i = 0; i < 3; i++) { for (const r of pending.splice(0)) r.resolve(ok(r.url === "/api/rules" ? state : { backups: [] })); await tick(); }
  await p;
  const html = String(els["#r-matrix"].innerHTML);
  if (!html.includes("forward to accountant@example.com")) fail("the destination must be shown");
  if (!/data-out="1"[^>]*>\s*allow sending/.test(html.replace(/aria-label="[^"]*"/g, ""))) fail("an allow-sending box must be offered");
  const box = { checked: true, dataset: { out: "1", r: "m1", a: "acc2" } };
  confirms.answer = false;
  await els["#r-matrix"].onchange({ target: { closest: () => box } }); await tick();
  if (!confirms.length || !confirms[0].includes("accountant@example.com")) fail("allowing must ask, naming the destination");
  if (box.checked || pending.some((r) => r.url === "/api/rules/include")) fail("Cancel must not allow sending");
  confirms.answer = true;
  box.checked = true;
  const done = els["#r-matrix"].onchange({ target: { closest: () => box } }); await tick();
  const inc = pending.find((r) => r.url === "/api/rules/include");
  if (!inc || JSON.stringify(inc.body) !== JSON.stringify({ rule: "m1", account: "acc2", include: true, outbound: true })) fail(`sent ${JSON.stringify(inc && inc.body)}`);
  for (let i = 0; i < 4; i++) { for (const r of pending.splice(0)) r.resolve(ok(r.url === "/api/rules" ? state : { ok: true, backups: [] })); await tick(); }
  await done;
  console.log("RULE-OUTBOUND OK");
})().catch((e) => { console.log("RULE-OUTBOUND ERROR " + e.stack); process.exit(2); });
