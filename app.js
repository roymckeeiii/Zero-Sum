"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmt = (n) => (n == null ? "–" : Number(n).toLocaleString());
const S = { tab: "inbox", account: "", limit: 30, items: [], sel: -1, accounts: [], images: false, slot: {} };

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "zero-sum" },
    body: JSON.stringify(body) } : {});
  const d = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
}

/* account colors: fixed categorical order by account (sorted by email), never by rank; past 8 -> neutral */
function slotOf(id) { const i = S.slot[id]; return i == null || i >= 8 ? "var(--s9)" : `var(--s${i + 1})`; }
function setSlots(accs) { S.slot = {}; [...accs].sort((a, b) => a.email.localeCompare(b.email)).forEach((a, i) => { S.slot[a.id] = i; }); }
const short = (email) => email.split("@")[0];
const acctChip = (id, email) => `<span class="chip"><span class="dot" style="background:${slotOf(id)}"></span>${esc(short(email))}</span>`;

function when(ts) {
  const d = new Date(ts * 1000), now = new Date();
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (now - d < 6 * 864e5) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}

const tip = $("#tip");
function showTip(e, html) { tip.innerHTML = html; tip.style.display = "block";
  const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8), y = Math.min(e.clientY + 14, innerHeight - tip.offsetHeight - 8);
  tip.style.left = `${x}px`; tip.style.top = `${y}px`; }
function hideTip() { tip.style.display = "none"; }

/* ------------------------------------------------------------------ tabs */
const loaded = {};
$("#tabs").onclick = (e) => {
  const b = e.target.closest("button[data-tab]");
  if (!b) return;
  S.tab = b.dataset.tab;
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("on", v.id === `v-${S.tab}`));
  if (!loaded[S.tab]) { loaded[S.tab] = true; ({ cleanup: loadSenders, ai: loadPlans, quarantine: loadQuarantine, analytics: loadAnalytics, rules: loadRules })[S.tab]?.(); }
};

/* ------------------------------------------------------------------ inbox */
async function loadAccounts() {
  try { S.accounts = await api("/api/accounts"); } catch (e) { S.accounts = []; }
  setSlots(S.accounts);
  const total = S.accounts.reduce((n, a) => n + (a.unread || 0), 0);
  $("#accounts").innerHTML = `<a data-id="" class="${S.account ? "" : "on"}"><span>All accounts</span><span class="n">${total || ""}</span></a>` +
    S.accounts.map((a) => `<a data-id="${esc(a.id)}" class="${a.id === S.account ? "on" : ""} ${a.ok ? "" : "bad"}" title="${esc(a.ok ? a.email : a.error)}">
      <span><span class="dot" style="background:${slotOf(a.id)}"></span>${esc(a.email)}</span><span class="n">${a.ok ? a.unread || "" : "!"}</span></a>`).join("") +
    `<div class="tools"><input type="search" id="q" placeholder="Search (Gmail syntax)"><label><input type="checkbox" id="unread"> Unread only</label>
      <button id="refresh">Refresh (r)</button></div>`;
  if (!S.accounts.length) $("#accounts").innerHTML += `<div class="err">No linked accounts yet: add them in Inbox Zero.</div>`;
  $("#q").value = S.q || ""; $("#unread").checked = !!S.unreadOnly;
  let t;
  $("#q").oninput = (e) => { S.q = e.target.value; clearTimeout(t); t = setTimeout(() => loadList(), 400); };
  $("#unread").onchange = (e) => { S.unreadOnly = e.target.checked; loadList(); };
  $("#refresh").onclick = () => { loadAccounts(); loadList(true); };
}

async function loadList(keepSel) {
  const p = new URLSearchParams({ limit: S.limit, account: S.account, q: S.q || "", unread: S.unreadOnly ? "1" : "0" });
  if (!keepSel) $("#list").innerHTML = `<div class="empty">Loading…</div>`;
  let d;
  try { d = await api(`/api/messages?${p}`); } catch (e) { $("#list").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const selId = keepSel && S.items[S.sel] ? S.items[S.sel].id : null;
  S.items = d.items; S.errors = d.errors; S.more = d.items.length >= S.limit;
  S.sel = selId ? S.items.findIndex((i) => i.id === selId) : -1;
  redraw();
}

function row(it, i) {
  const chips = (it.rules || []).map((r) => `<span class="chip rule" title="Inbox Zero rule (${esc(r.status)})">${esc(r.name)}</span>`).join("") +
    it.labels.map((l) => `<span class="chip">${esc(l)}</span>`).join("");
  return `<div class="row ${it.unread ? "unread" : ""} ${i === S.sel ? "sel" : ""}" data-i="${i}">
    <span class="dot" style="background:${slotOf(it.account)}"></span>
    <span class="from" title="${esc(it.fromAddr)}">${it.starred ? '<span class="star" title="Starred">★</span> ' : ""}${it.protected ? '<span class="chip prot" title="Protected sender">protected</span>' : ""}${esc(it.from)}</span><span class="when">${when(it.ts)}</span>
    <span class="acts"><button data-act="archive" title="Archive (e)">Archive</button>
      <button data-act="${it.starred ? "unstar" : "star"}" title="Star (s)">${it.starred ? "Unstar" : "Star"}</button>
      <button data-act="${it.unread ? "read" : "unread"}">${it.unread ? "Read" : "Unread"}</button>
      <button data-act="delete" title="Move to Trash (#)">Delete</button></span>
    <span class="subj">${esc(it.subject)}</span><span class="snip">${esc(it.snippet)}</span>
    <span class="chips">${S.account ? "" : acctChip(it.account, it.email)}${chips}</span></div>`;
}

function redraw() {
  $("#list").innerHTML = (S.errors || []).map((e) => `<div class="err">${esc(e.email)}: ${esc(e.error)}</div>`).join("") +
    (S.items.length ? S.items.map(row).join("") : `<div class="empty">Nothing here.</div>`) +
    (S.more ? `<button id="more">Load more</button>` : "");
  const more = $("#more");
  if (more) more.onclick = () => { S.limit = Math.min(S.limit * 2, 200); loadList(true); };
}

async function act(i, action) {
  const it = S.items[i];
  if (!it) return;
  try { await api("/api/action", { account: it.account, threadId: it.threadId, id: it.id, action }); }
  catch (e) { alert(`${action} failed: ${e.message}`); return; }
  if (action === "archive" || action === "delete") {
    S.items = S.items.filter((x) => !(x.threadId === it.threadId && x.account === it.account));
    if (S.sel >= S.items.length) S.sel = S.items.length - 1;
    redraw();
    if (action === "delete") toast(`Moved to Trash: ${esc(it.subject)}`, "Undo",
      () => api("/api/action", { account: it.account, threadId: it.threadId, action: "undelete" }).then(() => loadList(true)));
    if (S.sel >= 0) open(S.sel); else $("#reader").innerHTML = `<div class="empty">${action === "delete" ? "Deleted" : "Archived"}.</div>`;
  } else if (action === "star" || action === "unstar") { it.starred = action === "star"; redraw(); if (S.sel === i) open(i); }
  else { it.unread = action === "unread"; redraw(); }
  loadAccounts();
}

function toast(html, label, fn) {
  let t = $("#toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
  t.innerHTML = `<span>${html}</span>${label ? `<button>${esc(label)}</button>` : ""}`;
  t.style.display = "flex";
  if (label) t.querySelector("button").onclick = () => { fn(); t.style.display = "none"; };
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.style.display = "none"; }, 8000);
}

function frameDoc(m) {
  const imgs = S.images ? "img-src https: data:;" : "img-src data:;";
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy"
    content="default-src 'none'; style-src 'unsafe-inline'; ${imgs} font-src data:"><base target="_blank">
    <style>body{font:14px/1.5 system-ui,sans-serif;margin:16px;color:#1d2330;word-wrap:break-word}img{max-width:100%;height:auto}</style>
    </head><body>${m.body}</body></html>`;
}

async function open(i) {
  S.sel = i; redraw();
  const it = S.items[i], r = $("#reader");
  document.body.classList.add("reading");
  r.innerHTML = `<div class="empty">Loading…</div>`;
  let m;
  try { m = await api(`/api/message?account=${encodeURIComponent(it.account)}&id=${encodeURIComponent(it.id)}`); }
  catch (e) { r.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const gmailUrl = `https://mail.google.com/mail/?authuser=${encodeURIComponent(m.email)}#all/${encodeURIComponent(m.threadId)}`;
  r.innerHTML = `<div class="hd"><h2>${esc(m.subject || "(no subject)")}</h2>
    <div class="meta"><b>${esc(m.from)}</b><br>to ${esc(m.to)}${m.cc ? `, cc ${esc(m.cc)}` : ""}<br>${esc(m.date)} · ${acctChip(it.account, m.email)}</div>
    <div class="tools"><button id="r-archive">Archive (e)</button><button id="r-star">${it.starred ? "Unstar" : "Star"} (s)</button>
      <button id="r-unread">Mark unread (u)</button><button id="r-delete">Delete (#)</button>
      <a href="${gmailUrl}" target="_blank" rel="noopener"><button>Reply in Gmail ↗</button></a>
      ${m.kind === "html" ? `<button id="r-img">${S.images ? "Hide" : "Show"} images</button>` : ""}
      <button id="r-close" class="mobile">Close</button></div></div>` +
    (m.kind === "html" ? `<iframe sandbox="allow-popups allow-popups-to-escape-sandbox" referrerpolicy="no-referrer" title="Email body"></iframe>`
      : `<pre>${esc(m.body)}</pre>`);
  if (m.kind === "html") r.querySelector("iframe").srcdoc = frameDoc(m);
  $("#r-archive").onclick = () => act(S.sel, "archive");
  $("#r-unread").onclick = () => act(S.sel, "unread");
  $("#r-delete").onclick = () => act(S.sel, "delete");
  $("#r-star").onclick = () => act(S.sel, S.items[S.sel].starred ? "unstar" : "star");
  $("#r-close").onclick = () => document.body.classList.remove("reading");
  const img = $("#r-img");
  if (img) img.onclick = () => { S.images = !S.images; open(S.sel); };
  if (it.unread) { it.unread = false; redraw();
    api("/api/action", { account: it.account, threadId: it.threadId, action: "read" }).then(loadAccounts).catch(() => {}); }
}

$("#accounts").onclick = (e) => {
  const a = e.target.closest("a[data-id]");
  if (!a) return;
  S.account = a.dataset.id; S.limit = 30;
  loadAccounts(); loadList();
};
$("#list").onclick = (e) => {
  const el = e.target.closest(".row");
  if (!el) return;
  const i = +el.dataset.i, b = e.target.closest("button[data-act]");
  if (b) { e.stopPropagation(); act(i, b.dataset.act); } else open(i);
};
document.onkeydown = (e) => {
  if (S.tab !== "inbox" || ["INPUT", "SELECT"].includes(e.target.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
  const n = S.items.length;
  if (e.key === "j" && n) open(Math.min(S.sel + 1, n - 1));
  else if (e.key === "k" && n) open(Math.max(S.sel - 1, 0));
  else if (e.key === "e" && S.sel >= 0) act(S.sel, "archive");
  else if (e.key === "u" && S.sel >= 0) act(S.sel, "unread");
  else if (e.key === "#" && S.sel >= 0) act(S.sel, "delete");
  else if (e.key === "s" && S.sel >= 0) act(S.sel, S.items[S.sel].starred ? "unstar" : "star");
  else if (e.key === "r") { loadAccounts(); loadList(true); }
  else if (e.key === "/") { e.preventDefault(); $("#q")?.focus(); }
};

/* ------------------------------------------------------------------ cleanup */
const STATUS = { UNSUBSCRIBED: "unsubscribed", AUTO_ARCHIVED: "auto-archived / quarantined", APPROVED: "kept" };
const MENU = [["Unsubscribe", ["unsubscribe", "unsub_archive", "unsub_quarantine", "unsub_delete"]],
  ["Without unsubscribing", ["autoarchive", "quarantine", "archive"]], ["Remove", ["delete", "block", "spam"]]];
const LABELS = { unsubscribe: "Unsubscribe", unsub_archive: "Unsubscribe & archive", unsub_quarantine: "Unsubscribe & quarantine",
  quarantine: "Quarantine", unsub_delete: "Unsubscribe & delete", autoarchive: "Auto-archive", archive: "Archive", delete: "Delete all", block: "Block", spam: "Spam" };
const TIPS = { unsubscribe: "Unsubscribe only (one-click where offered); mail already there stays where it is",
  unsub_delete: "Unsubscribe, move all mail from this sender to Trash, and send anything that still arrives to Trash",
  unsub_archive: "Unsubscribe, archive what is in the inbox, and keep anything that still arrives out of the inbox",
  unsub_quarantine: "Unsubscribe, and hold their mail (now and future) on the Quarantine tab for review",
  quarantine: "Hold their mail (now and future) on the Quarantine tab, out of the inbox, to review and clear in bulk",
  autoarchive: "Skip the inbox from now on (Gmail filter), and archive what is there", archive: "Archive what is in the inbox now",
  delete: "Move all mail from this sender to Trash (Gmail empties Trash after 30 days)",
  block: "Move all mail from this sender to Trash, and send future mail straight to Trash (Gmail filter)",
  spam: "Report all mail from this sender as spam (also trains Gmail's spam filter)" };
const CONFIRM = { unsub_delete: "Unsubscribe and move ALL mail from", delete: "Move ALL mail from", block: "Move ALL mail from",
  spam: "Report ALL mail from" };
function confirmText(a, s) {  // every removal action asks first (Roy, 11-07)
  const n = `${s.addr} (${s.count} recent emails across ${s.accounts.length} account(s))`;
  if (CONFIRM[a]) return `${CONFIRM[a]} ${n}` + (a === "spam" ? " as spam?" : " to Trash?") +
    (["block", "unsub_delete"].includes(a) ? " Future mail from this sender will go straight to Trash." : "");
  if (a === "protect") return `Protect ${s.addr}? Zero Sum will refuse to unsubscribe, archive, delete or block anything from it, now and in future, and Gmail will never send it to Spam (in every account).`;
  return { unsubscribe: `Unsubscribe from ${n}?`, unsub_archive: `Unsubscribe from ${n} and archive their mail? Future mail skips the inbox.`,
    unsub_quarantine: `Unsubscribe from ${n} and move their mail to Quarantine? Future mail goes there too.`,
    quarantine: `Move mail from ${n} to Quarantine? Future mail goes there too.`,
    autoarchive: `Archive mail from ${n}? Future mail skips the inbox.`, archive: `Archive what is in the inbox from ${n}?` }[a];
}
async function loadSenders() {
  const p = new URLSearchParams({ q: $("#c-q").value, days: $("#c-days").value, hide: $("#c-handled").checked ? "0" : "1" });
  let d;
  try { d = await api(`/api/senders?${p}`); } catch (e) { $("#c-table").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const c = d.collect, done = c.filter((x) => x.backfill_done).length, msgs = c.reduce((n, x) => n + x.messages, 0);
  $("#c-status").innerHTML = `History collected: ${fmt(msgs)} emails across ${c.length} accounts; ${done}/${c.length} fully back-filled
    (older mail is back-filled a few hundred messages per account each run).` + c.filter((x) => x.error).map((x) => `<div class="err">${esc(x.email)}: ${esc(x.error)}</div>`).join("");
  if (!d.senders.length) { $("#c-table").innerHTML = `<div class="empty">No senders yet${msgs ? " match" : ": history is still being collected"}.</div>`; return; }
  S.senders = d.senders;
  $("#c-table").innerHTML = `<table><thead><tr><th>Sender</th><th class="num">Emails</th><th class="num">Read</th>
    <th class="num">In inbox</th><th>Accounts</th><th>Last</th><th>Apply to every account</th></tr></thead><tbody>` +
    d.senders.map((s, i) => `<tr data-row="${i}"><td class="who" data-peek="${i}" title="Show the 20 most recent emails">
      <span class="caret">▸</span> <b>${esc(s.name || s.addr)}</b><br><span class="note">${esc(s.addr)}</span></td>
      <td class="num">${fmt(s.count)}</td><td class="num">${s.readPct}%</td><td class="num">${fmt(s.inbox)}</td>
      <td>${s.accounts.map((a) => `${acctChip(a.id, a.email)}${a.status ? `<span class="note">${STATUS[a.status] || a.status}</span> ` : ""}`).join("")}</td>
      <td class="note">${when(s.last)}</td>
      <td class="acts-cell">${s.protected ? '<span class="chip prot">protected</span> ' : ""}
        <div class="grp"><button data-i="${i}" data-a="${s.protected ? "unprotect" : "protect"}" title="Protected senders are never archived, deleted or blocked in bulk">${s.protected ? "Unprotect" : "Protect"}</button>
          <button data-i="${i}" data-a="star" title="Star what is in the inbox, and future mail (Gmail filter)">Auto-star</button>
          <button data-i="${i}" data-a="unstar" title="Remove the auto-star filter and the stars">Un-star</button>
          <button data-i="${i}" data-a="keep" title="Mark as wanted in Inbox Zero (hides it here)">Keep</button>
          ${S.ai && !s.protected ? `<button data-i="${i}" data-ai="1" title="Let AI read this sender's subjects and suggest what to keep and what to clear">✨ AI suggest</button>` : ""}</div>
        <div class="grp"><select class="cleanup" data-i="${i}" ${s.protected ? "disabled title='Protected sender'" : ""}>
          <option value="">Clean up…</option>${MENU.map(([g, acts]) => `<optgroup label="${g}">${acts.map((a) =>
            `<option value="${a}" title="${esc(TIPS[a])}">${LABELS[a]}</option>`).join("")}</optgroup>`).join("")}</select></div></td></tr>`).join("") + "</tbody></table>";
  $("#c-prot").innerHTML = d.protected.length ? "Protected: " + d.protected.map((k) => `<span class="chip prot">${esc(k)}
    <button class="x" data-unprot="${esc(k)}" title="Unprotect">×</button></span>`).join("") : "";
}
/* confirmation dialog, with "keep emails whose subject contains" for actions that clear mail */
const KEEPABLE = ["unsub_archive", "unsub_quarantine", "unsub_delete", "autoarchive", "quarantine", "archive", "delete", "block", "spam"];
function askDialog(a, s, ai) {
  const d = $("#dlg"), text = confirmText(a, s);
  if (!text && !ai) return Promise.resolve({ keep: [] });
  $("#dlg-title").textContent = `${LABELS[a] || a}: ${s.addr}`;
  $("#dlg-text").textContent = text || "";
  $("#dlg-keep-row").hidden = !KEEPABLE.includes(a);
  $("#dlg-keep").value = ai ? ai.keep_words.join(", ") : "";
  $("#dlg-ai").innerHTML = ai ? `<p><b>AI suggests: ${esc(LABELS[ai.action] || ai.action)}</b>${ai.keep_words.length ? `, keeping subjects with
    <i>${ai.keep_words.map(esc).join(", ")}</i>` : ""}. ${esc(ai.reason)}</p><p class="note">From ${ai.sampled} recent emails (${esc(ai.model)}).
    Green = kept, the rest gets the action:</p><ul class="subjects">` + ai.subjects.slice(0, 25).map((x) =>
    `<li class="${x.kept ? "kept" : ""}">${x.count}× ${esc(x.subject)}</li>`).join("") + "</ul>" : "";
  $("#dlg-ok").textContent = LABELS[a] || "Confirm";
  $("#dlg-ok").className = ["unsub_delete", "delete", "block", "spam"].includes(a) ? "danger" : "primary";
  const preview = () => {  // live: which sample subjects the typed keep words would keep
    const w = $("#dlg-keep").value.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
    d.querySelectorAll(".subjects li").forEach((li) => li.classList.toggle("kept", subjectHas(w, li.textContent)));
  };
  $("#dlg-keep").oninput = preview;
  d.showModal();
  return new Promise((res) => {
    d.onclose = () => res(d.returnValue === "ok" ? { keep: $("#dlg-keep").value.split(",").map((x) => x.trim()).filter(Boolean) } : null);
  });
}
async function runSender(s, a, b, ai) {
  const ok = await askDialog(a, s, ai);
  if (!ok) return;
  if (b) { b.disabled = true; b.textContent = "…"; }
  try {
    const r = await api("/api/sender", { addr: s.addr, action: a, keep: ok.keep });
    S.lastSender = s;
    const next = ["delete", "quarantine", "block", "archive", "unsubscribe"].filter((x) => x !== a)
      .map((x) => `<button data-next="${x}" class="${["delete", "block"].includes(x) ? "danger" : ""}">${LABELS[x]}</button>`).join(" ");
    $("#c-result").innerHTML = `<div class="card"><b>${esc(LABELS[a] || a)}: ${esc(s.addr)}</b>
      ${s.protected ? "" : `<div class="grp" style="margin:6px 0">Next for this sender: ${next}</div>`}<ul>` +
      r.results.map((x) => `<li class="${x.ok ? "" : "err"}">${esc(x.email)}: ${esc(x.note)}</li>`).join("") + "</ul>" +
      (r.manual.length ? `<p>Finish unsubscribing by opening these (one per account):</p><ul>` +
        r.manual.map((m) => `<li>${esc(m.email)}: <a href="${esc(m.url)}" target="_blank" rel="noopener noreferrer">${esc(m.url.slice(0, 80))}</a></li>`).join("") + "</ul>" : "") + "</div>";
  } catch (err) { $("#c-result").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  loadSenders();
}
/* Undo: every bulk change is recorded with the messages it touched (cleanup.py _record) */
function undoWhat(a) {
  if (a.add.includes("TRASH")) return "moved to Trash";
  if (a.add.includes("SPAM")) return "reported as spam";
  if (a.add.includes("STARRED")) return "starred";
  if (a.remove.includes("STARRED")) return "unstarred";
  if (a.add.includes("INBOX")) return "returned to the inbox";
  if (a.add.length) return "moved to Quarantine";
  return a.remove.includes("INBOX") ? "archived" : "changed";
}
async function loadUndo() {
  let d;
  try { d = await api("/api/undo"); } catch (e) { $("#c-undo").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  $("#c-undo").innerHTML = d.actions.length ? `<p>Each line is one bulk change of the last ${d.days} days. Undo puts those
    messages back where they were. It does not remove filters: use Keep or Reset on the sender for that.</p><ul class="ops">` +
    d.actions.map((a) => `<li><span class="note">${esc(a.at.slice(0, 16).replace("T", " "))}</span> ${esc(a.email)}:
      <b>${fmt(a.count)}</b> ${undoWhat(a)}${a.note ? ` (${esc(a.note)})` : ""}
      ${a.undone ? `<span class="note">undone</span>` : `<button data-undo="${Number(a.id)}">Undo</button>`}</li>`).join("") + "</ul>"
    : "No bulk changes recorded yet.";
}
$("#c-undo").onclick = async (e) => {
  const id = e.target.dataset && e.target.dataset.undo;
  if (!id || !confirm("Put these messages back where they were?")) return;
  e.target.disabled = true;
  try { const r = await api("/api/undo", { id: Number(id) }); $("#c-result").innerHTML = `<div class="card">${fmt(r.restored)} messages restored.</div>`; }
  catch (err) { $("#c-result").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  loadUndo(); loadSenders();
};
$("#c-undo-box").ontoggle = () => { if ($("#c-undo-box").open) loadUndo(); };

/* 20 most recent emails from a sender, as a list (shared by Cleanup and AI Cleanup) */
const recentCache = {};
async function recent(addr) {
  if (!recentCache[addr]) recentCache[addr] = api(`/api/sender/recent?addr=${encodeURIComponent(addr)}&n=20`).then((d) => d.items);
  return recentCache[addr];
}
function recentList(items, tag) {
  if (!items.length) return `<div class="note">No mail found.</div>`;
  return `<div class="peek">` + items.map((it) => {
    const t = tag ? tag(it) : null;
    return `<div class="pm">${acctChip(it.account, it.email)}<span class="s"><b>${esc(it.subject)}</b> <span class="note">${esc(it.snippet)}</span></span>
      ${t ? `<span class="chip act-${t.action}">${esc(t.text)}</span>` : ""}<span class="note">${when(it.ts)}</span></div>`;
  }).join("") + "</div>";
}
async function togglePeek(row, addr, cols) {
  const next = row.nextElementSibling;
  if (next && next.classList.contains("peek-row")) { next.remove(); row.querySelector(".caret").textContent = "▸"; return; }
  row.querySelector(".caret").textContent = "▾";
  const tr = document.createElement("tr"); tr.className = "peek-row";
  tr.innerHTML = `<td colspan="${cols}"><div class="note">Loading the 20 most recent emails…</div></td>`;
  row.after(tr);
  try { tr.firstChild.innerHTML = recentList(await recent(addr)); } catch (err) { tr.firstChild.innerHTML = `<div class="err">${esc(err.message)}</div>`; }
}
$("#c-table").onclick = async (e) => {
  const peek = e.target.closest("td[data-peek]");
  if (peek) return togglePeek(peek.parentElement, S.senders[+peek.dataset.peek].addr, 7);
  const aiB = e.target.closest("button[data-ai]");
  if (aiB) {
    const s = S.senders[+aiB.dataset.i];
    aiB.disabled = true; aiB.textContent = "✨ Thinking…";
    try {
      const sug = await api("/api/ai/suggest", { addr: s.addr });
      if (sug.action === "keep") { $("#c-result").innerHTML = `<div class="card"><b>AI: keep ${esc(s.addr)}.</b> ${esc(sug.reason)}</div>`; }
      else await runSender(s, sug.action, null, sug);
    } catch (err) { $("#c-result").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
    aiB.disabled = false; aiB.textContent = "✨ AI suggest";
    return;
  }
  const b = e.target.closest("button[data-a]");
  if (!b) return;
  await runSender(S.senders[+b.dataset.i], b.dataset.a, b);
};
$("#c-table").onchange = (e) => {  // the "Clean up…" menu runs the chosen action through the button handler
  const sel = e.target.closest("select.cleanup");
  if (!sel || !sel.value) return;
  const btn = document.createElement("button");
  btn.dataset.i = sel.dataset.i; btn.dataset.a = sel.value; sel.value = "";
  $("#c-table").appendChild(btn); btn.click(); btn.remove();
};
let ct;
$("#c-q").oninput = () => { clearTimeout(ct); ct = setTimeout(loadSenders, 400); };
$("#c-days").onchange = loadSenders; $("#c-handled").onchange = loadSenders;
$("#c-result").onclick = (e) => {
  const n = e.target.closest("button[data-next]");
  if (!n || !S.lastSender) return;
  S.senders.push(S.lastSender);  // reuse the table's handler for the sender just handled, even if it is now hidden
  const btn = document.createElement("button");
  btn.dataset.i = S.senders.length - 1; btn.dataset.a = n.dataset.next;
  $("#c-table").appendChild(btn); btn.click(); btn.remove();
};
$("#c-prot").onclick = async (e) => {
  const x = e.target.closest("button[data-unprot]");
  if (!x) return;
  await api("/api/sender", { addr: x.dataset.unprot, action: "unprotect" }).catch((err) => alert(err.message));
  loadSenders();
};
$("#c-protect-many").onclick = async () => {
  const d = $("#dlg-protect");
  $("#dlg-protect-text").value = "";
  d.showModal();
  const ok = await new Promise((res) => { d.onclose = () => res(d.returnValue === "ok"); });
  const text = $("#dlg-protect-text").value;
  if (!ok || !text.trim()) return;
  try {
    const r = await api("/api/protect/bulk", { text });
    $("#c-result").innerHTML = `<div class="card">${fmt(r.protected)} sender(s) protected. Gmail "never spam" filters are being added in the background.` +
      (r.rejected.length ? `<br><span class="note">Not read as an address or domain: ${r.rejected.map(esc).join(", ")}</span>` : "") + "</div>";
  } catch (err) { $("#c-result").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  loadSenders();
};
$("#c-protect-btn").onclick = async () => {
  const v = $("#c-protect").value.trim().toLowerCase();
  if (!v) return;
  try { await api("/api/sender", { addr: v, action: "protect" }); $("#c-protect").value = ""; } catch (err) { alert(err.message); }
  loadSenders();
};
$("#c-collect").onclick = async () => { await api("/api/collect", {}).catch(() => {}); $("#c-status").textContent = "Collecting… refresh in a minute."; };

/* Whole-word match, as the applied Gmail search subject:(word) does (cleanup.py subject_has): a substring test showed
   "Reorder now" as kept by "order", and Gmail then gave it the destructive action. */
function subjectHas(words, subject) {
  const sub = String(subject || "").toLowerCase();
  return (words || []).some((w) => {
    w = String(w || "").trim().toLowerCase().replace(/\s+/g, " ");
    if (!w) return false;
    const body = w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/ /g, "\\s+");
    return new RegExp(`(?<![\\p{L}\\p{N}])${body}(?![\\p{L}\\p{N}])`, "u").test(sub);
  });
}

/* ------------------------------------------------------------------ AI cleanup */
const GACT = { keep: "Keep & star", archive: "Archive", quarantine: "Quarantine", delete: "Delete" };
const gactSel = (v, attrs) => `<select ${attrs}>${Object.entries(GACT).map(([k, l]) => `<option value="${k}" ${k === v ? "selected" : ""}>${l}</option>`).join("")}</select>`;
function classify(subject, plan) {  // first group whose words appear in the subject wins, else the default
  const gi = plan.groups.findIndex((g) => subjectHas(g.words, subject));
  return gi >= 0 ? { action: plan.groups[gi].action, text: `${plan.groups[gi].label || "group " + (gi + 1)} → ${GACT[plan.groups[gi].action]}` }
    : { action: plan.default, text: `other → ${GACT[plan.default]}` };
}
let pollT;
async function loadProgress() {
  let g;
  try { g = await api("/api/ai/progress"); } catch (e) { $("#ai-progress").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const pct = (a, b) => (b ? Math.min(100, Math.round((100 * a) / b)) : 0);
  $("#ai-progress").innerHTML = `<div class="ph" style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <h3 style="margin:0">Working through your whole history</h3>
      <label><input type="checkbox" id="ai-bg" ${g.background ? "checked" : ""}> Analyze senders in the background</label></div>
    <p class="note">Zero Sum reads your mail a little at a time (every 15 minutes, within Gmail's limits) and plans senders as their
      history comes in, biggest first. Nothing is changed until you apply a plan.</p>
    <div class="prog-grid"><div>Mail history collected: <b>${fmt(g.collected)}</b> of ${fmt(g.mailboxTotal)} (${pct(g.collected, g.mailboxTotal)}%)
      <div class="meter"><div style="width:${pct(g.collected, g.mailboxTotal)}%"></div></div></div>
    <div>Senders analyzed: <b>${fmt(g.planned)}</b> of ${fmt(g.senders)} with ${"5+"} emails (${pct(g.planned, g.senders)}%)
      <div class="meter"><div style="width:${pct(g.planned, g.senders)}%"></div></div></div></div>
    <details><summary class="note">Per account · AI calls today: ${fmt(g.callsToday)} of ${fmt(g.callCap)}</summary>
      ${g.accounts.map((a) => `<div class="note">${esc(a.email)}: ${fmt(a.collected)} of ${fmt(a.total)} (${pct(a.collected, a.total)}%)</div>`).join("")}</details>`;
  $("#ai-bg").onchange = (e) => api("/api/ai/background", { on: e.target.checked }).catch((err) => alert(err.message));
}
async function loadPlans() {
  loadProgress();
  let d;
  try { d = await api("/api/ai/plans"); S.sorting = await api("/api/ai/sorting"); } catch (e) { $("#ai-body").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  S.plans = d.plans;
  if (Object.values(S.sorting).some((x) => x.status === "sorting")) { clearTimeout(S.sortT); S.sortT = setTimeout(loadPlans, 8000); }
  const r = d.running;
  $("#ai-status").textContent = !d.ai ? "AI is not configured on this server." : r.busy ? `Analyzing senders… ${r.done}/${r.total}`
    : `${d.plans.filter((p) => p.status === "new").length} plan(s) to review.` + (r.error ? ` Last error: ${r.error}` : "");
  $("#ai-run").disabled = !d.ai || r.busy;
  clearTimeout(pollT);
  if (r.busy) pollT = setTimeout(loadPlans, 5000);
  const open = new Set([...document.querySelectorAll("#ai-body .plan[data-open]")].map((x) => x.dataset.addr));
  $("#ai-body").innerHTML = d.plans.length ? d.plans.map((p, i) => planCard(p, i)).join("")
    : `<div class="empty">No plans yet. Click "Analyze top senders": the AI reads each sender's recent subjects and proposes
       which emails to keep and which to clear. Nothing changes until you apply a plan.</div>`;
  d.plans.forEach((p, i) => { if (open.has(p.addr)) showPreview(i); });
}
function planCard(p, i) {
  const done = p.status !== "new", ai = (p.mode || "words") === "ai", srt = (S.sorting || {})[p.addr];
  const sampleCounts = {};
  (p.subjects || []).forEach((x) => { const c = classify(x.subject, p); sampleCounts[c.text] = (sampleCounts[c.text] || 0) + x.count; });
  return `<div class="card plan ${done ? "done" : ""}" data-i="${i}" data-addr="${esc(p.addr)}">
    <div class="ph"><div><b>${esc(p.addr)}</b> <span class="note">· ${fmt(p.count)} emails · ${p.accounts.map((a) => acctChip(a.id, a.email)).join("")}</span>
      ${done ? `<span class="chip">${esc(p.status)}</span>` : ""}<div class="note">${esc(p.reason || "")}</div>
      ${srt ? `<div class="note">AI sort ${esc(srt.status)}: ${fmt(srt.done)}/${fmt(srt.total)} checked, ${fmt(srt.kept)} kept; the rest → ${GACT[srt.action]}.
        Rule: <i>${esc(srt.rule)}</i> <button data-pa="stop" data-i="${i}">Stop AI sort</button></div>` : ""}</div>
      <div class="grp">${done ? `<button data-pa="undo" data-i="${i}">Review again</button>` : `<button data-pa="skip" data-i="${i}">Skip</button>
        <button data-pa="redo" data-i="${i}">Re-analyze</button><button data-pa="apply" data-i="${i}" class="primary">Apply plan</button>`}</div></div>
    ${done ? "" : `<div class="mode"><button data-pa="mode-words" data-i="${i}" class="${ai ? "" : "on"}">Word groups</button><button
        data-pa="mode-ai" data-i="${i}" class="${ai ? "on" : ""}">AI sorts each email</button>
      <span class="note">${ai ? "For senders whose good and bad mail can't be told apart by words: the AI reads every email's subject and preview, now and as new mail arrives."
        : "Gmail filters by subject words: instant and free."}</span></div>` +
    (ai ? `<div class="groups"><label class="rule">Keep emails like this (plain English, edit freely)
        <textarea data-f="rule" rows="2" placeholder="e.g. keep travel articles, guides and trip ideas; sales, deals and booking promos are junk">${esc(p.keep_rule || "")}</textarea></label>
      <div class="g"><span class="gl note">Emails the rule doesn't keep</span><span class="gw note">judged one by one by the AI</span>
        ${gactSel(p.default, 'data-f="default"')}<span></span></div></div>
      <details data-i="${i}"><summary>Preview the 20 most recent emails, tagged by the AI with this rule</summary><div class="pv"></div></details>` :
    `<div class="groups">${p.groups.map((g, gi) => `<div class="g" data-gi="${gi}">
        <input data-f="label" value="${esc(g.label)}" placeholder="Name" class="gl">
        <input data-f="words" value="${esc(g.words.join(", "))}" placeholder="subject words, comma-separated" class="gw">
        ${gactSel(g.action, 'data-f="action"')}<button data-pa="del-g" data-i="${i}" data-gi="${gi}" title="Remove group">×</button></div>`).join("")}
      <div class="g"><span class="gl note">Everything else</span><span class="gw note">mail that matches no group above</span>
        ${gactSel(p.default, 'data-f="default"')}<span></span></div>
      <button data-pa="add-g" data-i="${i}">+ Add group</button>
      <span class="note">In the sample of ${p.sampled}: ${Object.entries(sampleCounts).map(([k, n]) => `${esc(k)}: ${n}`).join(" · ")}</span></div>
      <details data-i="${i}"><summary>Preview the 20 most recent emails and what each would get</summary><div class="pv"></div></details>`)}</div>`;
}
function readCard(i) {  // editor state back into S.plans[i]
  const card = document.querySelector(`#ai-body .plan[data-i="${i}"]`), p = S.plans[i];
  if (!card || p.status !== "new") return p;
  const rule = card.querySelector('[data-f="rule"]');
  if (rule) { if (rule.value !== p.keep_rule) p._verdicts = null; p.keep_rule = rule.value; }
  p.groups = [...card.querySelectorAll(".g[data-gi]")].map((g) => ({ label: g.querySelector('[data-f="label"]').value.trim(),
    words: g.querySelector('[data-f="words"]').value.split(",").map((w) => w.trim()).filter(Boolean), action: g.querySelector('[data-f="action"]').value }));
  p.default = card.querySelector('[data-f="default"]').value;
  return p;
}
async function showPreview(i) {
  const card = document.querySelector(`#ai-body .plan[data-i="${i}"]`);
  const det = card?.querySelector("details");
  if (!det) return;
  det.open = true; card.dataset.open = "1";
  const pv = det.querySelector(".pv"), p = readCard(i);
  pv.innerHTML = `<div class="note">Loading…</div>`;
  try {
    const items = await recent(p.addr);
    if ((p.mode || "words") === "ai") {
      const ck = `${p.addr}\n${p.keep_rule}`;  // one AI call per sender + rule, kept across re-renders
      if (!p._verdicts) p._verdicts = (S.tagCache ||= {})[ck];
      if (!p._verdicts) {
        pv.innerHTML = `<div class="note">The AI is reading the 20 emails…</div>`;
        p._verdicts = S.tagCache[ck] = (await api("/api/ai/classify", { addr: p.addr, rule: p.keep_rule })).verdicts;
      }
      pv.innerHTML = `<div class="note">Tagged with the rule above. Changed the rule? <a href="#" data-retag="${i}">Tag again</a></div>` +
        recentList(items, (it) => p._verdicts[it.id] ? { action: "keep", text: "keep" } : { action: p.default, text: GACT[p.default] });
    } else pv.innerHTML = recentList(items, (it) => classify(it.subject, p));
  } catch (e) { pv.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
$("#ai-body").addEventListener("toggle", (e) => { const det = e.target; if (det.tagName === "DETAILS" && det.open) showPreview(+det.dataset.i);
  else if (det.tagName === "DETAILS") delete det.closest(".plan").dataset.open; }, true);
$("#ai-body").oninput = (e) => {  // live: re-tag the preview as the plan is edited (AI mode re-tags on request)
  const card = e.target.closest(".plan"); if (!card) return;
  const i = +card.dataset.i, p = readCard(i);
  if ((p.mode || "words") === "words" && card.querySelector("details")?.open) showPreview(i);
};
$("#ai-body").onchange = $("#ai-body").oninput;
$("#ai-body").onclick = async (e) => {
  const rt = e.target.closest("a[data-retag]");
  if (rt) { e.preventDefault(); const p = readCard(+rt.dataset.retag); p._verdicts = null; delete (S.tagCache || {})[`${p.addr}\n${p.keep_rule}`];
    return showPreview(+rt.dataset.retag); }
  const b = e.target.closest("button[data-pa]"); if (!b) return;
  const i = +b.dataset.i, p = readCard(i), a = b.dataset.pa;
  if (a === "mode-words" || a === "mode-ai") { p.mode = a === "mode-ai" ? "ai" : "words"; return loadPlansRender(); }
  if (a === "stop") { if (confirm(`Stop AI-sorting new mail from ${p.addr}? Mail already sorted stays where it is.`))
    await api("/api/ai/stop-sort", { addr: p.addr }).catch((err) => alert(err.message)); return loadPlans(); }
  if (a === "add-g") { p.groups.push({ label: "", words: [], action: "keep" }); return loadPlansRender(); }
  if (a === "del-g") { p.groups.splice(+b.dataset.gi, 1); return loadPlansRender(); }
  if (a === "skip" || a === "undo") { await api("/api/ai/skip", { addr: p.addr, undo: a === "undo" }).catch(() => {}); return loadPlans(); }
  if (a === "redo") { await api("/api/ai/analyze", { addr: p.addr }).catch((err) => alert(err.message)); return loadPlans(); }
  if (a === "apply") {
    const isAi = (p.mode || "words") === "ai";
    if (isAi && !p.keep_rule.trim()) return alert("Write which emails to keep first.");
    const lines = isAi ? [`• keep: ${p.keep_rule}`, `• everything else → ${GACT[p.default]}`]
      : p.groups.map((g) => `• subject has ${g.words.join(" / ")} → ${GACT[g.action]}`).concat([`• everything else → ${GACT[p.default]}`]);
    if (!confirm(isAi ? `AI-sort mail from ${p.addr} in ${p.accounts.length} account(s)? The AI judges every existing email (every folder, every year; runs in the background) and each new one every 15 minutes:\n\n${lines.join("\n")}`
      : `Apply this plan to ${p.addr} in ${p.accounts.length} account(s)? It sorts existing mail (every folder, every year) and adds Gmail filters for future mail:\n\n${lines.join("\n")}`)) return;
    b.disabled = true; b.textContent = "Applying…";
    try {
      const r = await api("/api/ai/apply", { addr: p.addr, groups: p.groups, default: p.default, mode: p.mode || "words", rule: p.keep_rule || "" });
      $("#ai-result").innerHTML = `<div class="card"><b>Applied: ${esc(p.addr)}</b><ul>` + r.results.map((x) => `<li class="${x.ok ? "" : "err"}">${esc(x.email)}: ${esc(x.note)}</li>`).join("") + "</ul></div>";
    } catch (err) { $("#ai-result").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
    loadPlans();
  }
};
function loadPlansRender() { const open = new Set([...document.querySelectorAll("#ai-body .plan[data-open]")].map((x) => x.dataset.addr));
  $("#ai-body").innerHTML = S.plans.map((p, i) => planCard(p, i)).join(""); S.plans.forEach((p, i) => { if (open.has(p.addr)) showPreview(i); }); }
$("#ai-run").onclick = async () => {
  try { const r = await api("/api/ai/analyze", { limit: +$("#ai-limit").value }); if (r.started === false) alert("An analysis is already running."); }
  catch (err) { alert(err.message); }
  loadPlans();
};

/* ------------------------------------------------------------------ quarantine */
async function loadQuarantine() {
  $("#q-body").innerHTML = `<div class="empty">Loading…</div>`;
  let d;
  try { d = await api("/api/quarantine"); } catch (e) { $("#q-body").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  S.quar = d;
  renderQuarantine();
}
function renderQuarantine() {
  const d = S.quar;
  $("#q-count").textContent = d.total || "";
  $("#q-summary").textContent = d.total ? `${fmt(d.total)} emails held in "${d.label}" across your accounts` +
    (d.shown < d.total ? ` (newest ${fmt(d.shown)} listed below; sender and "all" buttons act on every one).` : ".")
    : `Nothing in "${d.label}". Use Clean up… > Quarantine on the Cleanup tab to hold a sender's mail here.`;
  $("#q-body").innerHTML = (d.errors || []).map((e) => `<div class="err">${esc(e.email)}: ${esc(e.error)}</div>`).join("") +
    d.senders.map((g, gi) => `<div class="qgroup"><div class="qh">
      <label><input type="checkbox" data-group="${gi}"></label>
      <span class="who"><b>${esc(g.name || g.addr)}</b> <span class="note">${esc(g.addr)} · ${g.count}${d.shown < d.total ? "+" : ""} email(s)</span><br>
        ${g.accounts.map((a) => acctChip(a.id, a.email)).join("")}</span>
      <button data-qa="release" data-g="${gi}" title="Back to the inbox, and stop quarantining this sender">Release sender</button>
      <button data-qa="archive" data-g="${gi}">Archive</button><button data-qa="delete" data-g="${gi}" class="danger">Delete</button></div>` +
      g.items.slice(0, g.open ? 1000 : 5).map((it, ii) => `<div class="qm"><input type="checkbox" data-g="${gi}" data-ii="${ii}">
        ${acctChip(it.account, it.email)}<span class="s">${esc(it.subject)} <span class="note">${esc(it.snippet)}</span></span>
        <span class="note">${when(it.ts)}</span></div>`).join("") +
      (g.items.length > 5 && !g.open ? `<div class="qm"><span></span><span></span><a href="#" data-more="${gi}">Show all ${g.count}</a><span></span></div>` : "") +
      "</div>").join("");
  selChanged();
}
function selected() {
  return [...document.querySelectorAll("#q-body input[data-ii]:checked")].map((c) => S.quar.senders[+c.dataset.g].items[+c.dataset.ii])
    .map((it) => ({ account: it.account, id: it.id }));
}
function selChanged() {
  const n = selected().length;
  $("#q-archive-sel").disabled = $("#q-delete-sel").disabled = !n;
  $("#q-archive-sel").textContent = `Archive selected${n ? ` (${n})` : ""}`;
  $("#q-delete-sel").textContent = `Delete selected${n ? ` (${n})` : ""}`;
}
async function qact(body, ask) {
  if (!confirm(ask)) return;
  $("#q-result").innerHTML = `<div class="note">Working…</div>`;
  try {
    const r = await api("/api/quarantine", body);
    $("#q-result").innerHTML = `<div class="card"><ul>` + (r.results.length ? r.results.map((x) => `<li class="${x.ok ? "" : "err"}">${esc(x.email)}: ${esc(x.note)}</li>`).join("")
      : "<li>Nothing to do.</li>") + "</ul></div>";
  } catch (e) { $("#q-result").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  loadQuarantine();
}
const QVERB = { delete: "Move to Trash", archive: "Archive (remove the label)", release: "Release back to the inbox" };
$("#q-body").onchange = (e) => {
  const g = e.target.dataset.group;
  if (g != null) document.querySelectorAll(`#q-body input[data-g="${g}"][data-ii]`).forEach((c) => { c.checked = e.target.checked; });
  selChanged();
};
$("#q-body").onclick = (e) => {
  const more = e.target.closest("a[data-more]");
  if (more) { e.preventDefault(); S.quar.senders[+more.dataset.more].open = true; return renderQuarantine(); }
  const b = e.target.closest("button[data-qa]");
  if (!b) return;
  const g = S.quar.senders[+b.dataset.g], a = b.dataset.qa;
  qact({ action: a, sender: g.addr }, `${QVERB[a]}: ALL quarantined email from ${g.addr} in every account?` +
    (a === "release" ? " New mail from them will reach the inbox again." : ""));
};
$("#q-refresh").onclick = loadQuarantine;
$("#q-archive-sel").onclick = () => { const it = selected(); qact({ action: "archive", items: it }, `Archive ${it.length} selected email(s)?`); };
$("#q-delete-sel").onclick = () => { const it = selected(); qact({ action: "delete", items: it }, `Move ${it.length} selected email(s) to Trash?`); };
$("#q-archive-all").onclick = () => qact({ action: "archive", all: true }, `Archive ALL ${S.quar?.total || 0} quarantined emails in every account?`);
$("#q-delete-all").onclick = () => qact({ action: "delete", all: true }, `Move ALL ${S.quar?.total || 0} quarantined emails in every account to Trash?`);

/* ------------------------------------------------------------------ analytics */
function hbars(rows, label, value, title) {
  const max = Math.max(1, ...rows.map(value));
  return rows.map((r) => `<div class="hbar" data-tip="${esc(title(r))}"><span class="lab">${label(r)}</span>
    <span class="track"><div class="fill" style="width:${(100 * value(r)) / max}%"></div></span><span class="val">${fmt(value(r))}</span></div>`).join("");
}

function dailyChart(daily, days) {
  const byDay = {};
  for (const d of daily) { (byDay[d.day] ||= { total: 0, parts: {} }); byDay[d.day].total += d.count; byDay[d.day].parts[d.email] = d.count; }
  const list = [];
  for (let i = days - 1; i >= 0; i--) { const dt = new Date(Date.now() - i * 864e5).toISOString().slice(0, 10); list.push({ day: dt, ...(byDay[dt] || { total: 0, parts: {} }) }); }
  const W = 900, H = 200, L = 40, B = 22, T = 8, max = Math.max(1, ...list.map((x) => x.total));
  const raw = max / 4, mag = Math.pow(10, Math.floor(Math.log10(raw))), step = [1, 2, 5, 10].map((m) => m * mag).find((v) => v >= raw);
  const top = Math.ceil(max / step) * step, nt = Math.round(top / step);
  const bw = (W - L) / list.length, gap = Math.min(2, bw * 0.25), y = (v) => T + (H - B - T) * (1 - v / top);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Emails received per day, all accounts">`;
  for (let k = 0; k <= nt; k++) { const v = step * k, yy = y(v);
    svg += `<line x1="${L}" x2="${W}" y1="${yy}" y2="${yy}" stroke="var(--grid)" stroke-width="1"/>
      <text x="${L - 6}" y="${yy + 4}" text-anchor="end" font-size="11" fill="var(--muted)">${fmt(Math.round(v))}</text>`; }
  list.forEach((d, i) => {
    const x = L + i * bw + gap / 2, w = Math.max(1, bw - gap), h = (H - B - T) * (d.total / top), yy = H - B - h, r = Math.min(4, w / 2, h);
    if (h > 0) svg += `<path d="M${x},${H - B} V${yy + r} Q${x},${yy} ${x + r},${yy} H${x + w - r} Q${x + w},${yy} ${x + w},${yy + r} V${H - B} Z" fill="var(--bar)"/>`;
    svg += `<rect x="${L + i * bw}" y="${T}" width="${bw}" height="${H - B - T}" fill="transparent" data-i="${i}"/>`;
    if (i % Math.ceil(list.length / 8) === 0) svg += `<text x="${x + w / 2}" y="${H - 6}" text-anchor="middle" font-size="11" fill="var(--muted)">${d.day.slice(5)}</text>`;
  });
  svg += "</svg>";
  return { svg, list };
}

async function loadAnalytics() {
  const days = +$("#a-days").value;
  let d;
  try { d = await api(`/api/analytics?days=${days}`); } catch (e) { $("#a-body").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const t = d.totals, cv = d.coverage;
  $("#a-cover").textContent = `Mail history: ${cv.backfilled}/${cv.accounts} accounts fully collected${cv.since ? `, since ${cv.since.slice(0, 10)}` : ""}.`;
  const tile = (v, k) => `<div class="tile"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  const chart = dailyChart(d.daily, days);
  $("#a-body").innerHTML = `<div class="tiles">${tile(fmt(t.received), "emails received")}${tile(t.readPct == null ? "–" : t.readPct + "%", "read")}
      ${tile(fmt(t.inboxNow), "in inboxes now")}${tile(fmt(t.senders), "senders")}${tile(fmt(t.ruleActions), "rule actions by Inbox Zero")}
      ${tile(fmt(t.unsubscribed + t.autoArchived), "senders unsubscribed / auto-archived")}</div>
    <div class="card"><h3>Emails received per day, all accounts</h3><div id="a-chart">${chart.svg}</div></div>
    <div class="card wide"><h3>By account</h3><table><thead><tr><th>Account</th><th class="num">Received</th><th class="num">Read</th>
      <th class="num">Senders</th><th class="num">In inbox now</th><th class="num">Unread threads now</th></tr></thead><tbody>` +
      d.accounts.map((a) => `<tr><td>${esc(a.email)}</td><td class="num">${fmt(a.received)}</td><td class="num">${a.readPct == null ? "–" : a.readPct + "%"}</td>
        <td class="num">${fmt(a.senders)}</td><td class="num">${fmt(a.inboxNow)}</td><td class="num">${fmt(a.unreadThreadsNow)}</td></tr>`).join("") + `</tbody></table></div>
    <div class="grid2"><div class="card"><h3>Top senders</h3>${hbars(d.topSenders, (r) => esc(r.name || r.addr), (r) => r.count,
        (r) => `${r.addr}: ${fmt(r.count)} emails, ${fmt(r.unread)} unread, ${r.accounts} account(s)`) || '<div class="note">No data yet.</div>'}</div>
      <div class="card"><h3>Top domains</h3>${hbars(d.topDomains, (r) => esc(r.domain), (r) => r.count,
        (r) => `${r.domain}: ${fmt(r.count)} emails from ${fmt(r.senders)} senders, ${fmt(r.unread)} unread`) || '<div class="note">No data yet.</div>'}</div></div>
    <div class="card"><h3>What Inbox Zero's rules did</h3>${hbars(d.rules, (r) => esc(r.name), (r) => r.total,
        (r) => `${r.name}: ` + Object.entries(r.accounts).map(([e, n]) => `${short(e)} ${n}`).join(", ")) || '<div class="note">No rule actions in this period.</div>'}</div>`;
  const svg = $("#a-chart svg");
  svg.onmousemove = (e) => { const i = e.target.dataset?.i; if (i == null) return hideTip(); const x = chart.list[+i];
    showTip(e, `<b>${x.day}: ${fmt(x.total)} emails</b><br>` + Object.entries(x.parts).sort((a, b) => b[1] - a[1]).map(([em, n]) => `${esc(short(em))}: ${fmt(n)}`).join("<br>")); };
  svg.onmouseleave = hideTip;
  $("#a-body").onmousemove = (e) => { const h = e.target.closest(".hbar"); if (h) showTip(e, esc(h.dataset.tip)); else if (!e.target.closest("svg")) hideTip(); };
}
$("#a-days").onchange = loadAnalytics;

/* ------------------------------------------------------------------ rules */
const OPS = { create: "create", adopt: "adopt existing", update: "update", delete: "delete", error: "error" };
async function loadRules() {
  let d;
  try { d = await api("/api/rules"); } catch (e) { $("#r-matrix").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (d.error) { $("#r-matrix").innerHTML = `<div class="err">${esc(d.error)}</div>`; return; }
  setSlots([d.master, ...d.accounts]);
  $("#r-master").innerHTML = `Rules are written in Inbox Zero on <b>${esc(d.master.email)}</b>. Tick a rule under an account to share it there; nothing is shared unless ticked.
    ${S.izUrl ? `<a href="${esc(S.izUrl)}" target="_blank" rel="noopener">Edit rules ↗</a>` : ""}`;
  $("#r-auto").checked = d.auto;
  $("#r-matrix").innerHTML = d.rules.length ? `<table class="matrix"><thead><tr><th>Master rule</th>` +
    d.accounts.map((a) => `<th class="num" title="${esc(a.email)}">${acctChip(a.id, a.email)}</th>`).join("") + `</tr></thead><tbody>` +
    d.rules.map((r) => `<tr><td><b>${esc(r.name)}</b>${r.enabled ? "" : ' <span class="note">(off)</span>'}<br>
      <span class="note">${esc(r.actions.join(", ").toLowerCase())}</span></td>` +
      d.accounts.map((a) => { const st = r.cells[a.id];
        return `<td class="cell"><label title="Copy this rule to ${esc(a.email)}"><input type="checkbox" data-r="${esc(r.id)}" data-a="${esc(a.id)}" ${st === "off" || st === "removing" ? "" : "checked"}>
          ${st === "off" ? "" : `<span class="st ${st}">${st}</span>`}</label></td>`; }).join("") + "</tr>").join("") + "</tbody></table>"
    : `<div class="empty">The master account has no rules yet: create them in Inbox Zero.</div>`;
  $("#r-log").innerHTML = d.log.length ? `<div class="card"><h3>Recent sync activity</h3><ul class="ops">` +
    d.log.map((l) => `<li class="${l.kind.includes("error") ? "err" : ""}"><span class="note">${esc(l.at.slice(0, 16).replace("T", " "))}</span>
      ${esc(l.account || "")}: ${esc(l.msg)}</li>`).join("") + "</ul></div>" : "";
}
function showOps(res) {
  const ops = res.ops.filter((o) => o.op !== "same");
  $("#r-ops").innerHTML = `<div class="card"><h3>${res.applied ? "Synced" : "Preview"}: ${ops.length ? `${ops.length} change(s)` : "everything is up to date"}</h3>` +
    (ops.length ? `<ul class="ops">` + ops.map((o) => `<li class="${o.op === "error" ? "err" : ""}"><b>${esc(OPS[o.op] || o.op)}</b> ${esc(o.rule)}
      <span class="note">on ${esc(o.account)}${o.detail ? ` · ${esc(o.detail)}` : ""}</span></li>`).join("") + "</ul>" : "") + "</div>";
}
$("#r-preview").onclick = async () => { try { showOps(await api("/api/rules/preview")); } catch (e) { $("#r-ops").innerHTML = `<div class="err">${esc(e.message)}</div>`; } };
$("#r-sync").onclick = async (e) => { e.target.disabled = true;
  try { showOps(await api("/api/rules/sync", {})); } catch (err) { $("#r-ops").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  e.target.disabled = false; loadRules(); };
$("#r-auto").onchange = (e) => api("/api/rules/auto", { on: e.target.checked }).catch((err) => alert(err.message));
$("#r-matrix").onchange = async (e) => {
  const c = e.target.closest("input[data-r]");
  if (!c) return;
  await api("/api/rules/include", { rule: c.dataset.r, account: c.dataset.a, include: c.checked }).catch((err) => alert(err.message));
  loadRules();
};

api("/api/config").then((c) => { S.izUrl = c.inboxZeroUrl; S.ai = !!c.ai; if (c.inboxZeroUrl) { const a = $("#iz-link"); a.href = c.inboxZeroUrl; a.hidden = false; } }).catch(() => {});
loadAccounts().then(() => loadList());
api("/api/quarantine").then((d) => { $("#q-count").textContent = d.total || ""; }).catch(() => {});
setInterval(() => { if (!document.hidden && S.tab === "inbox") { loadAccounts(); loadList(true); } }, 120000);
