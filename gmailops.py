"""Everything that changes Gmail in bulk, in one place: the filters this app creates (each recorded in unified.filter,
and only those are ever removed), label changes on existing mail (_batch: recorded with the messages they touch
before Gmail is called, refused for protected senders and own addresses), and the undo journal built from that
record (unified.action_log)."""
import contextlib, email.utils, os, re, threading, time, traceback, urllib.error
from concurrent.futures import ThreadPoolExecutor

from collector import CPOOL, SKIP, _store, qs
from core import ACC, POOL, db, gmail, gmail_list, gmail_spare, header, label_id, log
from guard import (frm, guard_sender, is_protected, own_address, own_set, protected_set, removes_mail, sender_addr,
                   sender_matches, SENDER_KEY)
from inbox import meta

MAX_BULK = int(os.environ.get("MAX_BULK", "200000"))  # most messages one bulk action touches per account
# Most restored messages one undo reads back from Gmail (one metadata read each, 5 quota units), after the undo has
# answered and only while nothing else uses the account's quota. Past it, the local flag stays as the undo set it.
UNDO_REFRESH = int(os.environ.get("UNDO_REFRESH", "500"))
UNDO_STEP = 20   # read back this many at a time, checking for spare quota before each step


# How long a sender read from Gmail is remembered (unified.sender_seen) for mail the local history does not hold, so that
# a run that is "partly done" does not read the same messages again next time (a message's sender never changes). A
# message with no From address (or deleted since, or one Gmail refuses for good: a bad id, a 403 that is not a rate
# limit) is remembered as unknown only for SEEN_UNKNOWN_MINUTES, then tried again. A read Gmail rate-limited, failed
# with a server error or a login error (401), or did not answer, is not remembered at all.
SEEN_DAYS, SEEN_UNKNOWN_MINUTES = 30, 60
_FAILED = object()   # senders_of: a read that failed (not the same as a message without a sender)


def _passing(e):
    """Is this failed Gmail read worth asking again soon? A rate limit, a server error, a timeout or no answer at all
    is: the message is not known yet. So is a 401 (the login failed even after a token refresh): that is about the
    account, not the message. Any other refusal (404 deleted since it was listed, 400 bad id, 403 that is not a rate
    limit) would be refused again on every run: it is remembered as unknown instead."""
    m = re.search(r": (\d{3}) ", str(e)) if isinstance(e, RuntimeError) and str(e).startswith("Gmail ") else None
    if not m:
        return True
    code, low = int(m.group(1)), str(e).lower().replace(" ", "")
    return code in (401, 429) or code >= 500 or (code == 403 and ("ratelimit" in low or "quotaexceeded" in low))


def senders_of(acc_id, ids, budget=None):
    """{message id: sender address, or None when it cannot be told}: from the local history (unified.msg, then the
    senders read earlier, unified.sender_seen), else read from Gmail and remembered.
    `budget(n)`, when given, says how many of the n messages missing from the local history may be read from Gmail;
    the ones beyond it are left out of the answer (not looked up), so the caller can tell them from unknown ones."""
    who = {}
    for i in range(0, len(ids), 5000):
        with db() as c:
            who.update(c.execute("SELECT id, from_addr FROM unified.msg WHERE account_id = %s AND id = ANY(%s)",
                                 (acc_id, ids[i:i + 5000])).fetchall())
    rest = [i for i in ids if i not in who]
    for i in range(0, len(rest), 5000):
        with db() as c:
            who.update(c.execute("SELECT id, from_addr FROM unified.sender_seen WHERE account_id = %s AND id = ANY(%s) "
                                 "AND (from_addr IS NOT NULL OR seen_at > now() - make_interval(mins => %s))",
                                 (acc_id, rest[i:i + 5000], SEEN_UNKNOWN_MINUTES)).fetchall())

    def read(mid):
        try:
            m = gmail(acc_id, f"/messages/{mid}?format=metadata&metadataHeaders=From")
            return email.utils.parseaddr(header(m.get("payload", {}).get("headers", []), "from"))[1].lower() or None
        except Exception as e:  # noqa: BLE001
            return _FAILED if _passing(e) else None
    todo = [i for i in ids if i not in who]
    if budget is not None:
        todo = todo[:max(0, budget(len(todo)))]
    answers = dict(zip(todo, CPOOL.map(read, todo)))
    # a failed read is left out of the answer, like a message not looked up: the caller counts it as not done yet
    # (run it again), not as someone else's mail, and it is not remembered
    got = {k: v for k, v in answers.items() if v is not _FAILED}
    who.update(got)
    if answers:
        seen = getattr(_ACTION, "seen", None)
        if seen is not None:   # this request's progress: senders it learned, and reads it spent
            seen["read"] += len(answers)
            seen["known"] += sum(1 for v in got.values() if v)
    if got:
        try:
            with db() as c:
                with c.cursor() as cur:
                    cur.executemany("INSERT INTO unified.sender_seen (account_id, id, from_addr) VALUES (%s, %s, %s) "
                                    "ON CONFLICT (account_id, id) DO UPDATE SET from_addr = EXCLUDED.from_addr, "
                                    "seen_at = now()", [(acc_id, k, v) for k, v in got.items()])
                c.execute("DELETE FROM unified.sender_seen WHERE seen_at < now() - make_interval(days => %s)", (SEEN_DAYS,))
        except Exception as e:  # noqa: BLE001 - only a cache: the answer stands, the next run reads them again
            log("cleanup-error", f"sender look-ups not remembered ({acc_id}): {e}"[:300])
    return who


TRASH_FILTER = {"addLabelIds": ["TRASH"]}
ARCHIVE_FILTER = {"removeLabelIds": ["INBOX"]}
STAR_FILTER = {"addLabelIds": ["STARRED"]}
NEVER_SPAM_FILTER = {"removeLabelIds": ["SPAM"]}  # Gmail's "Never send it to Spam", for an address or a domain
# What a recorded filter does. "keep_star" stars the kept part of a sender (keep words, a plan's keep group);
# "star" is the Auto-star button; "never_spam" comes with Protect.
FILTER_KINDS = ("trash", "archive", "quarantine", "keep_star", "star", "never_spam")
AUTOMATION_KINDS = ("trash", "archive", "quarantine", "keep_star")  # one sender's clean-up automation: replaced as a set


def _filter(acc_id, addr, action, kind, keep=None, query=None, negated=None, source=""):
    """Create the Gmail filter `from:addr` -> action and write its id down in unified.filter, so that Protect, Keep,
    Reset and a re-applied plan can later remove exactly the filters this app made, and never one it did not.
    `keep` / `negated`: mail whose subject matches is left out; `query`: only mail that matches. Returns the id."""
    if kind not in FILTER_KINDS:
        raise ValueError("bad filter kind")
    if kind in ("trash", "archive", "quarantine") or removes_mail(action.get("addLabelIds") or (), action.get("removeLabelIds") or ()):
        guard_sender(addr)  # whatever the caller checked: no filter may move a protected sender's future mail
    crit = {"from": addr}
    if query:
        crit["query"] = query
    if keep or negated:
        crit["negatedQuery"] = keep or negated
    with _sender_lock(acc_id, addr):   # one at a time per sender: another action's new filter is not claimed here
        return _create_filter(acc_id, addr, action, kind, crit, source)


# (account, sender) -> [lock, how many threads hold or wait for it]; an entry goes once nobody needs it, so the dict
# only ever holds the senders being worked on right now
_filter_locks, _filter_locks_guard = {}, threading.Lock()


# How long an action waits for another action on the same sender (one holding its lock can take a minute and more on
# a big sender) before it gives up on that sender and says so, instead of holding a request slot with no limit and
# running long after its page has stopped waiting.
SENDER_WAIT = float(os.environ.get("SENDER_WAIT", "30"))
BUSY_NOTE = "another action on this sender is still running: try again when it has finished"


class SenderBusy(RuntimeError):
    """_sender_lock waited SENDER_WAIT seconds for another action on the sender, and gave up."""


@contextlib.contextmanager
def _sender_lock(acc_id, addr, wait=None):
    """One sender's filters in one account are changed by one action at a time: the old filters removed and the new
    ones made as one step, so two actions at once (two tabs, Block then Auto-archive) cannot leave both sets. Re-entrant:
    an action holding it may create filters (_filter takes it again). Waits at most `wait` (default SENDER_WAIT)
    seconds for another action, then raises SenderBusy and changes nothing."""
    key = (acc_id, (addr or "").lower())
    with _filter_locks_guard:
        ent = _filter_locks.get(key)
        if ent is None:
            ent = _filter_locks[key] = [threading.RLock(), 0]
        ent[1] += 1
    try:
        if not ent[0].acquire(timeout=SENDER_WAIT if wait is None else wait):
            raise SenderBusy(BUSY_NOTE)
        try:
            yield
        finally:
            ent[0].release()
    finally:
        with _filter_locks_guard:
            ent[1] -= 1
            if not ent[1]:
                _filter_locks.pop(key, None)


def sender_busy(acc_id, addr):
    """True while some action holds or waits for this sender's lock."""
    with _filter_locks_guard:
        return (acc_id, (addr or "").lower()) in _filter_locks


# After a filter POST that got no answer, Zero Sum looks for the filter right away and again after these many seconds,
# so one Gmail makes a moment late is still found and recorded. What it claims is a filter that matches exactly and
# was not there just before the POST. Gmail filters carry no creation time, so one edge remains: an identical filter
# made by hand in Gmail (or by another tool) in those few seconds, while Zero Sum's own POST was lost, would be
# recorded as Zero Sum's (with source "... (claimed)"), and a later Keep, Protect or Reset would remove it.
CLAIM_DELAYS = (2, 5)
_claim_wait = time.sleep


def _claim_new(acc_id, before, crit, action):
    """The filter matching `crit` -> `action` exactly that was not there before the POST, or None."""
    return next((x for x in gmail(acc_id, "/settings/filters").get("filter", [])
                 if x.get("id") not in before and (x.get("criteria") or {}) == crit
                 and _same_action(x.get("action") or {}, action)), None)


def _create_filter(acc_id, addr, action, kind, crit, source):
    # the filters there before: a POST whose outcome is unknown may only claim one that was not among them
    before = {x.get("id") for x in gmail(acc_id, "/settings/filters").get("filter", [])}
    try:
        f = gmail(acc_id, "/settings/filters", "POST", {"criteria": crit, "action": action})
    except RuntimeError as e:
        if "exists" not in str(e).lower():
            raise
        if not getattr(e, "retried", False):
            return None  # an identical filter is already there: recorded earlier, or not this app's (then it stays so)
        # "exists" answered a repeat of this request: the first try may have made it before its answer was lost. A
        # matching filter that was not there before the POST is this app's, so it is found and recorded like any other
        # (else Protect, Keep and Reset could never remove it); one that was there before is left alone
        f = _claim_new(acc_id, before, crit, action)
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        # no answer at all: Gmail may have made the filter before the connection dropped, or may still be making it
        # (a read timeout). Look now and again after CLAIM_DELAYS, and record it if a new filter matching exactly shows
        # up; if none does, nothing was made and the error stands
        f = None
        for wait in (0,) + tuple(CLAIM_DELAYS):
            if wait:
                _claim_wait(wait)
            try:
                f = _claim_new(acc_id, before, crit, action)
            except Exception:  # noqa: BLE001 - cannot look now: try again, else report the original error
                f = None
            if f:
                break
        if not f:
            raise
        source = f"{source} (claimed)"   # kept apart in the record: see CLAIM_DELAYS for the case it cannot tell
    fid = (f or {}).get("id")
    if not fid:
        return None
    try:
        with db() as c:
            c.execute("INSERT INTO unified.filter (account_id, filter_id, addr, kind, source) VALUES (%s, %s, %s, %s, %s) "
                      "ON CONFLICT (account_id, filter_id) DO UPDATE SET addr = EXCLUDED.addr, kind = EXCLUDED.kind, "
                      "source = EXCLUDED.source", (acc_id, fid, addr, kind, str(source)[:60]))
    except Exception:
        try:  # a filter nobody has on record could never be cleaned up again: take it back
            gmail(acc_id, f"/settings/filters/{fid}", "DELETE")
        except Exception:  # noqa: BLE001
            pass
        raise
    return fid


def _same_action(a, b):
    return all(sorted(a.get(k) or []) == sorted(b.get(k) or []) for k in ("addLabelIds", "removeLabelIds")) and \
        a.get("forward") == b.get("forward")


def remove_app_filters(acc_id, keys, kinds=AUTOMATION_KINDS, exact=False):
    """Delete the Gmail filters this app created (unified.filter) in one account for a sender, a domain ("@example.com",
    subdomains included) or a list of them, of the given kinds. Only recorded filters are touched: a filter someone
    made by hand in Gmail, even `from:x -> Trash`, is never deleted. With exact=True the recorded sender must equal
    the key (unprotecting a domain leaves the never-spam filter of a separately protected address). Returns how many."""
    keys = [keys] if isinstance(keys, str) else list(keys)
    with db() as c:
        rows = c.execute("SELECT filter_id, addr FROM unified.filter WHERE account_id = %s AND kind = ANY(%s) "
                         "ORDER BY created_at, filter_id", (acc_id, list(kinds))).fetchall()
    n = 0
    for fid, addr in rows:
        if not any((addr == k) if exact else sender_matches(addr, k) for k in keys):
            continue
        try:
            gmail(acc_id, f"/settings/filters/{fid}", "DELETE")
        except RuntimeError as e:
            if ": 404 " not in str(e):  # 404: already removed by hand in Gmail
                raise
        with db() as c:
            c.execute("DELETE FROM unified.filter WHERE account_id = %s AND filter_id = %s", (acc_id, fid))
        n += 1
    return n


def remove_trash_filters(acc_id, key):
    """Delete this app's send-to-Trash filters for a sender or domain (see remove_app_filters). Returns how many."""
    return remove_app_filters(acc_id, key, kinds=("trash",))


def foreign_filters(acc_id, keys):
    """How many Gmail filters that this app did NOT create still take these senders' mail out of the inbox or to
    Trash. They are left alone; the caller tells the user, because a "protected" sender is then not fully protected."""
    keys = [keys] if isinstance(keys, str) else list(keys)
    with db() as c:
        mine = {r[0] for r in c.execute("SELECT filter_id FROM unified.filter WHERE account_id = %s", (acc_id,)).fetchall()}
    n = 0
    for f in gmail(acc_id, "/settings/filters").get("filter", []):
        act = f.get("action") or {}
        moves = "TRASH" in (act.get("addLabelIds") or []) or "INBOX" in (act.get("removeLabelIds") or [])
        who = (f.get("criteria") or {}).get("from")
        if moves and f.get("id") not in mine and any(sender_matches(who, k) for k in keys):
            n += 1
    return n


FOREIGN_NOTE = "{n} Gmail filter(s) not made by Zero Sum still move this sender's mail: left in place, remove them in Gmail"
KEEPQ = re.compile(r"subject:\([^()]*\)( OR subject:\([^()]*\))*")


def _shape(f, qlid, prot):
    """The kind of a Gmail filter that has exactly a shape this app creates (`from:` one sender, optionally with the
    subject query keep words produce, and one of its actions), else None."""
    crit, act = f.get("criteria") or {}, f.get("action") or {}
    who = str(crit.get("from") or "").lower()
    if not set(crit) <= {"from", "query", "negatedQuery"} or not SENDER_KEY.fullmatch(who):
        return None
    if any(k in crit and not KEEPQ.fullmatch(str(crit[k])) for k in ("query", "negatedQuery")):
        return None
    if any(not isinstance(v, list) for v in act.values()):
        return None
    act = {k: sorted(v) for k, v in act.items() if v}
    bare = set(crit) == {"from"}
    if act == NEVER_SPAM_FILTER:
        return "never_spam" if bare and who in prot else None
    if who.startswith("@"):  # only Protect makes a filter for a whole domain
        return None
    if act == TRASH_FILTER:
        return "trash"
    if act == ARCHIVE_FILTER:
        return "archive"
    if qlid and act == {"addLabelIds": [qlid], "removeLabelIds": ["INBOX"]}:
        return "quarantine"
    if act == STAR_FILTER:
        return "star" if bare else "keep_star"
    return None


def adopt_filters(d=None):
    """One-off: put the filters this app created before it kept records into unified.filter. A report only, unless
    apply is set. A filter is a candidate when it has exactly a shape this app creates; it is adopted when the
    database also shows this app acted on that sender in that account (a clean-up status, an applied plan, or a
    protected key), or always with all=true. Adoption only records: it never deletes or changes a filter. Running
    it again finds nothing new."""
    d = d or {}
    apply, every = bool(d.get("apply")), bool(d.get("all"))
    prot = protected_set()
    with db() as c:
        planned = {r[0] for r in c.execute("SELECT addr FROM unified.ai_plan WHERE status IN ('applied', 'partial')").fetchall()}
        handled = {(a, e) for a, e in c.execute('SELECT "emailAccountId", lower(email) FROM "Newsletter" '
                                                "WHERE status IS NOT NULL").fetchall()}
    out = []
    for acc in ACC.list():
        rep = {"email": acc["email"], "filters": 0, "recorded": 0, "other": 0, "candidates": [], "adopted": 0}
        out.append(rep)
        try:
            with db() as c:
                mine = {r[0] for r in c.execute("SELECT filter_id FROM unified.filter WHERE account_id = %s",
                                                (acc["id"],)).fetchall()}
            qlid = label_id(acc["id"], QUARANTINE, create=False)
            for f in gmail(acc["id"], "/settings/filters").get("filter", []):
                rep["filters"] += 1
                if f.get("id") in mine:
                    rep["recorded"] += 1
                    continue
                kind = _shape(f, qlid, prot)
                if not kind:
                    rep["other"] += 1
                    continue
                who = f["criteria"]["from"].lower()
                why = ("protected" if kind == "never_spam" else "cleanup status" if (acc["id"], who) in handled
                       else "applied plan" if who in planned else None)
                cand = {"id": f["id"], "from": who, "kind": kind, "evidence": why, "adopt": bool(why or every)}
                rep["candidates"].append(cand)
                if apply and cand["adopt"]:
                    with db() as c:
                        c.execute("INSERT INTO unified.filter (account_id, filter_id, addr, kind, source) "
                                  "VALUES (%s, %s, %s, %s, 'adopted') ON CONFLICT DO NOTHING", (acc["id"], f["id"], who, kind))
                    rep["adopted"] += 1
        except Exception as e:  # noqa: BLE001 - one account failing must not hide the others' report
            rep["error"] = str(e)[:200]
    if apply:
        log("cleanup", "adopt filters: " + "; ".join(f"{r['email']}: {r['adopted']} recorded" for r in out))
    return {"applied": apply, "accounts": out}


def undo_list(q=None):
    """Bulk changes of the last UNDO_DAYS days, newest first, for the Undo list."""
    with db() as c:
        rows = c.execute("SELECT id, at, account_id, note, add_labels, remove_labels, cardinality(ids), undone_at "
                         "FROM unified.action_log WHERE at > now() - make_interval(days => %s) "
                         "ORDER BY id DESC LIMIT 60", (UNDO_DAYS,)).fetchall()
    names = {a["id"]: a["email"] for a in ACC.list()}
    return {"days": UNDO_DAYS, "actions": [
        {"id": i, "at": at.isoformat(), "email": names.get(acc, acc), "note": note, "add": add, "remove": rem,
         "count": n, "undone": undone.isoformat() if undone else None}
        for i, at, acc, note, add, rem, n, undone in rows]}


def undo_action(d):
    """Put the messages of one recorded bulk change back as they were: its added labels come off (out of Trash,
    Spam or Quarantine), its removed labels go back on (INBOX). Filters are not touched: Keep or Reset removes those.

    The change is claimed (undone_at set) in the same statement that reads it, so a double click or a second tab
    cannot run one undo twice; if Gmail then fails, the claim is released and Undo can be tried again. A change older
    than UNDO_DAYS is refused even before the journal is pruned: Gmail may already have emptied that Trash."""
    aid = int(d.get("id") or 0)
    with db() as c:
        row = c.execute("UPDATE unified.action_log SET undone_at = now() WHERE id = %s AND undone_at IS NULL "
                        "AND at > now() - make_interval(days => %s) "
                        "RETURNING account_id, add_labels, remove_labels, ids", (aid, UNDO_DAYS)).fetchone()
        if not row:
            seen = c.execute("SELECT undone_at FROM unified.action_log WHERE id = %s "
                             "AND at > now() - make_interval(days => %s)", (aid, UNDO_DAYS)).fetchone()
    if not row:
        raise ValueError("already undone" if seen and seen[0] else "no such action (older than %d days?)" % UNDO_DAYS)
    acc, add, rem, ids = row
    back_add, back_rem, ids, held = list(rem), list(add), list(ids), []
    if ids and removes_mail(back_add, back_rem):
        # putting mail back somewhere out of view (Quarantine, after a delete from Quarantine) would be refused by
        # _batch for a sender protected since: those messages still come out of Trash / Spam, into the inbox
        prot, own = protected_set(), own_set()
        with db() as c:
            known = c.execute("SELECT id, from_addr FROM unified.msg WHERE account_id = %s AND id = ANY(%s)",
                              (acc, ids)).fetchall()
        mine = {i for i, frm in known if own_address(frm, own) or is_protected(frm, prot)}
        held, ids = [i for i in ids if i in mine], [i for i in ids if i not in mine]
    try:
        n = _batch(acc, ids, add=back_add, remove=back_rem, record=False)
        if held:
            n += _batch(acc, held, add=["INBOX"], remove=[x for x in back_rem if x != "INBOX"], record=False)
    except Exception:
        with db() as c:   # nothing (or not everything) was put back: let the person try again
            c.execute("UPDATE unified.action_log SET undone_at = NULL WHERE id = %s", (aid,))
        raise
    try:
        # Where the restored mail is now. Mail the undo took out of the inbox is out of it; mail it put INBOX back on is
        # marked as in the inbox right away. INBOX also goes back on mail that has been put in Trash or Spam since (an
        # archive undone after a delete), which Gmail does not count as in the inbox, so up to UNDO_REFRESH of the
        # restored messages (only those ids: the account's whole Trash is never listed) are read back from Gmail in the
        # background, after this request has answered. _store(refill=True) sets the flag from the labels Gmail
        # returns, and also reads back the rows Delete/Block dropped, so the sender shows up again.
        inbox = held + (ids if "INBOX" in back_add else [])
        out = ids if "INBOX" in back_rem else []
        with db() as c:
            for flag, which in ((True, inbox), (False, out)):
                for i in range(0, len(which), 5000):
                    c.execute("UPDATE unified.msg SET inbox = %s WHERE account_id = %s AND id = ANY(%s)",
                              (flag, acc, which[i:i + 5000]))
        check = inbox[:UNDO_REFRESH]
        seen = set(check)
        rest = [i for i in list(ids) + held if i not in seen][:max(0, UNDO_REFRESH - len(check))]
        if check or rest:
            _background(_undo_read_back, aid, acc, check, rest)
    except Exception as e:  # noqa: BLE001 - the mail itself is restored; the collector fills the rest
        log("cleanup-error", f"undo #{aid}: history not refreshed: {e}"[:300], ACC.email(acc))
    note = (f"{len(held)} from a protected sender or your own address went back to the inbox, not to where the "
            "change had taken them from") if held else ""
    log("cleanup", f"undo #{aid}: {n} messages restored" + (f"; {note}" if note else ""), ACC.email(acc))
    return {"ok": True, "restored": n, **({"held": len(held), "note": note} if held else {})}


# Background work (the undo read-back) runs on its own two threads, not on core.POOL: a read-back can wait minutes
# for spare quota, and the request listings (inbox, analytics, AI Cleanup) need POOL's threads meanwhile. More undos
# than this queue up and run in turn.
BACKGROUND = ThreadPoolExecutor(max_workers=2, thread_name_prefix="background")


def _background(fn, *args):
    """Run fn(*args) on the background threads, after the current request has answered. Nobody waits for the
    result, so anything it raises (even from its own last log line) is logged here instead of vanishing."""
    def job():
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001
            try:
                log("cleanup-error", f"background {getattr(fn, '__name__', 'job')} failed: {e}"[:300])
            except Exception:  # noqa: BLE001 - logging itself failed: at least the container log has it
                traceback.print_exc()
    return BACKGROUND.submit(job)


def _undo_read_back(aid, acc, check, rest):
    """undo_action's read-back, in the background: `check` is read again (refill), `rest` only where the local history
    lacks it. A few messages at a time, and only while nothing else is spending the account's Gmail quota (a request
    or the collector goes first); after waiting too long it stops, and the flags stay as the undo set them."""
    failed = []
    for part, refill in ((check, True), (rest, False)):
        for i in range(0, len(part), UNDO_STEP):
            try:
                if not gmail_spare(acc, cost=UNDO_STEP * 5):   # 5 units per metadata read
                    log("cleanup-error", f"undo #{aid}: read-back stopped, the account's Gmail quota stayed busy",
                        ACC.email(acc))
                    return
                _store(acc, part[i:i + UNDO_STEP], refill=refill)
            except Exception as e:  # noqa: BLE001 - one step failing must not skip the others
                failed.append(e)
    if failed:   # the mail itself is restored; the collector fills the rest
        log("cleanup-error", f"undo #{aid}: history not fully refreshed: {failed[0]}"[:300], ACC.email(acc))


def bulk_query(query):
    """The search a bulk change really runs: a sender action never reaches what the account itself sent, its drafts
    or chats. Used by the action and by its preview, so the two cannot drift apart."""
    return query if "in:sent" in query else f"{query} {SKIP}"


def _modify_existing(acc_id, query, add=(), remove=(), *, sender):
    """Apply label changes to every message matching `query` (batches of 1000). Returns the count. `sender` is whose
    mail the query selects: _batch refuses a protected sender and the account's own address.

    Gmail's `from:"addr"` is a token match, not an exact one: it also finds mail whose display name or a longer address
    contains addr ("deals@shop.com" <alerts@bank.com>, deals@shop.com.au). So when the change takes mail out of view,
    every message's real sender is looked up (senders_of, as Quarantine delete does) and only mail really from
    `sender` is changed. The rest is left alone and counted in _ACTION.held: "protected" (a protected sender or one of
    the user's own addresses) and "other" (someone else, or a sender that could not be told)."""
    ids = [m["id"] for m in gmail_list(acc_id, qs(bulk_query(query)), "messages", MAX_BULK)]
    if ids and removes_mail(add, remove):
        prot = protected_set()
        for s in ([sender] if isinstance(sender, str) else sorted(sender or ())):
            guard_sender(s, prot)   # the sender itself first: refused outright, whatever the messages turn out to be
        who = senders_of(acc_id, ids, lookup_budget())
        mine = [i for i in ids if who.get(i) == sender]
        held, own = getattr(_ACTION, "held", None), own_set()
        if held is not None:
            for i in ids:
                if i not in who:
                    held["unchecked"] += 1
                elif who.get(i) != sender:
                    w = who.get(i)
                    held["protected" if w and (own_address(w, own) or is_protected(w, prot)) else "other"] += 1
        ids = mine
    n = _batch(acc_id, ids, add, remove, sender=sender)
    held = getattr(_ACTION, "held", None)
    if held is not None:
        held["done"] += n
    return n


def held_start():
    """Start counting what _modify_existing leaves alone for this request (per account)."""
    _ACTION.held = {"protected": 0, "other": 0, "unchecked": 0, "done": 0}


UNCHECKED_NOTE = "{n} not done yet (their senders were not looked up in this run): run it again to continue"
STALLED_NOTE = "{n} not done: {why}"
NO_READS = "Gmail did not return their senders just now (it refused or did not answer): try again later"
NO_BUDGET = "no sender look-ups were left for this run (BULK_LOOKUPS)"


def unchecked_note(n, done):
    """The note for n messages whose senders were not looked up. When this run changed some mail or learned some
    senders, running it again gets further: "run it again", and the request is marked partial. When it did neither,
    running it again would only repeat it: the note says what stopped it instead, and nothing is marked partial."""
    seen = getattr(_ACTION, "seen", None) or {}
    if done or seen.get("known"):
        _ACTION.partial = True
        return UNCHECKED_NOTE.format(n=n)
    return STALLED_NOTE.format(n=n, why=NO_READS if seen.get("read") else NO_BUDGET)


def held_note():
    """'' or a note saying how many messages were left alone, and why; resets the count."""
    h = getattr(_ACTION, "held", None) or {}
    out = []
    if h.get("protected"):
        out.append(f"{h['protected']} from a protected sender or your own address left alone")
    if h.get("other"):
        out.append(f"{h['other']} sent by someone else (or sender unknown) left alone")
    if h.get("unchecked"):
        out.append(unchecked_note(h["unchecked"], h.get("done")))
    held_start()
    return "; ".join(out)


# Most message senders one request (a Cleanup action, a plan, a Quarantine action) reads from Gmail for mail the local
# history does not have yet, over all its accounts: each read is one messages.get, about 10 a second per account at the
# default GMAIL_UNITS_PER_SEC, and the request has to answer well inside the proxy's timeout (Cloudflare: 100 s). The
# messages not looked up are left alone and the result says "run it again"; the senders read are remembered
# (unified.sender_seen), so the next run starts where this one stopped. Each account gets an equal share of what is
# left when its turn comes (what one does not use goes to the next), so one account cannot starve the others.
# Background work (AI sort) has no limit.
# DEFAULT - Roy may change: 300 (about 30 s)
BULK_LOOKUPS = int(os.environ.get("BULK_LOOKUPS", "300"))


def lookups_start(n=None):
    """Give this request (thread) its budget of Gmail sender look-ups; see BULK_LOOKUPS."""
    _ACTION.lookups, _ACTION.partial, _ACTION.cap = [BULK_LOOKUPS if n is None else n], False, None
    _ACTION.seen = {"read": 0, "known": 0}


def lookups_account(accounts_left):
    """The next account's share of this request's look-ups: an equal part of what is left over the accounts still to
    go (this one included). Also restarts the count of what this account's look-ups learned (unchecked_note)."""
    left = getattr(_ACTION, "lookups", None)
    _ACTION.seen = {"read": 0, "known": 0}
    if left is not None:
        _ACTION.cap = [-(-max(0, left[0]) // max(1, accounts_left))]


def lookups_end():
    """True when some of this request's work was left for another run; ends the budget."""
    partial = bool(getattr(_ACTION, "partial", False))
    _ACTION.lookups, _ACTION.partial, _ACTION.cap, _ACTION.seen = None, False, None, None
    return partial


def lookup_budget():
    """senders_of's `budget` for this request, or None (no limit) outside one."""
    left = getattr(_ACTION, "lookups", None)
    if left is None:
        return None
    cap = getattr(_ACTION, "cap", None)

    def budget(n):
        k = min(n, max(0, left[0]), max(0, cap[0]) if cap else n)
        left[0] -= k
        if cap:
            cap[0] -= k
        return k
    return budget


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


def _batch(acc_id, ids, add=(), remove=(), record=True, sender=None):
    """Apply label changes to these message ids (batches of 1000). Returns the count.

    Every bulk change ends here, so this is where a protected sender and the user's own address are refused, whatever
    the entry point checked. `sender` is the address (or addresses) the ids belong to. Without it, a change that takes
    mail out of view leaves out the messages the local history attributes to a protected or own address; with it, the
    messages the local history attributes to anyone else are left out (callers look the real sender up first)."""
    ids = list(ids)
    if ids and removes_mail(add, remove):
        prot = protected_set()
        if sender is not None:
            whose = {sender} if isinstance(sender, str) else set(sender)
            for s in sorted(whose):
                guard_sender(s, prot)
            with db() as c:
                other = {i for i, frm in c.execute("SELECT id, from_addr FROM unified.msg WHERE account_id = %s AND "
                                                   "id = ANY(%s)", (acc_id, ids)).fetchall() if frm not in whose}
            ids = [i for i in ids if i not in other]
        else:
            with db() as c:
                known = c.execute("SELECT id, from_addr FROM unified.msg WHERE account_id = %s AND id = ANY(%s)",
                                  (acc_id, ids)).fetchall()
            if known:
                own = own_set()
                held = {i for i, frm in known if own_address(frm, own) or is_protected(frm, prot)}
                ids = [i for i in ids if i not in held]
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


QUARANTINE = os.environ.get("QUARANTINE_LABEL", "Quarantine")


def sender_recent(q):
    """The newest N emails from a sender across every account and folder (Trash and Spam excluded), for previews."""
    addr = sender_addr(q.get("addr", [""])[0])
    n = max(1, min(int(q.get("n", ["20"])[0]), 50))

    def ids(a):
        try:
            return [(a, m["id"]) for m in gmail_list(a["id"], qs(f"{frm(addr)} -in:trash -in:spam"), "messages", n)]
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
