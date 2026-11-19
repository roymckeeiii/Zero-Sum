// "Show images" applies to the message it was clicked on only: the next message opened (j/k, another sender) is shown
// with remote images blocked again, so its tracking pixels do not load.
"use strict";
const { load } = require("./dom_sandbox");
const { t: { S, open }, els, pending, ok, settle, tick } = load(["S", "open"]);
const msg = (id) => ({ id, threadId: id, kind: "html", body: `<p>${id}</p><img src="https://track.example/${id}.gif">`, from: "x", to: "y", subject: id, date: "", email: "a@x.com" });
const item = (id) => ({ id, threadId: id, account: "A", email: "a@x.com", ts: 1, labels: [], from: "x", fromAddr: "x", subject: id, snippet: "", unread: false });
const fail = (m) => { console.log("IMAGES FAIL " + m); process.exit(1); };
async function show(i) {
  const p = open(i);
  await tick();
  for (const r of pending.splice(0)) r.resolve(ok(r.url.includes("/api/message?") ? msg(S.items[i].id) : { items: [], errors: [] }));
  await p; await tick();
  return String(els["#reader iframe"].srcdoc || "");
}
(async () => {
  await settle();
  S.items = [item("aaaaaa1"), item("bbbbbb2")];
  if ((await show(0)).includes("img-src https:")) fail("images must be blocked by default");
  const click = els["#r-img"].onclick;           // Show images on the first message
  click(); await tick();
  for (const r of pending.splice(0)) r.resolve(ok(msg("aaaaaa1")));
  await tick(); await tick();
  if (!String(els["#reader iframe"].srcdoc).includes("img-src https:")) fail("Show images must allow them on this message");
  if ((await show(1)).includes("img-src https:")) fail("the next message must not load remote images");
  if (!(await show(0)).includes("img-src https:")) fail("going back to the allowed message may show them again");
  console.log("IMAGES OK");
})().catch((e) => { console.log("IMAGES ERROR " + e.stack); process.exit(2); });
