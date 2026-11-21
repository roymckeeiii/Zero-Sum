// What the page draws can be used without a mouse and read out by a screen reader: message rows take focus and
// have a name, every quarantine and rule tick box says what it ticks, accounts are links. Run by test_a11y.py.
"use strict";
const assert = require("assert");
const { load } = require("./dom_sandbox");

const { t, el } = load(["row", "renderQuarantine", "S"]);
const count = (html, re) => (html.match(re) || []).length;

const it = { account: "a1", email: "me@x.com", id: "aa11", threadId: "t1", from: "Shop <b>", fromAddr: "deals@shop.com",
  subject: 'Sale "now" <50%>', snippet: "s", ts: 1700000000, unread: true, starred: false, folder: "Inbox", labels: [], rules: [] };
const r = t.row(it, 0);
assert.match(r, /class="row [^"]*" data-i="0" tabindex="0"/);
assert.match(r, /aria-label="Unread\. From Shop &lt;b&gt;: Sale &quot;now&quot; &lt;50%&gt;, /);   // escaped, never raw

t.S.quar = { label: "Quarantine", total: 3, shown: 3, errors: [], senders: [
  { addr: "deals@shop.com", name: "Shop", count: 2, accounts: [{ id: "a1", email: "me@x.com" }], items: [
    { account: "a1", email: "me@x.com", id: "aa01", subject: "One", snippet: "", ts: 1 },
    { account: "a1", email: "me@x.com", id: "aa02", subject: "Two <i>", snippet: "", ts: 2 }] },
  { addr: "x@y.com", name: "", count: 1, accounts: [{ id: "a1", email: "me@x.com" }], items: [
    { account: "a1", email: "me@x.com", id: "aa03", subject: "Three", snippet: "", ts: 3 }] }] };
t.renderQuarantine();
const q = el("#q-body").innerHTML;
const boxes = count(q, /<input type="checkbox"/g);
assert.strictEqual(boxes, 5);
assert.strictEqual(count(q, /<input type="checkbox"[^>]* aria-label="[^"]+"/g), boxes);
assert.match(q, /aria-label="Select every listed email from Shop"/);
assert.match(q, /aria-label="Select: Two &lt;i&gt;"/);
console.log("A11Y OK");
