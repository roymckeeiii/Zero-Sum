// RFC-001885: an older search (or Load more) that answers after a newer one must not overwrite the list.
// Runs app.js's real loadList in a sandbox with a stub DOM and a fetch whose answers we release in any order.
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm");
const src = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8") + "\n;globalThis.__t = { S, loadList };";
const stub = () => new Proxy(function () {}, {
  get: (t, k) => (k === Symbol.toPrimitive ? () => "" : k === "then" ? undefined : stub()),
  set: () => true, apply: () => stub(), construct: () => stub(),
});
const pending = [];
const ctx = {
  console, URLSearchParams, setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {}, Promise, JSON, Math, Date, Set, Map, String, Number, Error,
  document: stub(), window: stub(), localStorage: stub(), location: stub(), navigator: stub(), history: stub(),
  matchMedia: () => stub(), addEventListener: () => {}, requestAnimationFrame: () => 0,
  fetch: (url) => new Promise((resolve) => pending.push({ url: String(url), resolve })),
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(src, ctx);
const { S, loadList } = ctx.__t;
const settleBoot = async () => {   // answer whatever app.js asked for at load (accounts, config, ...) with empty data
  for (let i = 0; i < 5; i++) {
    for (const p of pending.splice(0)) {
      const body = p.url.includes("/api/accounts") ? [] : p.url.includes("/api/messages") ? { items: [], errors: [], next: "" } : {};
      p.resolve({ ok: true, status: 200, json: async () => body });
    }
    await new Promise((r) => setTimeout(r, 0));
  }
};
const answer = (p, items, next) => p.resolve({ ok: true, status: 200, json: async () => ({ items, errors: [], next }) });
(async () => {
  await settleBoot();
  S.items = []; S.scope = "all";
  S.q = "old"; const older = loadList();            // first search
  S.q = "new"; const newer = loadList();            // user typed again before it answered
  const msgs = pending.splice(0).filter((p) => p.url.includes("/api/messages"));
  if (msgs.length !== 2) throw new Error(`expected 2 searches, got ${msgs.length}`);
  const [p1, p2] = msgs;
  answer(p2, [{ id: "n1", account: "A", email: "a@x.com", ts: 2, labels: [], from: "x", fromAddr: "x", subject: "s", snippet: "" }], "NEXT_NEW");
  await newer;
  answer(p1, [{ id: "o1", account: "A", email: "a@x.com", ts: 1, labels: [], from: "x", fromAddr: "x", subject: "s", snippet: "" }], "NEXT_OLD");
  await older;
  const ok = S.items.length === 1 && S.items[0].id === "n1" && S.next === "NEXT_NEW";
  console.log(ok ? "STALE-GUARD OK" : `STALE-GUARD FAIL items=${S.items.map((i) => i.id)} next=${S.next}`);
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.log("STALE-GUARD ERROR " + e.stack); process.exit(2); });
