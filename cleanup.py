"""Merged email cleanup (Inbox Zero's bulk unsubscribe / archive, across every linked account) and the collector that
feeds it and the analytics.

Collector: every 15 minutes, per account, stores message metadata (sender, date, read/inbox state, List-Unsubscribe)
in unified.msg: new mail since the last run, a backfill of the last BACKFILL_DAYS a few hundred messages at a time,
and a refresh of read/inbox state for the last 30 days. Sent mail, drafts and chats are skipped.

Sender actions apply to every account that has mail from the sender (or the accounts given), and mirror what Inbox
Zero does for one account: Newsletter.status (UNSUBSCRIBED / AUTO_ARCHIVED / APPROVED, by bare address), a Gmail
filter `from:<sender>` that skips the inbox, and archiving what is already in the inbox. Unsubscribing uses RFC 8058
one-click POST when the sender supports it; other links (web pages, mailto) are returned for Roy to open, since this
app never sends email.
"""
import email.utils, os, re, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

from core import ACC, db, gmail, gmail_list, header, log, new_id

BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "180"))
BACKFILL_STEP = int(os.environ.get("BACKFILL_STEP", "400"))
SKIP = "-in:sent -in:drafts -in:chats"
CPOOL = ThreadPoolExecutor(max_workers=8)
COLLECT_LOCK = threading.Lock()


def qs(query):
    return "/messages?q=" + urllib.parse.quote(query)


def parse_unsub(value):
    links = re.findall(r"<([^>]+)>", value or "")
    https = next((u for u in links if u.lower().startswith("https://")), None)
    mailto = next((u for u in links if u.lower().startswith("mailto:")), None)
    return https or mailto


def _store(acc_id, ids):
    if not ids:
        return 0
    with db() as c:
        have = {r[0] for r in c.execute("SELECT id FROM unified.msg WHERE account_id = %s AND id = ANY(%s)",
                                        (acc_id, ids)).fetchall()}
    todo = [i for i in ids if i not in have]

    def fetch(mid):
        try:
            m = gmail(acc_id, f"/messages/{mid}?format=metadata&metadataHeaders=From&metadataHeaders=List-Unsubscribe"
                              "&metadataHeaders=List-Unsubscribe-Post")
        except Exception:  # noqa: BLE001 - deleted between list and get
            return None
        hs = m.get("payload", {}).get("headers", [])
        name, addr = email.utils.parseaddr(header(hs, "from"))
        addr = (addr or "unknown").lower()
        lab = m.get("labelIds", [])
        return (acc_id, m["id"], m["threadId"], int(m.get("internalDate", 0)) / 1000, addr, name or None,
                addr.rsplit("@", 1)[-1], "UNREAD" in lab, "INBOX" in lab, parse_unsub(header(hs, "list-unsubscribe")),
                "one-click" in header(hs, "list-unsubscribe-post").lower())
    rows = [r for r in CPOOL.map(fetch, todo) if r]
    with db() as c:
        with c.cursor() as cur:
            cur.executemany("INSERT INTO unified.msg VALUES (%s, %s, %s, to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s) "
                            "ON CONFLICT DO NOTHING", rows)
    return len(rows)


def collect_account(acc):
    a = acc["id"]
    with db() as c:
        st = c.execute("SELECT extract(epoch from newest), extract(epoch from oldest), backfill_done "
                       "FROM unified.collect_state WHERE account_id = %s", (a,)).fetchone()
    newest, oldest, done = (st or (None, None, False))
    if newest is None:
        ids = [m["id"] for m in gmail_list(a, qs(f"{SKIP} newer_than:7d"), "messages", 2000)]
    else:
        ids = [m["id"] for m in gmail_list(a, qs(f"{SKIP} after:{int(newest) - 3600}"),
                                           "messages", 2000)]
    added = _store(a, ids)
    if not done:
        before = int(oldest) if oldest else int(time.time()) - 7 * 86400
        older = [m["id"] for m in gmail_list(a, qs(f"{SKIP} before:{before} newer_than:{BACKFILL_DAYS}d"), "messages", BACKFILL_STEP)]
        added += _store(a, older)
        done = len(older) < BACKFILL_STEP
    # read / inbox state can change after collection: refresh the last 30 days from two cheap id lists
    inbox = [m["id"] for m in gmail_list(a, qs("in:inbox newer_than:30d"), "messages", 10000)]
    unread = [m["id"] for m in gmail_list(a, qs("is:unread newer_than:30d"), "messages", 10000)]
    with db() as c:
        c.execute("UPDATE unified.msg SET inbox = (id = ANY(%s)), unread = (id = ANY(%s)) "
                  "WHERE account_id = %s AND ts > now() - interval '30 days'", (inbox, unread, a))
        c.execute("INSERT INTO unified.collect_state (account_id, newest, oldest, backfill_done, updated_at, error) "
                  "SELECT %s, max(ts), min(ts), %s, now(), NULL FROM unified.msg WHERE account_id = %s "
                  "ON CONFLICT (account_id) DO UPDATE SET newest = EXCLUDED.newest, oldest = EXCLUDED.oldest, "
                  "backfill_done = EXCLUDED.backfill_done, updated_at = now(), error = NULL", (a, done, a))
    return added


def collect_all():
    if not COLLECT_LOCK.acquire(blocking=False):
        return
    try:
        for acc in ACC.list():
            try:
                n = collect_account(acc)
                if n:
                    print(f"[collect] {acc['email']}: +{n}", flush=True)
            except Exception as e:  # noqa: BLE001
                log("collect-error", str(e)[:500], acc["email"])
                with db() as c:
                    c.execute("INSERT INTO unified.collect_state (account_id, error) VALUES (%s, %s) "
                              "ON CONFLICT (account_id) DO UPDATE SET error = EXCLUDED.error, updated_at = now()",
                              (acc["id"], str(e)[:300]))
    finally:
        COLLECT_LOCK.release()


def collect_status():
    with db() as c:
        rows = {r[0]: r[1:] for r in c.execute(
            "SELECT s.account_id, s.backfill_done, s.oldest, s.updated_at, s.error, "
            "(SELECT count(*) FROM unified.msg m WHERE m.account_id = s.account_id) FROM unified.collect_state s").fetchall()}
    out = []
    for a in ACC.list():
        r = rows.get(a["id"])
        out.append({"email": a["email"], "messages": r[4] if r else 0, "backfill_done": bool(r and r[0]),
                    "oldest": r[1].isoformat() if r and r[1] else None,
                    "updated": r[2].isoformat() if r and r[2] else None, "error": r[3] if r else None})
    return out


# ---------------------------------------------------------------- senders
def senders(q):
    days = max(7, min(int(q.get("days", ["90"])[0]), BACKFILL_DAYS))
    search = q.get("q", [""])[0].strip().lower()
    hide = q.get("hide", ["1"])[0] == "1"
    accs = {a["id"]: a["email"] for a in ACC.list()}
    with db() as c:
        rows = c.execute(
            "SELECT from_addr, max(from_name), min(domain), count(*), sum(unread::int), sum(inbox::int), "
            "extract(epoch from max(ts)), array_agg(DISTINCT account_id), bool_or(unsub IS NOT NULL) "
            "FROM unified.msg WHERE ts > now() - make_interval(days => %s) "
            "AND (%s = '' OR from_addr LIKE %s OR lower(coalesce(from_name, '')) LIKE %s) "
            "GROUP BY from_addr ORDER BY count(*) DESC LIMIT 400",
            (days, search, f"%{search}%", f"%{search}%")).fetchall()
        addrs = [r[0] for r in rows]
        status = {}
        for acc, em, st in c.execute('SELECT "emailAccountId", lower(email), status::text FROM "Newsletter" '
                                     'WHERE lower(email) = ANY(%s) AND status IS NOT NULL', (addrs,)).fetchall():
            status.setdefault(em, {})[acc] = st
    out = []
    for addr, name, dom, n, unread, inbox, last, acc_ids, has_unsub in rows:
        st = status.get(addr, {})
        handled = bool(st) and all(acc in st for acc in acc_ids)
        if hide and handled:
            continue
        out.append({"addr": addr, "name": name, "domain": dom, "count": n, "unread": unread, "inbox": inbox,
                    "readPct": round(100 * (n - unread) / n) if n else 0, "last": int(last),
                    "accounts": [{"id": a, "email": accs.get(a, "?"), "status": st.get(a)} for a in acc_ids],
                    "unsub": has_unsub})
    return {"senders": out, "collect": collect_status()}


def _newsletter(c, acc_id, addr, status):
    c.execute('INSERT INTO "Newsletter" (id, "updatedAt", email, status, "emailAccountId") '
              'VALUES (%s, now(), %s, %s::"NewsletterStatus", %s) ON CONFLICT (email, "emailAccountId") '
              'DO UPDATE SET status = EXCLUDED.status, "updatedAt" = now()', (new_id(), addr, status, acc_id))


def _filter(acc_id, addr):
    try:
        gmail(acc_id, "/settings/filters", "POST", {"criteria": {"from": addr}, "action": {"removeLabelIds": ["INBOX"]}})
    except RuntimeError as e:
        if "exists" not in str(e).lower():
            raise


def _archive_existing(acc_id, addr):
    ids = [m["id"] for m in gmail_list(acc_id, qs(f"from:{addr} in:inbox"), "messages", 5000)]
    for i in range(0, len(ids), 1000):
        gmail(acc_id, "/messages/batchModify", "POST", {"ids": ids[i:i + 1000], "removeLabelIds": ["INBOX"]})
    with db() as c:
        c.execute("UPDATE unified.msg SET inbox = false WHERE account_id = %s AND from_addr = %s", (acc_id, addr))
    return len(ids)


def _one_click(url):
    req = urllib.request.Request(url, data=b"List-Unsubscribe=One-Click", method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": "Mozilla/5.0 (unsubscribe)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status < 400
    except urllib.error.HTTPError as e:
        return e.code < 400
    except Exception:  # noqa: BLE001
        return False


def sender_action(d):
    addr, act = (d.get("addr") or "").strip().lower(), d.get("action")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+", addr) or act not in ("unsubscribe", "autoarchive", "archive", "keep", "reset"):
        raise ValueError("bad sender action")
    with db() as c:
        acc_ids = [r[0] for r in c.execute("SELECT DISTINCT account_id FROM unified.msg WHERE from_addr = %s",
                                           (addr,)).fetchall()]
        unsub = {a: (u, p) for a, u, p in c.execute(
            "SELECT DISTINCT ON (account_id) account_id, unsub, unsub_post FROM unified.msg "
            "WHERE from_addr = %s AND unsub IS NOT NULL ORDER BY account_id, ts DESC", (addr,)).fetchall()}
    if d.get("accounts"):
        acc_ids = [a for a in acc_ids if a in set(d["accounts"])]
    results, manual = [], []
    for a in acc_ids:
        em = ACC.email(a)
        try:
            note = []
            if act == "unsubscribe":
                u, post = unsub.get(a, (None, False))
                if u and u.startswith("https://") and post and _one_click(u):
                    note.append("unsubscribed (one-click)")
                elif u:
                    manual.append({"email": em, "url": u})
                    note.append("open the link to finish unsubscribing")
                else:
                    note.append("no unsubscribe link: blocked with a filter instead")
            if act in ("unsubscribe", "autoarchive"):
                _filter(a, addr)
                n = _archive_existing(a, addr)
                note.append(f"filter added, {n} archived")
            if act == "archive":
                note.append(f"{_archive_existing(a, addr)} archived")
            status = {"unsubscribe": "UNSUBSCRIBED", "autoarchive": "AUTO_ARCHIVED", "keep": "APPROVED"}.get(act)
            with db() as c:
                if status:
                    _newsletter(c, a, addr, status)
                elif act == "reset":
                    c.execute('UPDATE "Newsletter" SET status = NULL, "updatedAt" = now() '
                              'WHERE lower(email) = %s AND "emailAccountId" = %s', (addr, a))
            results.append({"email": em, "ok": True, "note": "; ".join(note) or act})
        except Exception as e:  # noqa: BLE001
            results.append({"email": em, "ok": False, "note": str(e)[:200]})
    log("cleanup", f"{act} {addr}: " + "; ".join(f"{r['email']}: {r['note']}" for r in results))
    return {"results": results, "manual": manual}
