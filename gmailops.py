"""Everything that changes Gmail in bulk, in one place: the filters this app creates (each recorded in unified.filter,
and only those are ever removed), label changes on existing mail (_batch: recorded with the messages they touch
before Gmail is called, refused for protected senders and own addresses), and the undo journal built from that
record (unified.action_log)."""
import email.utils, os, re, threading

from collector import CPOOL, SKIP, _store, qs
from core import ACC, POOL, db, gmail, gmail_list, header, label_id, log
from guard import (frm, guard_sender, is_protected, own_address, own_set, protected_set, removes_mail, sender_addr,
                   sender_matches, SENDER_KEY)
from inbox import meta

MAX_BULK = int(os.environ.get("MAX_BULK", "200000"))  # most messages one bulk action touches per account


def senders_of(acc_id, ids):
    """{message id: sender address, or None when it cannot be told}: from the local history, else read from Gmail."""
    who = {}
    for i in range(0, len(ids), 5000):
        with db() as c:
            who.update(c.execute("SELECT id, from_addr FROM unified.msg WHERE account_id = %s AND id = ANY(%s)",
                                 (acc_id, ids[i:i + 5000])).fetchall())

    def read(mid):
        try:
            m = gmail(acc_id, f"/messages/{mid}?format=metadata&metadataHeaders=From")
            return email.utils.parseaddr(header(m.get("payload", {}).get("headers", []), "from"))[1].lower() or None
        except Exception:  # noqa: BLE001 - unknown stays unknown: the caller leaves that message alone
            return None
    todo = [i for i in ids if i not in who]
    who.update(zip(todo, CPOOL.map(read, todo)))
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
    try:
        f = gmail(acc_id, "/settings/filters", "POST", {"criteria": crit, "action": action})
    except RuntimeError as e:
        if "exists" not in str(e).lower():
            raise
        return None  # an identical filter is already there: recorded earlier, or not this app's (then it stays so)
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
        planned = {r[0] for r in c.execute("SELECT addr FROM unified.ai_plan WHERE status = 'applied'").fetchall()}
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
    try:
        n = _batch(acc, list(ids), add=list(rem), remove=list(add), record=False)
    except Exception:
        with db() as c:   # nothing (or not everything) was put back: let the person try again
            c.execute("UPDATE unified.action_log SET undone_at = NULL WHERE id = %s", (aid,))
        raise
    try:  # Delete/Block dropped these messages from the local history: read them back so the sender shows up again
        _store(acc, list(ids)[:5000])
    except Exception as e:  # noqa: BLE001 - the mail itself is restored; the collector fills the rest
        log("cleanup-error", f"undo #{aid}: history not refreshed: {e}"[:300], ACC.email(acc))
    log("cleanup", f"undo #{aid}: {n} messages restored", ACC.email(acc))
    return {"ok": True, "restored": n}


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
        who = senders_of(acc_id, ids)
        mine = [i for i in ids if who.get(i) == sender]
        held, own = getattr(_ACTION, "held", None), own_set()
        if held is not None:
            for i in ids:
                if who.get(i) != sender:
                    w = who.get(i)
                    held["protected" if w and (own_address(w, own) or is_protected(w, prot)) else "other"] += 1
        ids = mine
    return _batch(acc_id, ids, add, remove, sender=sender)


def held_start():
    """Start counting what _modify_existing leaves alone for this request (per account)."""
    _ACTION.held = {"protected": 0, "other": 0}


def held_note():
    """'' or a note saying how many messages were left alone, and why; resets the count."""
    h = getattr(_ACTION, "held", None) or {}
    out = []
    if h.get("protected"):
        out.append(f"{h['protected']} from a protected sender or your own address left alone")
    if h.get("other"):
        out.append(f"{h['other']} sent by someone else (or sender unknown) left alone")
    held_start()
    return "; ".join(out)


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
