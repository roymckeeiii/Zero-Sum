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
  if (!loaded[S.tab]) { loaded[S.tab] = true; ({ cleanup: loadSenders, analytics: loadAnalytics, rules: loadRules })[S.tab]?.(); }
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
const STATUS = { UNSUBSCRIBED: "unsubscribed", AUTO_ARCHIVED: "auto-archived", APPROVED: "kept" };
const LABELS = { unsubscribe: "Unsubscribe", autoarchive: "Auto-archive", archive: "Archive", delete: "Delete all", block: "Block", spam: "Spam" };
const TIPS = { unsubscribe: "Unsubscribe (one-click where offered), skip the inbox from now on, archive what is there",
  autoarchive: "Skip the inbox from now on (Gmail filter), and archive what is there", archive: "Archive what is in the inbox now",
  delete: "Move all mail from this sender to Trash (Gmail empties Trash after 30 days)",
  block: "Move all mail from this sender to Trash, and send future mail straight to Trash (Gmail filter)",
  spam: "Report all mail from this sender as spam (also trains Gmail's spam filter)" };
const CONFIRM = { delete: "Move ALL mail from", block: "Move ALL mail from", spam: "Report ALL mail from" };
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
    d.senders.map((s, i) => `<tr><td><b>${esc(s.name || s.addr)}</b><br><span class="note">${esc(s.addr)}</span></td>
      <td class="num">${fmt(s.count)}</td><td class="num">${s.readPct}%</td><td class="num">${fmt(s.inbox)}</td>
      <td>${s.accounts.map((a) => `${acctChip(a.id, a.email)}${a.status ? `<span class="note">${STATUS[a.status] || a.status}</span> ` : ""}`).join("")}</td>
      <td class="note">${when(s.last)}</td>
      <td class="acts-cell">${s.protected ? '<span class="chip prot">protected</span> ' : ""}
        <div class="grp"><button data-i="${i}" data-a="${s.protected ? "unprotect" : "protect"}" title="Protected senders are never archived, deleted or blocked in bulk">${s.protected ? "Unprotect" : "Protect"}</button>
          <button data-i="${i}" data-a="star" title="Star what is in the inbox, and future mail (Gmail filter)">Auto-star</button>
          <button data-i="${i}" data-a="unstar" title="Remove the auto-star filter and the stars">Un-star</button>
          <button data-i="${i}" data-a="keep" title="Mark as wanted in Inbox Zero (hides it here)">Keep</button></div>
        <div class="grp">${["unsubscribe", "autoarchive", "archive", "delete", "block", "spam"].map((a) => `<button data-i="${i}" data-a="${a}"
          ${s.protected ? "disabled title='Protected sender'" : `title="${esc(TIPS[a])}"`} class="${["delete", "block", "spam"].includes(a) ? "danger" : ""}">${LABELS[a]}</button>`).join("")}</div></td></tr>`).join("") + "</tbody></table>";
  $("#c-prot").innerHTML = d.protected.length ? "Protected: " + d.protected.map((k) => `<span class="chip prot">${esc(k)}
    <button class="x" data-unprot="${esc(k)}" title="Unprotect">×</button></span>`).join("") : "";
}
$("#c-table").onclick = async (e) => {
  const b = e.target.closest("button[data-a]");
  if (!b) return;
  const s = S.senders[+b.dataset.i], a = b.dataset.a;
  if (CONFIRM[a] && !confirm(`${CONFIRM[a]} ${s.addr} (${s.count} recent emails across ${s.accounts.length} account(s))` +
    (a === "spam" ? " as spam?" : " to Trash?") + (a === "block" ? " Future mail from this sender will go straight to Trash." : ""))) return;
  b.disabled = true; b.textContent = "…";
  try {
    const r = await api("/api/sender", { addr: s.addr, action: b.dataset.a });
    $("#c-result").innerHTML = `<div class="card"><b>${esc(b.dataset.a)} ${esc(s.addr)}</b><ul>` +
      r.results.map((x) => `<li class="${x.ok ? "" : "err"}">${esc(x.email)}: ${esc(x.note)}</li>`).join("") + "</ul>" +
      (r.manual.length ? `<p>Finish unsubscribing by opening these (one per account):</p><ul>` +
        r.manual.map((m) => `<li>${esc(m.email)}: <a href="${esc(m.url)}" target="_blank" rel="noopener noreferrer">${esc(m.url.slice(0, 80))}</a></li>`).join("") + "</ul>" : "") + "</div>";
  } catch (err) { $("#c-result").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  loadSenders();
};
let ct;
$("#c-q").oninput = () => { clearTimeout(ct); ct = setTimeout(loadSenders, 400); };
$("#c-days").onchange = loadSenders; $("#c-handled").onchange = loadSenders;
$("#c-prot").onclick = async (e) => {
  const x = e.target.closest("button[data-unprot]");
  if (!x) return;
  await api("/api/sender", { addr: x.dataset.unprot, action: "unprotect" }).catch((err) => alert(err.message));
  loadSenders();
};
$("#c-protect-btn").onclick = async () => {
  const v = $("#c-protect").value.trim().toLowerCase();
  if (!v) return;
  try { await api("/api/sender", { addr: v, action: "protect" }); $("#c-protect").value = ""; } catch (err) { alert(err.message); }
  loadSenders();
};
$("#c-collect").onclick = async () => { await api("/api/collect", {}).catch(() => {}); $("#c-status").textContent = "Collecting… refresh in a minute."; };

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

api("/api/config").then((c) => { S.izUrl = c.inboxZeroUrl; if (c.inboxZeroUrl) { const a = $("#iz-link"); a.href = c.inboxZeroUrl; a.hidden = false; } }).catch(() => {});
loadAccounts().then(() => loadList());
setInterval(() => { if (!document.hidden && S.tab === "inbox") { loadAccounts(); loadList(true); } }, 120000);
