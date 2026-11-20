// When the account list cannot be loaded (signed out, server down), the sidebar says so; it must not claim that no
// accounts are linked. A request that never reaches the app (an expired sign-in is redirected to Cloudflare's login,
// which fetch reports as a bare network error) gets a message that says what to do.
"use strict";
const { load } = require("./dom_sandbox");
const { t: { S, loadAccounts, api }, els, pending, settle } = load(["S", "loadAccounts", "api"]);
const fail = (why) => { console.log(`ERROR-STATES FAIL ${why}`); process.exit(1); };
(async () => {
  await settle();
  const p = loadAccounts();
  const req = pending.splice(0).find((x) => x.url === "/api/accounts");
  req.resolve({ ok: false, status: 502, json: async () => ({ error: "OperationalError: database <down>" }) });
  await p;
  let html = String(els["#accounts"].innerHTML);
  if (html.includes("No linked accounts")) fail(`a failed load reads as no accounts: ${html.slice(-200)}`);
  if (!html.includes("Could not load your accounts") || !html.includes("database &lt;down&gt;")) fail(`error not shown (escaped): ${html.slice(-300)}`);
  const p2 = loadAccounts();                                   // a real empty answer still says so
  pending.splice(0).find((x) => x.url === "/api/accounts").resolve({ ok: true, status: 200, json: async () => [] });
  await p2;
  html = String(els["#accounts"].innerHTML);
  if (!html.includes("No linked accounts")) fail("an empty account list no longer says how to add one");
  const p3 = api("/api/messages");
  pending.splice(0)[0].resolve(Promise.reject(new TypeError("Failed to fetch")));
  let msg = "";
  try { await p3; } catch (e) { msg = e.message; }
  if (!/sign-in|reload/i.test(msg)) fail(`network error not explained: ${msg}`);
  // the proxy gave up waiting on a long action (Cloudflare 524, an HTML page): it may still be running
  const p4 = api("/api/sender", { addr: "a@b.c", action: "delete" });
  pending.splice(0)[0].resolve({ ok: false, status: 524, json: async () => { throw new SyntaxError("not JSON"); } });
  msg = "";
  try { await p4; } catch (e) { msg = e.message; }
  if (!/may still be running/.test(msg) || !/Undo/.test(msg)) fail(`a 524 is not explained: ${msg}`);
  await settle();
  console.log("ERROR-STATES OK");
  process.exit(0);
})().catch((e) => { console.log("ERROR-STATES ERROR " + e.stack); process.exit(2); });
