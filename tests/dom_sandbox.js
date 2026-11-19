// Loads app.js in a vm sandbox with a small stub DOM and a fetch whose answers the test releases, in any order.
// Elements found with $()/querySelector remember what is written to them (innerHTML, onclick, ...), so a test can
// read what was drawn and click what was wired up; setInterval callbacks are kept in `intervals`, to be run by hand. Shared by the node-run UI tests.
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm");

function load(exportNames) {
  const src = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8") + `\n;globalThis.__t = { ${exportNames.join(", ")} };`;
  const stub = () => new Proxy(function () {}, {
    get: (t, k) => (k === Symbol.toPrimitive ? () => "" : k === "then" ? undefined : k === Symbol.iterator ? [][Symbol.iterator] : stub()),
    set: () => true, apply: () => stub(), construct: () => stub(),
  });
  const els = {};
  const el = (sel) => (els[sel] ||= new Proxy({ querySelector: (s) => el(`${sel} ${s}`), style: {} }, {
    get: (t, k) => (k in t ? t[k] : k === "then" ? undefined : stub()),
    set: (t, k, v) => { t[k] = v; return true; },
  }));
  const pending = [], intervals = [];
  const document = new Proxy({}, { get: (t, k) => (k === "querySelector" ? el : k === "querySelectorAll" ? () => []
    : k === "hidden" ? false : stub()) });
  const ctx = {
    console, URLSearchParams, setTimeout, clearTimeout, setInterval: (fn) => intervals.push(fn), clearInterval: () => {}, Promise, JSON, Math, Date, Intl, Set, Map, String, Number, Error,
    document, window: stub(), localStorage: stub(), location: stub(), navigator: stub(), history: stub(), alert: () => {},
    matchMedia: () => stub(), addEventListener: () => {}, requestAnimationFrame: () => 0, encodeURIComponent,
    fetch: (url, opts) => new Promise((resolve) => pending.push({ url: String(url), body: opts && opts.body ? JSON.parse(opts.body) : null, resolve })),
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const tick = () => new Promise((r) => setTimeout(r, 0));
  const ok = (body) => ({ ok: true, status: 200, json: async () => body });
  // answer whatever is waiting with empty data (accounts, config, lists), a few rounds deep
  const settle = async () => { for (let i = 0; i < 5; i++) { for (const p of pending.splice(0)) p.resolve(ok(p.url.includes("/api/accounts") ? [] : { items: [], errors: [], next: "" })); await tick(); } };
  return { t: ctx.__t, els, el, pending, intervals, tick, ok, settle };
}

module.exports = { load };
