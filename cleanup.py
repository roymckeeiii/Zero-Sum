"""Merged email cleanup (Inbox Zero's bulk unsubscribe / archive, across every linked account) and the collector that
feeds it and the analytics.

Collector: every 15 minutes, per account, stores message metadata (sender, date, read/inbox state, List-Unsubscribe)
in unified.msg: new mail since the last run, a backfill of the whole history (or the last BACKFILL_DAYS) a batch at a time,
and a refresh of read/inbox state for the last 30 days. Sent mail, drafts and chats are skipped.

Sender actions apply to every account that has mail from the sender (or the accounts given), and mirror what Inbox
Zero does for one account: Newsletter.status (UNSUBSCRIBED / AUTO_ARCHIVED / APPROVED, by bare address), a Gmail
filter `from:<sender>` that skips the inbox, and archiving what is already in the inbox. Unsubscribing uses RFC 8058
one-click POST when the sender supports it; other links (web pages, mailto) are returned for Roy to open, since this
app never sends email.
"""
import email.utils, os, re, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

from core import ACC, POOL, db, gmail, gmail_list, header, label_id, log, new_id

BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "0"))  # 0 = back to the account's first email: old mail matters
BACKFILL_STEP = int(os.environ.get("BACKFILL_STEP", "2000"))
MAX_BULK = int(os.environ.get("MAX_BULK", "200000"))  # most messages one bulk action touches per account
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


def _widen_backfill():
    """Back-fills marked done under an older, shorter BACKFILL_DAYS (e.g. 180 days) resume when the limit is raised or
    removed (0 = all history): the stored horizon is compared with the current one."""
    from core import set_setting, setting
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


# ---------------------------------------------------------------- senders
def senders(q):
    days = max(7, min(int(q.get("days", ["90"])[0]), BACKFILL_DAYS or 36500))  # up to "all time"
    search = q.get("q", [""])[0].strip().lower()
    hide = q.get("hide", ["1"])[0] == "1"
    accs = {a["id"]: a["email"] for a in ACC.list()}
    protset = protected_set()
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
        prot = is_protected(addr, protset)
        handled = bool(st) and all(acc in st for acc in acc_ids)
        if hide and handled and not prot:
            continue
        out.append({"addr": addr, "name": name, "domain": dom, "count": n, "unread": unread, "inbox": inbox,
                    "readPct": round(100 * (n - unread) / n) if n else 0, "last": int(last),
                    "accounts": [{"id": a, "email": accs.get(a, "?"), "status": st.get(a)} for a in acc_ids],
                    "unsub": has_unsub, "protected": prot})
    return {"senders": out, "collect": collect_status(), "protected": sorted(protset)}


def _newsletter(c, acc_id, addr, status):
    c.execute('INSERT INTO "Newsletter" (id, "updatedAt", email, status, "emailAccountId") '
              'VALUES (%s, now(), %s, %s::"NewsletterStatus", %s) ON CONFLICT (email, "emailAccountId") '
              'DO UPDATE SET status = EXCLUDED.status, "updatedAt" = now()', (new_id(), addr, status, acc_id))


# ---------------------------------------------------------------- protection
def protected_set():
    with db() as c:
        return {r[0] for r in c.execute("SELECT key FROM unified.protected").fetchall()}


def is_protected(addr, prot=None):
    """A sender is protected by its exact address or its domain ("@example.com")."""
    prot = protected_set() if prot is None else prot
    if addr in prot:
        return True
    labels = addr.rsplit("@", 1)[-1].split(".")  # "@chase.com" also covers alerts.chase.com, never a bare TLD
    return any("@" + ".".join(labels[i:]) in prot for i in range(max(1, len(labels) - 1)))


def own_address(addr):
    """True when addr is one of the linked accounts: `from:<me>` matches everything that account ever sent."""
    return addr in {a["email"].lower() for a in ACC.list()}


PROTECT_KEY = re.compile(r"[^@\s,;]*@[^@\s,;]+\.[^@\s,;]+")


def parse_protect_keys(text):
    """A pasted list of senders -> (keys, rejected). One or more per line, separated by spaces or semicolons; on a
    CSV line only the first column counts (so a keep-list export with notes and dates pastes as it is). An address
    stays an address, "@example.com" a domain, and a bare "example.com" becomes "@example.com"."""
    keys, rejected = [], []
    for line in str(text or "").splitlines():
        first = line.split(",", 1)[0]
        for tok in re.split(r"[\s;]+", first.strip()):
            tok = tok.strip().strip('"\'<>').lower()
            if not tok:
                continue
            key = tok if "@" in tok else "@" + tok
            if PROTECT_KEY.fullmatch(key) and len(key) <= 254:
                if key not in keys:
                    keys.append(key)
            elif tok not in rejected:
                rejected.append(tok[:60])
    return keys, rejected


def _protect_in_gmail(keys):
    """For a bulk protect: per account, one read of its filters, then drop send-to-Trash filters for these senders and
    add "never send to Spam" for each. Runs in the background; the protection itself is already in the database."""
    never_spam = {"removeLabelIds": ["SPAM"]}
    for acc in ACC.list():
        done = cleared = 0
        try:
            for f in gmail(acc["id"], "/settings/filters").get("filter", []):
                frm = (f.get("criteria") or {}).get("from")
                if "TRASH" in ((f.get("action") or {}).get("addLabelIds") or []) and any(sender_matches(frm, k) for k in keys):
                    gmail(acc["id"], f"/settings/filters/{f['id']}", "DELETE")
                    cleared += 1
            for k in keys:
                _filter(acc["id"], k, never_spam)
                done += 1
            log("cleanup", f"bulk protect: {done} never-spam filter(s), {cleared} send-to-Trash filter(s) removed", acc["email"])
        except Exception as e:  # noqa: BLE001 - protection in Zero Sum still applies
            log("cleanup-error", f"bulk protect stopped after {done} of {len(keys)}: {e}"[:300], acc["email"])


def protect_many(d):
    """Protect a pasted list of senders at once (e.g. a keep-list carried over from another tool). They are protected
    here immediately, so no cleanup action can touch them; the Gmail filters follow in the background."""
    keys, rejected = parse_protect_keys(d.get("text"))
    if not keys:
        raise ValueError("no addresses or domains found" + (f" (could not read: {', '.join(rejected[:5])})" if rejected else ""))
    if len(keys) > 2000:
        raise ValueError("at most 2000 senders at a time")
    with db() as c:
        with c.cursor() as cur:
            cur.executemany("INSERT INTO unified.protected (key) VALUES (%s) ON CONFLICT DO NOTHING", [(k,) for k in keys])
    for k in keys:
        stop_automation(k)
    threading.Thread(target=_protect_in_gmail, args=(keys,), daemon=True).start()
    log("cleanup", f"bulk protect: {len(keys)} sender(s)")
    return {"ok": True, "protected": len(keys), "rejected": rejected[:50]}


def protected_list(q=None):
    with db() as c:
        return [{"key": k, "at": a.isoformat()} for k, a in c.execute(
            "SELECT key, created_at FROM unified.protected ORDER BY key").fetchall()]


# ---------------------------------------------------------------- Gmail helpers
def keep_query(words):
    """Gmail search for 'subject contains any of these words/phrases', or None. Input is cleaned, not trusted."""
    out = []
    words = [w for w in (words or []) if str(w).strip()]
    if len(words) > 10:  # never drop one silently: a missing keep word means that mail gets the destructive action
        raise ValueError("at most 10 keep words per group")
    for w in words:
        w = re.sub(r"[^\w\s'&$%!-]", " ", str(w), flags=re.U).strip().lstrip("-").strip()[:40]  # "-word" would negate
        if w:
            out.append(f'"{w}"' if " " in w else w)
    return f"subject:({' OR '.join(out)})" if out else None


def subject_has(words, subject):
    """Does the subject contain one of the words or phrases as whole words? This is what the applied Gmail search
    `subject:(word)` matches, so the previews use it too. A plain substring test showed "Reorder now" as kept by
    "order" (green in the preview), and Gmail then gave it the destructive action."""
    sub = str(subject or "").lower()
    for w in words or []:
        w = re.sub(r"\s+", " ", str(w).strip().lower())
        if w and re.search(r"(?<![^\W_])" + re.escape(w).replace(r"\ ", r"\s+") + r"(?![^\W_])", sub, flags=re.U):
            return True
    return False


def _filter(acc_id, addr, action, remove=False, keep=None):
    """Create (or with remove=True delete) the Gmail filter `from:addr` -> action. With `keep` (a keep_query), mail whose
    subject matches is left out of the filter."""
    crit = {"from": addr, **({"negatedQuery": keep} if keep else {})}
    if remove:
        for f in gmail(acc_id, "/settings/filters").get("filter", []):
            if (f.get("criteria") or {}).get("from", "").lower() == addr and f.get("action") == action:
                gmail(acc_id, f"/settings/filters/{f['id']}", "DELETE")
        return
    try:
        gmail(acc_id, "/settings/filters", "POST", {"criteria": crit, "action": action})
    except RuntimeError as e:
        if "exists" not in str(e).lower():
            raise


def undo_list(q=None):
    """Bulk changes of the last UNDO_DAYS days, newest first, for the Undo list."""
    with db() as c:
        rows = c.execute("SELECT id, at, account_id, note, add_labels, remove_labels, cardinality(ids), undone_at "
                         "FROM unified.action_log ORDER BY id DESC LIMIT 60").fetchall()
    names = {a["id"]: a["email"] for a in ACC.list()}
    return {"days": UNDO_DAYS, "actions": [
        {"id": i, "at": at.isoformat(), "email": names.get(acc, acc), "note": note, "add": add, "remove": rem,
         "count": n, "undone": undone.isoformat() if undone else None}
        for i, at, acc, note, add, rem, n, undone in rows]}


def undo_action(d):
    """Put the messages of one recorded bulk change back as they were: its added labels come off (out of Trash,
    Spam or Quarantine), its removed labels go back on (INBOX). Filters are not touched: Keep or Reset removes those."""
    with db() as c:
        row = c.execute("SELECT account_id, add_labels, remove_labels, ids, undone_at FROM unified.action_log "
                        "WHERE id = %s", (int(d.get("id") or 0),)).fetchone()
    if not row:
        raise ValueError("no such action (older than %d days?)" % UNDO_DAYS)
    acc, add, rem, ids, undone = row
    if undone:
        raise ValueError("already undone")
    n = _batch(acc, list(ids), add=list(rem), remove=list(add), record=False)
    with db() as c:
        c.execute("UPDATE unified.action_log SET undone_at = now() WHERE id = %s", (int(d["id"]),))
    try:  # Delete/Block dropped these messages from the local history: read them back so the sender shows up again
        _store(acc, list(ids)[:5000])
    except Exception as e:  # noqa: BLE001 - the mail itself is restored; the collector fills the rest
        log("cleanup-error", f"undo #{int(d['id'])}: history not refreshed: {e}"[:300], ACC.email(acc))
    log("cleanup", f"undo #{int(d['id'])}: {n} messages restored", ACC.email(acc))
    return {"ok": True, "restored": n}


def sender_matches(frm, key):
    """Does a filter's `from` value fall under `key`: an address, or a domain ("@example.com", subdomains included)?"""
    frm, key = (frm or "").strip().lower(), (key or "").strip().lower()
    if not frm or not key:
        return False
    if key.startswith("@"):
        dom = frm.rsplit("@", 1)[-1]
        return dom == key[1:] or dom.endswith("." + key[1:])
    return frm == key


def remove_trash_filters(acc_id, key):
    """Delete the account's Gmail filters that send this sender's (or domain's) mail straight to Trash. Block,
    Unsubscribe & delete and a plan's delete groups create them; without this they outlived Protect, Keep, Reset and
    a re-applied plan, and kept trashing new mail from a sender the app showed as protected. Returns how many."""
    n = 0
    for f in gmail(acc_id, "/settings/filters").get("filter", []):
        if "TRASH" in ((f.get("action") or {}).get("addLabelIds") or []) \
                and sender_matches((f.get("criteria") or {}).get("from"), key):
            gmail(acc_id, f"/settings/filters/{f['id']}", "DELETE")
            n += 1
    return n


def stop_automation(key):
    """Drop the AI sorts covered by `key` (an address or "@domain"); a running pass stops before its next batch."""
    with db() as c:
        for (addr,) in c.execute("SELECT addr FROM unified.ai_sort").fetchall():
            if sender_matches(addr, key):
                c.execute("DELETE FROM unified.ai_sort WHERE addr = %s", (addr,))


def _modify_existing(acc_id, query, add=(), remove=()):
    """Apply label changes to every message matching `query` (batches of 1000). Returns the count."""
    if "in:sent" not in query:  # a sender action never reaches what the account itself sent, its drafts or chats
        query = f"{query} {SKIP}"
    return _batch(acc_id, [m["id"] for m in gmail_list(acc_id, qs(query), "messages", MAX_BULK)], add, remove)


_ACTION = threading.local()   # .note: what the person asked for ("block deals@shop.com"), for the undo list
UNDO_DAYS = 28               # Gmail empties Trash after 30 days: past that an undo could not bring mail back


def _record(acc_id, ids, add, remove):
    """Write down which messages a bulk change is about to touch, before it happens. A wrong Delete or Block used to
    leave only a count in the log: nothing said which mail had moved, so the only way back was searching Trash by hand."""
    with db() as c:
        c.execute("INSERT INTO unified.action_log (account_id, note, add_labels, remove_labels, ids) "
                  "VALUES (%s, %s, %s, %s, %s)",
                  (acc_id, str(getattr(_ACTION, "note", "") or "")[:300], list(add), list(remove), list(ids)))
        c.execute("DELETE FROM unified.action_log WHERE at < now() - make_interval(days => %s)", (UNDO_DAYS,))


def _batch(acc_id, ids, add=(), remove=(), record=True):
    """Apply label changes to these message ids (batches of 1000). Returns the count."""
    if ids and record and (add or remove):
        _record(acc_id, ids, add, remove)
    for i in range(0, len(ids), 1000):
        chunk = ids[i:i + 1000]
        try:
            gmail(acc_id, "/messages/batchModify", "POST",
                  {"ids": chunk, "addLabelIds": list(add), "removeLabelIds": list(remove)})
        except RuntimeError:
            if "TRASH" in add:
                for mid in chunk:  # some accounts refuse TRASH in batchModify: trash one by one
                    gmail(acc_id, f"/messages/{mid}/trash", "POST")
            elif "TRASH" in remove:  # the same accounts, on an undo: out of Trash one by one, then the other labels
                for mid in chunk:
                    gmail(acc_id, f"/messages/{mid}/untrash", "POST")
                rest_add, rest_rem = list(add), [x for x in remove if x != "TRASH"]
                if rest_add or rest_rem:
                    gmail(acc_id, "/messages/batchModify", "POST",
                          {"ids": chunk, "addLabelIds": rest_add, "removeLabelIds": rest_rem})
            else:
                raise
    return len(ids)


def _archive_existing(acc_id, addr, keep=None):
    n = _modify_existing(acc_id, f"from:{addr} in:inbox" + (f" -{keep}" if keep else ""), remove=["INBOX"])
    with db() as c:
        c.execute("UPDATE unified.msg SET inbox = false WHERE account_id = %s AND from_addr = %s", (acc_id, addr))
    return n


def _drop_rows(acc_id, addr):
    with db() as c:
        c.execute("DELETE FROM unified.msg WHERE account_id = %s AND from_addr = %s", (acc_id, addr))


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


QUARANTINE = os.environ.get("QUARANTINE_LABEL", "Quarantine")
SHOW = int(os.environ.get("QUARANTINE_SHOW", "600"))  # messages per account listed with details on the Quarantine tab
TRASH_FILTER = {"addLabelIds": ["TRASH"]}
ARCHIVE_FILTER = {"removeLabelIds": ["INBOX"]}
STAR_FILTER = {"addLabelIds": ["STARRED"]}
# actions that remove mail from view: refused for protected senders
DESTRUCTIVE = {"unsubscribe", "unsub_archive", "unsub_quarantine", "unsub_delete", "autoarchive", "archive",
               "quarantine", "delete", "block", "spam"}
UNSUB = {"unsubscribe", "unsub_archive", "unsub_quarantine", "unsub_delete"}
ACTIONS = DESTRUCTIVE | {"keep", "reset", "star", "unstar", "protect", "unprotect"}


def sender_action(d):
    addr, act = (d.get("addr") or "").strip().lower(), d.get("action")
    if act not in ACTIONS:
        raise ValueError("bad sender action")
    if act in ("protect", "unprotect"):
        if not re.fullmatch(r"[^@\s]*@[^@\s]+\.[^@\s]+", addr):
            raise ValueError("protect an address (name@example.com) or a domain (@example.com)")
        with db() as c:
            if act == "protect":
                c.execute("INSERT INTO unified.protected (key) VALUES (%s) ON CONFLICT DO NOTHING", (addr,))
            else:
                c.execute("DELETE FROM unified.protected WHERE key = %s", (addr,))
        never_spam = {"removeLabelIds": ["SPAM"]}  # Gmail's "Never send it to Spam", for this address or domain
        cleared = 0
        if act == "protect":
            stop_automation(addr)
        for acc in ACC.list():
            try:
                _filter(acc["id"], addr, never_spam, remove=(act == "unprotect"))
                if act == "protect":  # what was set up before the protection must stop deleting its mail
                    cleared += remove_trash_filters(acc["id"], addr)
            except Exception as e:  # noqa: BLE001 - protection in Zero Sum still applies
                log("cleanup-error", f"{act} {addr}: filters: {e}"[:300], acc["email"])
        if cleared:
            log("cleanup", f"protect {addr}: removed {cleared} send-to-Trash filter(s)")
        if act == "protect" and not addr.startswith("@"):  # also "kept" in Inbox Zero for every account that gets it
            with db() as c:
                for (a,) in c.execute("SELECT DISTINCT account_id FROM unified.msg WHERE from_addr = %s", (addr,)).fetchall():
                    _newsletter(c, a, addr, "APPROVED")
        log("cleanup", f"{act} {addr}")
        return {"results": [{"email": "all accounts", "ok": True, "note": f"{addr} {act}ed"}], "manual": []}
    if not re.fullmatch(r"[^@\s]+@[^@\s]+", addr):
        raise ValueError("bad sender address")
    if act in DESTRUCTIVE and is_protected(addr):
        raise PermissionError(f"{addr} is protected: unprotect it first")
    if act in DESTRUCTIVE and own_address(addr):
        raise PermissionError(f"{addr} is one of your own accounts: its mail is never cleaned up")
    kq = keep_query(d.get("keep")) if act in DESTRUCTIVE else None
    _ACTION.note = f"{act} {addr}"
    if act in ("keep", "reset"):
        stop_automation(addr)
    nk = f" -{kq}" if kq else ""  # appended to every "which existing mail" query
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
            if act in UNSUB:  # the unsubscribe part; what happens to their mail follows below
                u, post = unsub.get(a, (None, False))
                if u and u.startswith("https://") and post and _one_click(u):
                    note.append("unsubscribed (one-click)")
                elif u:
                    manual.append({"email": em, "url": u})
                    note.append("open the link to finish unsubscribing")
                else:
                    note.append("no unsubscribe link" + ("" if act != "unsubscribe" else ": use Block or Quarantine"))
            if act == "unsub_delete":  # and clear it out: Trash now, future mail straight to Trash
                _filter(a, addr, TRASH_FILTER, keep=kq)
                note.append(f"{_modify_existing(a, f'from:{addr} -in:trash{nk}', add=['TRASH'])} moved to Trash, "
                            "future mail goes to Trash")
                if not kq:
                    _drop_rows(a, addr)
            if act in ("quarantine", "unsub_quarantine"):  # held for review on the Quarantine tab, now and in future
                lid = label_id(a, QUARANTINE)
                _filter(a, addr, {"addLabelIds": [lid], "removeLabelIds": ["INBOX"]}, keep=kq)
                n = _modify_existing(a, f"from:{addr} -in:trash -in:spam -label:{QUARANTINE}{nk}", add=[lid],
                                     remove=["INBOX"])  # every folder, every year
                note.append(f"filter added, {n} moved to {QUARANTINE}")
                with db() as c:
                    c.execute("UPDATE unified.msg SET inbox = false WHERE account_id = %s AND from_addr = %s", (a, addr))
            if act in ("autoarchive", "unsub_archive"):
                _filter(a, addr, ARCHIVE_FILTER, keep=kq)
                note.append(f"filter added, {_archive_existing(a, addr, kq)} archived")
            elif act == "archive":
                note.append(f"{_archive_existing(a, addr, kq)} archived")
            elif act == "delete":
                note.append(f"{_modify_existing(a, f'from:{addr} -in:trash{nk}', add=['TRASH'])} moved to Trash")
                if not kq:
                    _drop_rows(a, addr)
            elif act == "block":
                _filter(a, addr, TRASH_FILTER, keep=kq)
                note.append(f"filter added (future mail goes to Trash), "
                            f"{_modify_existing(a, f'from:{addr} -in:trash{nk}', add=['TRASH'])} moved to Trash")
                if not kq:
                    _drop_rows(a, addr)
            elif act == "spam":
                note.append(f"{_modify_existing(a, f'from:{addr} -in:spam -in:trash{nk}', add=['SPAM'], remove=['INBOX'])} "
                            "reported as spam")
                if not kq:
                    _drop_rows(a, addr)
            elif act in ("keep", "reset"):  # back to normal delivery: no filter may keep trashing this sender
                n = remove_trash_filters(a, addr)
                if n:
                    note.append(f"{n} send-to-Trash filter(s) removed")
            elif act == "star":
                _filter(a, addr, STAR_FILTER)
                note.append(f"filter added, {_modify_existing(a, f'from:{addr} in:inbox -is:starred', add=['STARRED'])} starred")
            elif act == "unstar":
                _filter(a, addr, STAR_FILTER, remove=True)
                note.append(f"filter removed, {_modify_existing(a, f'from:{addr} is:starred', remove=['STARRED'])} unstarred")
            if kq and act in DESTRUCTIVE:  # the kept ones: starred now and in future, so they stand out
                try:
                    gmail(a, "/settings/filters", "POST", {"criteria": {"from": addr, "query": kq}, "action": STAR_FILTER})
                except RuntimeError as e:
                    if "exists" not in str(e).lower():
                        raise
                note.append(f"{_modify_existing(a, f'from:{addr} {kq} -is:starred -in:trash', add=['STARRED'])} kept and starred")
            status = {"unsubscribe": "UNSUBSCRIBED", "unsub_archive": "UNSUBSCRIBED", "unsub_quarantine": "UNSUBSCRIBED",
                      "unsub_delete": "UNSUBSCRIBED", "autoarchive": "AUTO_ARCHIVED", "quarantine": "AUTO_ARCHIVED",
                      "block": "AUTO_ARCHIVED",
                      "keep": "APPROVED"}.get(act)
            with db() as c:
                if status:
                    _newsletter(c, a, addr, status)
                elif act == "reset":
                    c.execute('UPDATE "Newsletter" SET status = NULL, "updatedAt" = now() '
                              'WHERE lower(email) = %s AND "emailAccountId" = %s', (addr, a))
            results.append({"email": em, "ok": True, "note": "; ".join(note) or act})
        except Exception as e:  # noqa: BLE001
            results.append({"email": em, "ok": False, "note": str(e)[:200]})
    log("cleanup", f"{act} {addr}{' keep ' + kq if kq else ''}: " + "; ".join(f"{r['email']}: {r['note']}" for r in results))
    return {"results": results, "manual": manual}


# ---------------------------------------------------------------- quarantine tab
def _qlabel(acc_id):
    return label_id(acc_id, QUARANTINE, create=False)


def quarantine_list(q=None):
    """Everything carrying the Quarantine label in every account, newest first, grouped by sender."""
    from inbox import meta
    errors, totals = [], {}

    def ids(a):
        try:
            lid = _qlabel(a["id"])
            if not lid:
                return []
            ids = [m["id"] for m in gmail_list(a["id"], f"/messages?labelIds={lid}", "messages", MAX_BULK)]
            totals[a["id"]] = len(ids)
            return [(a, i) for i in ids[:SHOW]]  # newest SHOW per account get details; actions use the full label
        except Exception as e:  # noqa: BLE001
            errors.append({"email": a["email"], "error": str(e)[:200]})
            return []

    def safe(p):
        try:
            return meta(*p)
        except Exception:  # noqa: BLE001
            return None
    pairs = [p for lst in POOL.map(ids, ACC.list()) for p in lst]
    items = sorted((i for i in POOL.map(safe, pairs) if i), key=lambda i: -i["ts"])
    groups = {}
    for it in items:
        g = groups.setdefault(it["fromAddr"].lower(), {"addr": it["fromAddr"].lower(), "name": it["from"], "items": [],
                                                        "accounts": {}})
        g["items"].append(it)
        g["accounts"][it["account"]] = it["email"]
    senders = sorted(groups.values(), key=lambda g: -len(g["items"]))
    for g in senders:
        g["count"] = len(g["items"])
        g["accounts"] = [{"id": k, "email": v} for k, v in g["accounts"].items()]
    return {"label": QUARANTINE, "total": sum(totals.values()), "shown": len(items), "senders": senders, "errors": errors}


QACTIONS = {"delete": "moved to Trash", "archive": "archived (label removed)", "release": "back in the inbox"}


def quarantine_action(d):
    """delete / archive / release quarantined mail: chosen messages (items), one sender (sender), or all (all=true).
    Releasing a sender also removes its quarantine filter, so new mail reaches the inbox again."""
    act = d.get("action")
    if act not in QACTIONS:
        raise ValueError("bad quarantine action")
    sender = (d.get("sender") or "").strip().lower()
    chosen = {}
    for it in d.get("items") or []:
        if re.fullmatch(r"[0-9a-f]{6,32}", str(it.get("id", ""))):
            chosen.setdefault(it.get("account"), []).append(it["id"])
    if not (chosen or sender or d.get("all")):
        raise ValueError("choose messages, a sender, or all")
    _ACTION.note = f"quarantine {act}" + (f" {sender}" if sender else " (all)" if d.get("all") else " (chosen)")
    results = []
    for a in ACC.list():
        if chosen and a["id"] not in chosen:
            continue
        try:
            lid = _qlabel(a["id"])
            if not lid:
                continue
            if chosen:
                ids = chosen[a["id"]]
            else:
                query = f"/messages?labelIds={lid}" + (f"&q={urllib.parse.quote('from:' + sender)}" if sender else "")
                ids = [m["id"] for m in gmail_list(a["id"], query, "messages", MAX_BULK)]
            if not ids:
                continue
            if act == "delete" and sender and is_protected(sender):
                raise PermissionError(f"{sender} is protected: unprotect it first")
            add = {"delete": ["TRASH"], "archive": [], "release": ["INBOX"]}[act]
            n = _batch(a["id"], ids, add=add, remove=[lid])
            note = f"{n} {QACTIONS[act]}"
            if act == "release" and sender:
                _filter(a["id"], sender, {"addLabelIds": [lid], "removeLabelIds": ["INBOX"]}, remove=True)
                note += ", quarantine filter removed"
            results.append({"email": a["email"], "ok": True, "note": note})
        except Exception as e:  # noqa: BLE001
            results.append({"email": a["email"], "ok": False, "note": str(e)[:200]})
    if act == "release" and sender:
        with db() as c:
            for (acc,) in c.execute("SELECT DISTINCT account_id FROM unified.msg WHERE from_addr = %s", (sender,)).fetchall():
                c.execute('UPDATE "Newsletter" SET status = NULL, "updatedAt" = now() '
                          'WHERE lower(email) = %s AND "emailAccountId" = %s AND status::text = \'AUTO_ARCHIVED\'',
                          (sender, acc))
    log("quarantine", f"{act} {sender or ('selected' if chosen else 'all')}: "
        + "; ".join(f"{r['email']}: {r['note']}" for r in results))
    return {"results": results}


# ---------------------------------------------------------------- previews and multi-group plans
def sender_recent(q):
    """The newest N emails from a sender across every account and folder (Trash and Spam excluded), for previews."""
    from inbox import meta
    addr = (q.get("addr", [""])[0] or "").strip().lower()
    n = max(1, min(int(q.get("n", ["20"])[0]), 50))
    if not re.fullmatch(r"[^@\s]+@[^@\s]+", addr):
        raise ValueError("bad sender address")

    def ids(a):
        try:
            return [(a, m["id"]) for m in gmail_list(a["id"], qs(f"from:{addr} -in:trash -in:spam"), "messages", n)]
        except Exception:  # noqa: BLE001
            return []

    def safe(p):
        try:
            return meta(*p)
        except Exception:  # noqa: BLE001
            return None
    pairs = [p for lst in POOL.map(ids, ACC.list()) for p in lst]
    items = sorted((i for i in POOL.map(safe, pairs) if i), key=lambda i: -i["ts"])[:n]
    return {"addr": addr, "items": items}


PLAN_ACTIONS = ("keep", "archive", "quarantine", "delete")


def _plan_action(acc_id, addr, action, crit_extra, query_extra, lid):
    """One group of a plan: a Gmail filter for future mail + the same change on existing mail. Returns a note."""
    if action == "keep":  # stays in the inbox, starred so it stands out
        filt, q, add, rem = STAR_FILTER, f"-is:starred -in:trash -in:spam", ["STARRED"], []
    elif action == "archive":
        filt, q, add, rem = ARCHIVE_FILTER, "in:inbox", [], ["INBOX"]
    elif action == "quarantine":
        filt, q, add, rem = {"addLabelIds": [lid], "removeLabelIds": ["INBOX"]}, f"-in:trash -in:spam -label:{QUARANTINE}", [lid], ["INBOX"]
    else:  # delete
        filt, q, add, rem = TRASH_FILTER, "-in:trash", ["TRASH"], []
    try:
        gmail(acc_id, "/settings/filters", "POST", {"criteria": {"from": addr, **crit_extra}, "action": filt})
    except RuntimeError as e:
        if "exists" not in str(e).lower():
            raise
    n = _modify_existing(acc_id, f"from:{addr} {query_extra} {q}".strip(), add=add, remove=rem)
    return n


def _labels_for(action, lid):
    """(add, remove) label ids that carry out a plan action on existing mail."""
    return {"keep": ([], []), "archive": ([], ["INBOX"]), "quarantine": ([lid], ["INBOX"]),
            "delete": (["TRASH"], [])}[action]


_SORT_RUNS, _SORT_LOCK = {}, threading.Lock()   # addr -> the background pass that owns it (this process)


def start_ai_sort(addr, rule, action):
    """Start the background pass over a sender's existing mail. A pass already running for the sender stops at its
    next batch: there is never more than one, however often the plan is re-applied."""
    token = object()
    with _SORT_LOCK:
        _SORT_RUNS[addr] = token
    threading.Thread(target=_ai_sort_existing, args=(addr, rule, action, token), daemon=True).start()


def _ai_sort_ids(addr, rule, action, pairs, token=None):
    """Classify (account, message id) pairs by the rule in batches and apply `action` to the ones not kept."""
    import ai
    from inbox import meta
    kept = 0
    for i in range(0, len(pairs), 40):
        chunk = pairs[i:i + 40]
        with db() as c:  # "Stop AI sort", a new rule or Protect takes effect before the next batch, not after the pass
            row = c.execute("SELECT rule, action FROM unified.ai_sort WHERE addr = %s", (addr,)).fetchone()
        if not row or tuple(row) != (rule, action):
            raise RuntimeError("stopped: the AI sort was removed or changed")
        if token is not None and _SORT_RUNS.get(addr) is not token:
            raise RuntimeError("stopped: a newer pass took over")
        if action != "keep" and is_protected(addr):
            raise PermissionError(f"{addr} is protected: AI sort paused")
        with db() as c:  # local history first: no Gmail call for mail the collector already has
            local = {(acc, mid): (sub, snip) for acc, mid, sub, snip in c.execute(
                "SELECT account_id, id, subject, coalesce(snippet, '') FROM unified.msg WHERE id = ANY(%s) AND subject IS NOT NULL",
                ([mid for _, mid in chunk],)).fetchall()}
        metas = []
        for a, mid in chunk:
            if (a["id"], mid) in local:
                sub, snip = local[(a["id"], mid)]
                metas.append((a, {"id": mid, "subject": sub, "snippet": snip}))
                continue
            try:
                metas.append((a, meta(a, mid)))
            except Exception:  # noqa: BLE001 - deleted meanwhile
                pass
        verdicts = ai.classify(rule, [(m["subject"], m["snippet"]) for _, m in metas])
        by_acc = {}
        for (a, m), keep in zip(metas, verdicts):
            if keep:
                kept += 1
            else:
                by_acc.setdefault(a["id"], []).append(m["id"])
        for acc_id, ids in by_acc.items():
            lid = label_id(acc_id, QUARANTINE) if action == "quarantine" else None
            add, rem = _labels_for(action, lid)
            if add or rem:
                _batch(acc_id, ids, add=add, remove=rem)
        with db() as c:
            c.execute("UPDATE unified.ai_sort SET done = done + %s, kept = kept + %s, updated_at = now() WHERE addr = %s",
                      (len(chunk), sum(1 for (_, _m), k in zip(metas, verdicts) if k), addr))
    return kept


def _ai_sort_existing(addr, rule, action, token=None):
    _ACTION.note = f"AI sort {addr}"
    try:
        pairs = []
        for a in ACC.list():
            q = f"from:{addr} -in:trash -in:spam {SKIP}" + (f" -label:{QUARANTINE}" if action == "quarantine" else "")
            pairs += [(a, m["id"]) for m in gmail_list(a["id"], qs(q), "messages", MAX_BULK)]
        with db() as c:
            c.execute("UPDATE unified.ai_sort SET total = %s, done = 0, kept = 0, status = 'sorting' WHERE addr = %s",
                      (len(pairs), addr))
        kept = _ai_sort_ids(addr, rule, action, pairs, token)
        with db() as c:
            c.execute("UPDATE unified.ai_sort SET status = 'active', last_ts = now(), updated_at = now() WHERE addr = %s", (addr,))
        log("ai-sort", f"{addr}: sorted {len(pairs)} existing, kept {kept}, rest {action}")
    except Exception as e:  # noqa: BLE001
        if "a newer pass took over" not in str(e):   # the newer pass owns the row's status now
            with db() as c:
                c.execute("UPDATE unified.ai_sort SET status = %s, updated_at = now() WHERE addr = %s", (f"error: {e}"[:200], addr))
        log("ai-sort-error", f"{addr}: {e}"[:500])
    finally:
        with _SORT_LOCK:
            if token is not None and _SORT_RUNS.get(addr) is token:
                del _SORT_RUNS[addr]


def resume_ai_sorts():
    """Pick up passes that died: the container restarted mid-pass (status still 'sorting', no pass running here), or
    the daily AI limit stopped one (retried from the next UTC day). Before, such a sender stayed half-sorted and its
    new mail was never sorted either, because only 'active' sorts are served."""
    with db() as c:
        rows = c.execute("SELECT addr, rule, action FROM unified.ai_sort WHERE "
                         "(status = 'sorting' AND updated_at < now() - interval '10 minutes') OR "
                         "(status LIKE 'error: daily AI limit%%' AND updated_at::date < (now() AT TIME ZONE 'utc')::date)"
                         ).fetchall()
    for addr, rule, action in rows:
        if addr in _SORT_RUNS or (action != "keep" and (is_protected(addr) or own_address(addr))):
            continue
        log("ai-sort", f"{addr}: resuming the interrupted sort")
        start_ai_sort(addr, rule, action)


def ai_sort_new():
    """Every collector run: judge new inbox mail from AI-sorted senders by their rule."""
    try:
        resume_ai_sorts()
    except Exception as e:  # noqa: BLE001
        log("ai-sort-error", f"resume: {e}"[:500])
    with db() as c:
        rows = c.execute("SELECT addr, rule, action, extract(epoch from last_ts) FROM unified.ai_sort "
                         "WHERE status = 'active'").fetchall()
    for addr, rule, action, last in rows:
        try:
            if action != "keep" and (is_protected(addr) or own_address(addr)):
                continue  # protected after the sort was set up: leave its mail alone
            _ACTION.note = f"AI sort {addr} (new mail)"
            since = int(last or 0) - 3600
            pairs = []
            for a in ACC.list():
                pairs += [(a, m["id"]) for m in gmail_list(a["id"], qs(f"from:{addr} in:inbox after:{since}"), "messages", 500)]
            if pairs:
                kept = _ai_sort_ids(addr, rule, action, pairs)
                log("ai-sort", f"{addr}: {len(pairs)} new, kept {kept}, rest {action}")
            with db() as c:
                c.execute("UPDATE unified.ai_sort SET last_ts = now() WHERE addr = %s", (addr,))
        except Exception as e:  # noqa: BLE001
            log("ai-sort-error", f"{addr}: {e}"[:500])


def ai_sort_status(q=None):
    with db() as c:
        return {r[0]: {"rule": r[1], "action": r[2], "status": r[3], "done": r[4], "total": r[5], "kept": r[6]}
                for r in c.execute("SELECT addr, rule, action, status, done, total, kept FROM unified.ai_sort").fetchall()}


def stop_ai_sort(d):
    with db() as c:
        c.execute("DELETE FROM unified.ai_sort WHERE addr = %s", (str(d.get("addr") or "").strip().lower(),))
    return {"ok": True}


def apply_plan(d):
    """Apply a multi-group plan to a sender in every account that gets its mail:
    groups = [{"label", "words": [...], "action": keep|archive|quarantine|delete}] (first matching group wins, by subject),
    default = action for everything that matches no group (keep = leave it alone)."""
    addr = (d.get("addr") or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+", addr):
        raise ValueError("bad sender address")
    groups = []
    raw_groups = [g for g in (d.get("groups") or []) if any(str(w).strip() for w in (g.get("words") or []))]
    if len(raw_groups) > 6:  # refused, not cut: a dropped "keep" group would hand its mail to the default action
        raise ValueError("at most 6 groups in a plan")
    for g in raw_groups:
        kq = keep_query(g.get("words"))
        if not kq or g.get("action") not in PLAN_ACTIONS:
            raise ValueError(f"group {str(g.get('label') or '?')[:40]!r} has no usable words or a bad action")
        groups.append({"label": str(g.get("label") or "")[:40], "kq": kq, "action": g["action"]})
    default = d.get("default") if d.get("default") in PLAN_ACTIONS else "keep"
    _ACTION.note = f"plan for {addr}"
    destructive = {g["action"] for g in groups} | {default}
    if destructive - {"keep"} and is_protected(addr):
        raise PermissionError(f"{addr} is protected: unprotect it first")
    if destructive - {"keep"} and own_address(addr):
        raise PermissionError(f"{addr} is one of your own accounts: its mail is never cleaned up")
    if d.get("mode") == "ai":  # AI sort: each email judged by the rule, now (background) and every collector run
        rule = re.sub(r"\s+", " ", str(d.get("rule") or ""))[:400]
        if not rule:
            raise ValueError("write what to keep first")
        for acc in ACC.list():  # an older Trash filter would delete new mail before the AI ever judged it
            try:
                remove_trash_filters(acc["id"], addr)
            except Exception as e:  # noqa: BLE001
                log("cleanup-error", f"apply {addr}: old filters: {e}"[:300], acc["email"])
        with db() as c:
            c.execute("INSERT INTO unified.ai_sort (addr, rule, action) VALUES (%s, %s, %s) ON CONFLICT (addr) DO UPDATE "
                      "SET rule = EXCLUDED.rule, action = EXCLUDED.action, status = 'sorting', done = 0, total = 0, kept = 0",
                      (addr, rule, default))
            c.execute("UPDATE unified.ai_plan SET status = 'applied', updated_at = now() WHERE addr = %s", (addr,))
        start_ai_sort(addr, rule, default)
        log("ai-cleanup", f"apply {addr}: AI sort, rest {default}: {rule}")
        return {"results": [{"email": "all accounts", "ok": True,
                             "note": f"AI sort started: existing mail is being sorted in the background (rest: {default}); "
                                     "new mail from this sender is sorted every 15 minutes"}]}
    stop_automation(addr)  # a word plan replaces an earlier AI sort
    with db() as c:
        acc_ids = [r[0] for r in c.execute("SELECT DISTINCT account_id FROM unified.msg WHERE from_addr = %s",
                                           (addr,)).fetchall()]
    results = []
    for a in acc_ids:
        em = ACC.email(a)
        try:
            lid = label_id(a, QUARANTINE) if "quarantine" in destructive else None
            remove_trash_filters(a, addr)  # a re-applied plan replaces the old one's delete filters, it doesn't add to them
            notes, earlier = [], []  # a group only takes mail no earlier group matched
            for g in groups:
                excl = "".join(f" -{k}" for k in earlier)
                crit = {"query": g["kq"], **({"negatedQuery": " OR ".join(earlier)} if earlier else {})}
                n = _plan_action(a, addr, g["action"], crit, f"{g['kq']}{excl}", lid)
                notes.append(f"{g['label'] or g['action']}: {n} {g['action']}")
                earlier.append(g["kq"])
            if default != "keep":
                crit = {"negatedQuery": " OR ".join(earlier)} if earlier else {}
                n = _plan_action(a, addr, default, crit, "".join(f" -{k}" for k in earlier), lid)
                notes.append(f"everything else: {n} {default}")
            results.append({"email": em, "ok": True, "note": "; ".join(notes) or "nothing to do"})
        except Exception as e:  # noqa: BLE001
            results.append({"email": em, "ok": False, "note": str(e)[:200]})
    with db() as c:
        status = {"keep": "APPROVED", "archive": "AUTO_ARCHIVED", "quarantine": "AUTO_ARCHIVED", "delete": "AUTO_ARCHIVED"}[default]
        for a in acc_ids:
            _newsletter(c, a, addr, status)
        c.execute("UPDATE unified.ai_plan SET status = 'applied', updated_at = now() WHERE addr = %s", (addr,))
    log("ai-cleanup", f"apply {addr}: " + "; ".join(f"{r['email']}: {r['note']}" for r in results))
    return {"results": results}
