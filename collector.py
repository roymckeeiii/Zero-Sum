"""The collector that feeds cleanup and analytics: every 15 minutes, per account, it stores message metadata (sender,
date, read/inbox state, List-Unsubscribe, subject, preview) in unified.msg: new mail since the last run, a back-fill
of the whole history (or the last BACKFILL_DAYS) a batch at a time, and a refresh of read/inbox state for the last
30 days. Sent mail, drafts and chats are skipped."""
import email.utils, os, re, threading, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor

from core import ACC, db, gmail, gmail_list, header, log, set_setting, setting

BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "0"))  # 0 = back to the account's first email: old mail matters
BACKFILL_STEP = int(os.environ.get("BACKFILL_STEP", "2000"))
SKIP = "-in:sent -in:drafts -in:chats"
NEW_MAIL_MAX = 100000  # new-mail pass: bounded by its `after:` date, not by a count
CPOOL = ThreadPoolExecutor(max_workers=8)
COLLECT_LOCK = threading.Lock()


def qs(query):
    return "/messages?q=" + urllib.parse.quote(query)


def parse_unsub(value):
    links = re.findall(r"<([^>]+)>", value or "")
    https = next((u for u in links if u.lower().startswith("https://")), None)
    mailto = next((u for u in links if u.lower().startswith("mailto:")), None)
    return https or mailto


def _store(acc_id, ids, refill=False):
    if not ids:
        return 0
    with db() as c:
        have = {r[0] for r in c.execute("SELECT id FROM unified.msg WHERE account_id = %s AND id = ANY(%s)",
                                        (acc_id, ids)).fetchall()}
    todo = [i for i in ids if i not in have]
    if refill:  # mail collected before subjects were stored: fetch it again to fill subject + snippet
        todo = list(ids)

    def fetch(mid):
        try:
            m = gmail(acc_id, f"/messages/{mid}?format=metadata&metadataHeaders=From&metadataHeaders=List-Unsubscribe"
                              "&metadataHeaders=List-Unsubscribe-Post&metadataHeaders=Subject")
        except Exception as e:  # noqa: BLE001
            if ": 404 " in str(e):  # deleted between list and get: nothing to store
                return None
            return e  # quota, 5xx, timeout: not "gone". Reported below so the collector does not move past it.
        hs = m.get("payload", {}).get("headers", [])
        name, addr = email.utils.parseaddr(header(hs, "from"))
        addr = (addr or "unknown").lower()
        lab = m.get("labelIds", [])
        return (acc_id, m["id"], m["threadId"], int(m.get("internalDate", 0)) / 1000, addr, name or None,
                addr.rsplit("@", 1)[-1], "UNREAD" in lab, "INBOX" in lab, parse_unsub(header(hs, "list-unsubscribe")),
                "one-click" in header(hs, "list-unsubscribe-post").lower(), header(hs, "subject")[:300],
                (m.get("snippet") or "")[:300])
    got = [r for r in CPOOL.map(fetch, todo) if r]
    rows, failed = [r for r in got if isinstance(r, tuple)], [r for r in got if not isinstance(r, tuple)]
    with db() as c:
        with c.cursor() as cur:
            cur.executemany("INSERT INTO unified.msg (account_id, id, thread_id, ts, from_addr, from_name, domain, unread, inbox, "
                            "unsub, unsub_post, subject, snippet) VALUES (%s, %s, %s, to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                            "ON CONFLICT (account_id, id) DO UPDATE SET subject = EXCLUDED.subject, snippet = EXCLUDED.snippet", rows)
    if failed:  # what was fetched is kept; the run stops here so newest/oldest stay put and the next run lists these again
        raise RuntimeError(f"{len(failed)} of {len(todo)} messages could not be read ({str(failed[0])[:160]})")
    return len(rows)


def collect_account(acc):
    a = acc["id"]
    with db() as c:
        st = c.execute("SELECT extract(epoch from newest), extract(epoch from oldest), backfill_done "
                       "FROM unified.collect_state WHERE account_id = %s", (a,)).fetchone()
    newest, oldest, done = (st or (None, None, False))
    if newest is None:
        ids = [m["id"] for m in gmail_list(a, qs(f"{SKIP} newer_than:7d"), "messages", NEW_MAIL_MAX)]
    else:  # everything since the last run: the list is newest first, so a cap of 2000 dropped the older part of a
        # long gap (downtime, a burst) for good, since back-fill only ever reads before `oldest`
        ids = [m["id"] for m in gmail_list(a, qs(f"{SKIP} after:{int(newest) - 3600}"),
                                           "messages", NEW_MAIL_MAX)]
    added = _store(a, ids)
    if not done:
        before = int(oldest) if oldest else int(time.time()) - 7 * 86400
        age = f" newer_than:{BACKFILL_DAYS}d" if BACKFILL_DAYS > 0 else ""
        older = [m["id"] for m in gmail_list(a, qs(f"{SKIP} before:{before}{age}"), "messages", BACKFILL_STEP)]
        added += _store(a, older)
        done = len(older) < BACKFILL_STEP
    # mail collected before subjects were stored: fill BACKFILL_STEP of them per run, newest first
    with db() as c:
        missing = [r[0] for r in c.execute("SELECT id FROM unified.msg WHERE account_id = %s AND subject IS NULL "
                                           "ORDER BY ts DESC LIMIT %s", (a, BACKFILL_STEP)).fetchall()]
    if missing:
        _store(a, missing, refill=True)
    # read / inbox state can change after collection: refresh the last 30 days from two cheap id lists. `newest` is
    # never later than now: one message dated in the future (mail imported with a wrong Date header) would otherwise
    # make the next new-mail pass search after that date and miss everything arriving until then.
    inbox = [m["id"] for m in gmail_list(a, qs("in:inbox newer_than:30d"), "messages", 10000)]
    unread = [m["id"] for m in gmail_list(a, qs("is:unread newer_than:30d"), "messages", 10000)]
    with db() as c:
        c.execute("UPDATE unified.msg SET inbox = (id = ANY(%s)), unread = (id = ANY(%s)) "
                  "WHERE account_id = %s AND ts > now() - interval '30 days'", (inbox, unread, a))
        c.execute("INSERT INTO unified.collect_state (account_id, newest, oldest, backfill_done, updated_at, error) "
                  "SELECT %s, least(max(ts), now()), min(ts), %s, now(), NULL FROM unified.msg WHERE account_id = %s "
                  "ON CONFLICT (account_id) DO UPDATE SET newest = EXCLUDED.newest, oldest = EXCLUDED.oldest, "
                  "backfill_done = EXCLUDED.backfill_done, updated_at = now(), error = NULL", (a, done, a))
    return added


def _widen_backfill():
    """Back-fills marked done under an older, shorter BACKFILL_DAYS (e.g. 180 days) resume when the limit is raised or
    removed (0 = all history): the stored horizon is compared with the current one."""
    now_h = BACKFILL_DAYS if BACKFILL_DAYS > 0 else 10 ** 6
    old = setting("backfill_horizon")
    if old is None or int(old) < now_h:
        with db() as c:
            c.execute("UPDATE unified.collect_state SET backfill_done = false")
        set_setting("backfill_horizon", now_h)
        print(f"[collect] history limit {old or 'unset'} -> {BACKFILL_DAYS or 'all'} days: back-fill resumed", flush=True)
    elif int(old) > now_h:
        set_setting("backfill_horizon", now_h)


def collect_all():
    if not COLLECT_LOCK.acquire(blocking=False):
        return
    try:
        _widen_backfill()
        for acc in ACC.list():
            try:
                n = collect_account(acc)
                if n:
                    print(f"[collect] {acc['email']}: +{n}", flush=True)
            except Exception as e:  # noqa: BLE001
                if "quota exceeded" in str(e).lower():  # shared per-user Gmail quota (Inbox Zero uses it too): next run
                    print(f"[collect] {acc['email']}: Gmail quota busy, continuing next run", flush=True)
                    continue
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
