// The reader shows the message last opened: an older message that answers after a newer one (j j pressed quickly)
// must not land in the reader, where Archive / Delete / e / # act on the newer selection, not on what is shown.
// Runs app.js's real open() in a sandbox with a small stub DOM and a fetch whose answers we release in any order.
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm");
const src = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8") + "\n;globalThis.__t = { S, open };";
const stub = () => new Proxy(function () {}, {
  get: (t, k) => (k === Symbol.toPrimitive ? () => "" : k === "then" ? undefined : k === Symbol.iterator ? [][Symbol.iterator] : stub()),
  set: () => true, apply: () => stub(), construct: () => stub(),
});
const els = {};
const el = (sel) => (els[sel] ||= new Proxy({}, {   // an element that remembers what is written to it
  get: (t, k) => (k in t ? t[k] : k === "then" ? undefined : stub()),
  set: (t, k, v) => { t[k] = v; return true; },
}));
const pending = [];
const document = new Proxy({}, { get: (t, k) => (k === "querySelector" ? el : k === "querySelectorAll" ? () => [] : stub()) });
const ctx = {
  console, URLSearchParams, setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {}, Promise, JSON, Math, Date, Set, Map, String, Number, Error,
  document, window: stub(), localStorage: stub(), location: stub(), navigator: stub(), history: stub(),
  matchMedia: () => stub(), addEventListener: () => {}, requestAnimationFrame: () => 0, encodeURIComponent,
  fetch: (url) => new Promise((resolve) => pending.push({ url: String(url), resolve })),
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(src, ctx);
const { S, open } = ctx.__t;
const tick = () => new Promise((r) => setTimeout(r, 0));
const ok = (body) => ({ ok: true, status: 200, json: async () => body });
const msg = (id) => ({ id, threadId: id, kind: "text", body: `body of ${id}`, from: "x", to: "y", subject: `subject ${id}`, date: "", email: "a@x.com" });
(async () => {
  for (let i = 0; i < 5; i++) { for (const p of pending.splice(0)) p.resolve(ok(p.url.includes("/api/accounts") ? [] : { items: [], errors: [], next: "" })); await tick(); }
  const item = (id) => ({ id, threadId: id, account: "A", email: "a@x.com", ts: 1, labels: [], from: "x", fromAddr: "x", subject: id, snippet: "", unread: false });
  S.items = [item("aaaaaa1"), item("bbbbbb2")];
  const first = open(0), second = open(1);           // j, j: the second message is the one selected
  const reads = pending.splice(0).filter((p) => p.url.includes("/api/message?"));
  if (reads.length !== 2) throw new Error(`expected 2 message reads, got ${reads.length}`);
  reads[1].resolve(ok(msg("bbbbbb2"))); await second;
  reads[0].resolve(ok(msg("aaaaaa1"))); await first; // the older answer comes last
  const shown = String(els["#reader"].innerHTML);
  const good = S.sel === 1 && shown.includes("subject bbbbbb2") && !shown.includes("subject aaaaaa1");
  console.log(good ? "READER-GUARD OK" : `READER-GUARD FAIL sel=${S.sel} shown=${shown.slice(0, 120)}`);
  process.exit(good ? 0 : 1);
})().catch((e) => { console.log("READER-GUARD ERROR " + e.stack); process.exit(2); });
