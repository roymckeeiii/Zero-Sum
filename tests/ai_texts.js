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
  // a partly applied plan keeps its Apply button, so "run it again" is one click; an applied one only offers Review again
  const plan = (status) => ({ addr: "deals@shop.com", status, count: 9, accounts: [{ id: "a1", email: "me@x.com" }], reason: "",
    groups: [{ label: "Orders", words: ["order"], action: "keep" }], default: "archive", subjects: [], sampled: 0, mode: "words" });
  for (const [status, apply] of [["partial", true], ["applied", false], ["new", true]]) {
    const p = loadPlans();
    answer("/api/ai/progress", progress(true, 1000));
    answer("/api/ai/plans", { ai: true, running: { busy: false, done: 0, total: 0 }, plans: [plan(status)] });
    await tick();
    answer("/api/ai/sorting", {});
    await p; await tick(); await tick();
    const body = String(els["#ai-body"].innerHTML);
    if (body.includes('data-pa="apply"') !== apply) fail(`${status} plan: Apply button shown=${!apply} expected ${apply}`);
    if (status === "partial" && !/partly applied/.test(body)) fail(`partial plan not marked: ${body}`);
    // its filters are live: the skip button says it stops there, not that the plan is skipped as if never applied
    if (status === "partial" && (!/filters active/.test(body) || !/data-pa="skip"[^>]*>Stop here</.test(body)))
      fail(`partial plan does not say its filters stay: ${body}`);
    if (status === "new" && !/data-pa="skip"[^>]*>Skip</.test(body)) fail(`new plan lost its Skip button: ${body}`);
    if (status === "partial" && !/1 plan\(s\) to review/.test(String(els["#ai-status"].textContent))) fail("partial plan not counted to review");
  }
  console.log("AI-TEXTS OK");
  process.exit(0);
})().catch((e) => { console.log("AI-TEXTS ERROR " + e.stack); process.exit(2); });
