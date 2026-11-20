// AI Cleanup texts: the empty state names the real button (or says AI is not configured), the background checkbox
// cannot be ticked without AI, and a mailbox size Gmail did not give is "unknown", not "of 0 (0%)".
"use strict";
const { load } = require("./dom_sandbox");
const { t: { loadPlans, loadProgress }, els, pending, settle, ok } = load(["loadPlans", "loadProgress"]);
const fail = (why) => { console.log(`AI-TEXTS FAIL ${why}`); process.exit(1); };
const answer = (url, body) => { const i = pending.findIndex((x) => x.url === url); if (i < 0) fail(`no request ${url}`); pending.splice(i, 1)[0].resolve(ok(body)); };
const tick = () => new Promise((r) => setTimeout(r, 0));
const progress = (ai, total) => ({ collected: 120, mailboxTotal: total, withSubject: 1, senders: 0, planned: 0, background: false,
  callsToday: 0, callCap: 400, ai, accounts: [{ email: "me@x.com", collected: 120, total }] });
(async () => {
  await settle();
  for (const ai of [true, false]) {
    const p = loadPlans();
    answer("/api/ai/progress", progress(ai, ai ? 1000 : null));
    answer("/api/ai/plans", { ai, running: { busy: false, done: 0, total: 0 }, plans: [] });
    await tick();
    answer("/api/ai/sorting", {});
    await p; await tick(); await tick();
    const body = String(els["#ai-body"].innerHTML), prog = String(els["#ai-progress"].innerHTML);
    if (/Analyze top senders/.test(body)) fail("the empty state names a button that does not exist");
    if (ai && !body.includes("✨ Analyze now")) fail(`the empty state does not name the button: ${body}`);
    if (!ai && !/not configured/.test(body)) fail(`the empty state does not say AI is not configured: ${body}`);
    const box = prog.match(/<input type="checkbox" id="ai-bg"[^>]*>/)[0];
    if (ai === /disabled/.test(box)) fail(`background checkbox disabled=${!ai} expected: ${box}`);
    if (ai && !/120<\/b> of 1,000 \(12%\)/.test(prog)) fail(`known total not shown: ${prog}`);
    if (!ai && (/120<\/b> of 0|120 of 0/.test(prog) || !/120<\/b> of unknown/.test(prog) || !/me@x.com: 120 of unknown/.test(prog))) fail(`unknown total shown as 0: ${prog}`);
  }
  console.log("AI-TEXTS OK");
  process.exit(0);
})().catch((e) => { console.log("AI-TEXTS ERROR " + e.stack); process.exit(2); });
