"""What the Cleanup, Quarantine and AI Cleanup tabs do: the sender list, sender actions (applied to every account that
has mail from the sender, mirroring Inbox Zero's Newsletter status, a Gmail filter for future mail and a label change
on existing mail), protection, the Quarantine tab, word plans and their preview. Unsubscribing uses RFC 8058 one-click
POST when the sender supports it; other links are returned for the user to open, since this app never sends email."""
import http.client, ipaddress, os, re, socket, ssl, threading, urllib.parse

from aisort import start_ai_sort, stop_automation
from collector import BACKFILL_DAYS, collect_status, qs
from core import ACC, POOL, db, gmail_list, label_id, log, new_id
from gmailops import (ARCHIVE_FILTER, AUTOMATION_KINDS, FOREIGN_NOTE, MAX_BULK, NEVER_SPAM_FILTER, QUARANTINE, STAR_FILTER,
                      TRASH_FILTER, _ACTION, _batch, _filter, _modify_existing, bulk_query, foreign_filters,
                      remove_app_filters, senders_of)
from guard import (SENDER_KEY, frm, guard_sender, is_protected, keep_query, own_address, parse_protect_keys,
                   protected_set, sender_addr)
from inbox import meta


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


def _protect_in_gmail(keys):
    """For a bulk protect: per account, drop the filters Zero Sum itself made for these senders (Trash, archive,
    quarantine) and add "never send to Spam" for each. Runs in the background; the protection itself is already in the database."""
    for acc in ACC.list():
        done = cleared = 0
        try:
            cleared = remove_app_filters(acc["id"], keys)
            left = foreign_filters(acc["id"], keys)
            for k in keys:
                _filter(acc["id"], k, NEVER_SPAM_FILTER, "never_spam", source="protect")
                done += 1
            log("cleanup", f"bulk protect: {done} never-spam filter(s), {cleared} of Zero Sum's own filter(s) removed"
                + (f"; {left} filter(s) Zero Sum did not create still move mail from these senders (left in place)"
                   if left else ""), acc["email"])
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


def _archive_existing(acc_id, addr, keep=None):
    n = _modify_existing(acc_id, sender_query("archive", addr, keep), remove=["INBOX"], sender=addr)
    with db() as c:
        c.execute("UPDATE unified.msg SET inbox = false WHERE account_id = %s AND from_addr = %s", (acc_id, addr))
    return n


def _drop_rows(acc_id, addr):
    with db() as c:
        c.execute("DELETE FROM unified.msg WHERE account_id = %s AND from_addr = %s", (acc_id, addr))


class _Pinned(http.client.HTTPSConnection):
    """HTTPS to one already-checked IP address, with the certificate still verified for the host name: the name
    cannot be re-resolved to somewhere else between the check and the connection."""

    def __init__(self, host, ip, timeout):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        self.sock = self._context.wrap_socket(socket.create_connection((self._ip, 443), self.timeout),
                                              server_hostname=self.host)


def unsub_target(url):
    """(host, ip, path) for a one-click unsubscribe URL, or ValueError. The URL comes from a stranger's email header,
    so it may only lead out to the public internet: https, port 443, no credentials, and every address the host
    resolves to must be public (not loopback, private, link-local, cloud metadata, or otherwise reserved)."""
    u = urllib.parse.urlsplit(str(url or ""))
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.port not in (None, 443):
        raise ValueError("unsubscribe link is not a plain https address")
    host = u.hostname.lower().rstrip(".")
    try:
        ips = {ipaddress.ip_address(i[4][0].split("%")[0]) for i in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except (OSError, ValueError):
        raise ValueError("unsubscribe host does not resolve") from None
    for ip in ips:
        real = getattr(ip, "ipv4_mapped", None) or ip
        if not real.is_global or real.is_multicast:
            raise ValueError("unsubscribe link points at a private or reserved address")
    if not ips:
        raise ValueError("unsubscribe host does not resolve")
    ip = sorted(ips, key=lambda i: (i.version, int(i)))[0]
    return host, str(ip), (u.path or "/") + (f"?{u.query}" if u.query else "")


def _one_click(url):
    """RFC 8058 one-click unsubscribe: one POST, to a public https address only (unsub_target), never following a
    redirect, 10 seconds at most. True only on a 2xx answer; anything else leaves the link for the user to open."""
    try:
        host, ip, path = unsub_target(url)
        conn = _Pinned(host, ip, timeout=10)
        try:
            conn.request("POST", path, body=b"List-Unsubscribe=One-Click",
                         headers={"Content-Type": "application/x-www-form-urlencoded",
                                  "User-Agent": "Mozilla/5.0 (unsubscribe)"})
            return 200 <= conn.getresponse().status < 300
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - refused, unreachable or odd: not unsubscribed
        return False


SHOW = int(os.environ.get("QUARANTINE_SHOW", "600"))  # messages per account listed with details on the Quarantine tab
# actions that remove mail from view: refused for protected senders
DESTRUCTIVE = {"unsubscribe", "unsub_archive", "unsub_quarantine", "unsub_delete", "autoarchive", "archive",
               "quarantine", "delete", "block", "spam"}
UNSUB = {"unsubscribe", "unsub_archive", "unsub_quarantine", "unsub_delete"}
ACTIONS = DESTRUCTIVE | {"keep", "reset", "star", "unstar", "protect", "unprotect"}
# actions that set up filters for future mail: each replaces the sender's earlier automation instead of adding to it
FILTERING = {"unsub_delete", "quarantine", "unsub_quarantine", "autoarchive", "unsub_archive", "block"}
# Which existing mail each sender action changes, as a Gmail search after `from:"sender"`. One table for the action
# and for its preview (preview()), so the confirmation dialog counts exactly what the action will touch.
EXISTING = {"unsub_delete": "-in:trash", "delete": "-in:trash", "block": "-in:trash",
            "quarantine": f"-in:trash -in:spam -label:{QUARANTINE}", "unsub_quarantine": f"-in:trash -in:spam -label:{QUARANTINE}",
            "autoarchive": "in:inbox", "unsub_archive": "in:inbox", "archive": "in:inbox",
            "spam": "-in:spam -in:trash", "star": "in:inbox -is:starred", "unstar": "is:starred"}


def sender_query(act, addr, kq=None):
    """The search for the existing mail a sender action changes (None: the action changes no existing mail).
    `kq` (keep words) leaves the kept subjects out of a destructive action."""
    if act not in EXISTING:
        return None
    return f"{frm(addr)} {EXISTING[act]}" + (f" -{kq}" if kq and act in DESTRUCTIVE else "")


def kept_query(addr, kq):
    """The kept part of a destructive action with keep words: starred now (and, by filter, in future)."""
    return f"{frm(addr)} {kq} -is:starred -in:trash"


def sender_action(d):
    addr, act = (d.get("addr") or "").strip().lower(), d.get("action")
    if act not in ACTIONS:
        raise ValueError("bad sender action")
    if act in ("protect", "unprotect"):
        if len(addr) > 254 or not SENDER_KEY.fullmatch(addr):
            raise ValueError("protect an address (name@example.com) or a domain (@example.com)")
        with db() as c:
            if act == "protect":
                c.execute("INSERT INTO unified.protected (key) VALUES (%s) ON CONFLICT DO NOTHING", (addr,))
            else:
                c.execute("DELETE FROM unified.protected WHERE key = %s", (addr,))
        cleared = left = 0
        if act == "protect":
            stop_automation(addr)
        for acc in ACC.list():
            try:
                if act == "protect":  # what was set up before the protection must stop moving its mail: Trash,
                    cleared += remove_app_filters(acc["id"], addr)  # archive and quarantine filters alike
                    _filter(acc["id"], addr, NEVER_SPAM_FILTER, "never_spam", source="protect")
                    left += foreign_filters(acc["id"], addr)
                else:
                    remove_app_filters(acc["id"], addr, kinds=("never_spam",), exact=True)
            except Exception as e:  # noqa: BLE001 - protection in Zero Sum still applies
                log("cleanup-error", f"{act} {addr}: filters: {e}"[:300], acc["email"])
        if cleared:
            log("cleanup", f"protect {addr}: removed {cleared} of Zero Sum's own filter(s)")
        if left:
            log("cleanup", f"protect {addr}: " + FOREIGN_NOTE.format(n=left))
        if act == "protect" and not addr.startswith("@"):  # also "kept" in Inbox Zero for every account that gets it
            with db() as c:
                for (a,) in c.execute("SELECT DISTINCT account_id FROM unified.msg WHERE from_addr = %s", (addr,)).fetchall():
                    _newsletter(c, a, addr, "APPROVED")
        log("cleanup", f"{act} {addr}")
        return {"results": [{"email": "all accounts", "ok": True, "note": f"{addr} {act}ed"
                             + (f"; {cleared} filter(s) removed" if cleared else "")
                             + ("; " + FOREIGN_NOTE.format(n=left) if left else "")}], "manual": []}
    addr = sender_addr(addr)
    if act in DESTRUCTIVE and is_protected(addr):
        raise PermissionError(f"{addr} is protected: unprotect it first")
    if act in DESTRUCTIVE and own_address(addr):
        raise PermissionError(f"{addr} is one of your own accounts: its mail is never cleaned up")
    kq = keep_query(d.get("keep")) if act in DESTRUCTIVE else None
    _ACTION.note = f"{act} {addr}"
    if act in ("keep", "reset"):
        stop_automation(addr)
    with db() as c:
        acc_ids = [r[0] for r in c.execute("SELECT DISTINCT account_id FROM unified.msg WHERE from_addr = %s",
                                           (addr,)).fetchall()]
        unsub = {a: (u, p) for a, u, p in c.execute(
            "SELECT DISTINCT ON (account_id) account_id, unsub, unsub_post FROM unified.msg "
            "WHERE from_addr = %s AND unsub IS NOT NULL ORDER BY account_id, ts DESC", (addr,)).fetchall()}
        if act in ("keep", "reset"):  # Delete and Block drop the sender's local history: its filters still say where
            acc_ids += [r[0] for r in c.execute("SELECT DISTINCT account_id FROM unified.filter WHERE addr = %s",
                                                (addr,)).fetchall() if r[0] not in acc_ids]
    if d.get("accounts"):
        acc_ids = [a for a in acc_ids if a in set(d["accounts"])]
    results, manual = [], []
    for a in acc_ids:
        em = ACC.email(a)
        try:
            note = []
            if act in FILTERING:  # a new action replaces the sender's earlier filters, it does not stack on them
                remove_app_filters(a, addr)
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
                _filter(a, addr, TRASH_FILTER, "trash", keep=kq, source=act)
                note.append(f"{_modify_existing(a, sender_query(act, addr, kq), add=['TRASH'], sender=addr)} moved to Trash, "
                            "future mail goes to Trash")
                if not kq:
                    _drop_rows(a, addr)
            if act in ("quarantine", "unsub_quarantine"):  # held for review on the Quarantine tab, now and in future
                lid = label_id(a, QUARANTINE)
                _filter(a, addr, {"addLabelIds": [lid], "removeLabelIds": ["INBOX"]}, "quarantine", keep=kq, source=act)
                n = _modify_existing(a, sender_query(act, addr, kq), add=[lid],
                                     remove=["INBOX"], sender=addr)  # every folder, every year
                note.append(f"filter added, {n} moved to {QUARANTINE}")
                with db() as c:
                    c.execute("UPDATE unified.msg SET inbox = false WHERE account_id = %s AND from_addr = %s", (a, addr))
            if act in ("autoarchive", "unsub_archive"):
                _filter(a, addr, ARCHIVE_FILTER, "archive", keep=kq, source=act)
                note.append(f"filter added, {_archive_existing(a, addr, kq)} archived")
            elif act == "archive":
                note.append(f"{_archive_existing(a, addr, kq)} archived")
            elif act == "delete":
                note.append(f"{_modify_existing(a, sender_query(act, addr, kq), add=['TRASH'], sender=addr)} moved to Trash")
                if not kq:
                    _drop_rows(a, addr)
            elif act == "block":
                _filter(a, addr, TRASH_FILTER, "trash", keep=kq, source=act)
                note.append(f"filter added (future mail goes to Trash), "
                            f"{_modify_existing(a, sender_query(act, addr, kq), add=['TRASH'], sender=addr)} moved to Trash")
                if not kq:
                    _drop_rows(a, addr)
            elif act == "spam":
                note.append(f"{_modify_existing(a, sender_query(act, addr, kq), add=['SPAM'], remove=['INBOX'], sender=addr)} "
                            "reported as spam")
                if not kq:
                    _drop_rows(a, addr)
            elif act in ("keep", "reset"):  # back to normal delivery: none of this app's filters may keep moving
                n = remove_app_filters(a, addr, kinds=AUTOMATION_KINDS + (("star",) if act == "reset" else ()))
                if n:  # this sender's mail (Trash, archive and quarantine alike)
                    note.append(f"{n} filter(s) removed")
                left = foreign_filters(a, addr)
                if left:
                    note.append(FOREIGN_NOTE.format(n=left))
            elif act == "star":
                _filter(a, addr, STAR_FILTER, "star", source=act)
                note.append(f"filter added, {_modify_existing(a, sender_query(act, addr), add=['STARRED'], sender=addr)} starred")
            elif act == "unstar":
                remove_app_filters(a, addr, kinds=("star",), exact=True)
                note.append(f"filter removed, {_modify_existing(a, sender_query(act, addr), remove=['STARRED'], sender=addr)} unstarred")
            if kq and act in DESTRUCTIVE:  # the kept ones: starred now and in future, so they stand out
                _filter(a, addr, STAR_FILTER, "keep_star", query=kq, source=act)
                note.append(f"{_modify_existing(a, kept_query(addr, kq), add=['STARRED'], sender=addr)} kept and starred")
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


def _qlabel(acc_id):
    return label_id(acc_id, QUARANTINE, create=False)


def quarantine_list(q=None):
    """Everything carrying the Quarantine label in every account, newest first, grouped by sender."""
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
    if sender:
        sender = sender_addr(sender)  # it goes into a Gmail search: an address, never a query
    if act == "delete" and sender:
        guard_sender(sender)
    chosen = {}
    items = d.get("items") or []
    for it in items if isinstance(items, list) else []:   # anything not {account: str, id: hex} is left out
        if isinstance(it, dict) and isinstance(it.get("account"), str) and isinstance(it.get("id"), str) \
                and re.fullmatch(r"[0-9a-f]{6,32}", it["id"]):
            chosen.setdefault(it["account"], []).append(it["id"])
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
                query = f"/messages?labelIds={lid}" + (f"&q={urllib.parse.quote(frm(sender))}" if sender else "")
                ids = [m["id"] for m in gmail_list(a["id"], query, "messages", MAX_BULK)]
            if not ids:
                continue
            add = {"delete": ["TRASH"], "archive": [], "release": ["INBOX"]}[act]
            senders, left = None, 0
            if act == "delete":  # "all", ticked messages and one sender alike: every message's real sender is
                who = senders_of(a["id"], ids)  # looked up, and protected, own and unknown ones stay in Quarantine
                prot, own = protected_set(), {x["email"].lower() for x in ACC.list()}
                ok = [i for i in ids if who.get(i) and who[i] not in own and not is_protected(who[i], prot)]
                left, ids, senders = len(ids) - len(ok), ok, {who[i] for i in ok}
            n = _batch(a["id"], ids, add=add, remove=[lid], sender=senders) if ids else 0
            note = f"{n} {QACTIONS[act]}"
            if left:
                note += f"; {left} left in {QUARANTINE} (protected sender, your own address, or sender unknown)"
            if act == "release" and sender and remove_app_filters(a["id"], sender, kinds=("quarantine",), exact=True):
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


PLAN_ACTIONS = ("keep", "archive", "quarantine", "delete")


def _plan_action(acc_id, addr, action, crit_extra, query_extra, lid):
    """One group of a plan: a Gmail filter for future mail + the same change on existing mail. Returns a note."""
    if action == "keep":  # stays in the inbox, starred so it stands out
        filt, add, rem = STAR_FILTER, ["STARRED"], []
    elif action == "archive":
        filt, add, rem = ARCHIVE_FILTER, [], ["INBOX"]
    elif action == "quarantine":
        filt, add, rem = {"addLabelIds": [lid], "removeLabelIds": ["INBOX"]}, [lid], ["INBOX"]
    else:  # delete
        filt, add, rem = TRASH_FILTER, ["TRASH"], []
    _filter(acc_id, addr, filt, "keep_star" if action == "keep" else {"delete": "trash"}.get(action, action),
            query=crit_extra.get("query"), negated=crit_extra.get("negatedQuery"), source="plan")
    return _modify_existing(acc_id, plan_query(addr, action, query_extra), add=add, remove=rem, sender=addr)


PLAN_EXISTING = {"keep": "-is:starred -in:trash -in:spam", "archive": "in:inbox",
                 "quarantine": f"-in:trash -in:spam -label:{QUARANTINE}", "delete": "-in:trash"}


def plan_query(addr, action, query_extra):
    """The search for the existing mail one group of a plan changes (shared by apply_plan and preview)."""
    return " ".join(x for x in (frm(addr), query_extra.strip(), PLAN_EXISTING[action]) if x)


def plan_input(d):
    """(addr, groups, default) from a plan request, validated: groups = [{"label", "kq", "action"}]."""
    addr = sender_addr(d.get("addr"))
    groups = []
    raw_groups = [g for g in (d.get("groups") or []) if any(str(w).strip() for w in (g.get("words") or []))]
    if len(raw_groups) > 6:  # refused, not cut: a dropped "keep" group would hand its mail to the default action
        raise ValueError("at most 6 groups in a plan")
    for g in raw_groups:
        kq = keep_query(g.get("words"))
        if not kq or g.get("action") not in PLAN_ACTIONS:
            raise ValueError(f"group {str(g.get('label') or '?')[:40]!r} has no usable words or a bad action")
        groups.append({"label": str(g.get("label") or "")[:40], "kq": kq, "action": g["action"]})
    return addr, groups, d.get("default") if d.get("default") in PLAN_ACTIONS else "keep"


def plan_steps(groups, default):
    """A word plan as ordered steps [(label, action, filter criteria, search)]: a group only takes mail no earlier
    group matched, and the default takes what no group matched (nothing, when the default is keep)."""
    steps, earlier = [], []
    for g in groups:
        crit = {"query": g["kq"], **({"negatedQuery": " OR ".join(earlier)} if earlier else {})}
        steps.append((g["label"] or g["action"], g["action"], crit, g["kq"] + "".join(f" -{k}" for k in earlier)))
        earlier.append(g["kq"])
    if default != "keep":
        steps.append(("everything else", default, {"negatedQuery": " OR ".join(earlier)} if earlier else {},
                      "".join(f" -{k}" for k in earlier)))
    return steps


PREVIEW_MAX = int(os.environ.get("PREVIEW_MAX", "20000"))  # a preview counts up to this many per account and step


def preview(d):
    """What a sender action ({addr, action, keep}) or a word plan ({addr, groups, default}) would change, per account
    and in total: counted in Gmail with the very searches the action runs (all folders, all years), not from the
    recent local window. Nothing is changed. A count that reaches PREVIEW_MAX is reported as capped ("or more")."""
    if "groups" in d or "default" in d:
        addr, groups, default = plan_input(d)
        steps = [(label, action, plan_query(addr, action, extra)) for label, action, _, extra in plan_steps(groups, default)]
        destructive = any(action != "keep" for _, action, _ in steps)
    else:
        addr, act = sender_addr(d.get("addr")), d.get("action")
        if act not in ACTIONS or act in ("protect", "unprotect"):
            raise ValueError("bad sender action")
        destructive = act in DESTRUCTIVE
        kq = keep_query(d.get("keep")) if destructive else None
        steps = [(act, act, sender_query(act, addr, kq))] if sender_query(act, addr, kq) else []
        if kq:
            steps.append(("kept and starred", "keep", kept_query(addr, kq)))
    if destructive:
        guard_sender(addr)
    with db() as c:
        acc_ids = [r[0] for r in c.execute("SELECT DISTINCT account_id FROM unified.msg WHERE from_addr = %s",
                                           (addr,)).fetchall()]
    if d.get("accounts"):
        acc_ids = [a for a in acc_ids if a in set(d["accounts"])]

    def count(job):
        a, query = job
        try:
            return len(gmail_list(a, qs(bulk_query(query)), "messages", PREVIEW_MAX))
        except Exception as e:  # noqa: BLE001 - shown per account; the other counts still help
            return str(e)[:200]
    jobs = [(a, query) for _, _, query in steps for a in acc_ids]
    counts = dict(zip(jobs, POOL.map(count, jobs)))
    out = []
    for label, action, query in steps:
        per = [{"id": a, "email": ACC.email(a), **({"count": counts[(a, query)], "capped": counts[(a, query)] >= PREVIEW_MAX}
                                                 if isinstance(counts[(a, query)], int) else {"error": counts[(a, query)]})}
               for a in acc_ids]
        out.append({"what": label, "action": action, "query": bulk_query(query), "accounts": per,
                    "total": sum(x.get("count", 0) for x in per),
                    "capped": any(x.get("capped") for x in per), "failed": any("error" in x for x in per)})
    return {"addr": addr, "accounts": len(acc_ids), "steps": out, "max": MAX_BULK}


def apply_plan(d):
    """Apply a multi-group plan to a sender in every account that gets its mail:
    groups = [{"label", "words": [...], "action": keep|archive|quarantine|delete}] (first matching group wins, by subject),
    default = action for everything that matches no group (keep = leave it alone)."""
    addr, groups, default = plan_input(d)
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
        for acc in ACC.list():  # an older Trash or archive filter would move new mail before the AI ever judged it
            try:
                remove_app_filters(acc["id"], addr)
            except Exception as e:  # noqa: BLE001
                log("cleanup-error", f"apply {addr}: old filters: {e}"[:300], acc["email"])
        with db() as c:
            c.execute("INSERT INTO unified.ai_sort (addr, rule, action) VALUES (%s, %s, %s) ON CONFLICT (addr) DO UPDATE "
                      "SET rule = EXCLUDED.rule, action = EXCLUDED.action, status = 'sorting', done = 0, total = 0, kept = 0, retries = 0",
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
            remove_app_filters(a, addr)  # a re-applied plan replaces the old one's filters (every kind), it doesn't add to them
            notes = []
            for label, action, crit, extra in plan_steps(groups, default):
                notes.append(f"{label}: {_plan_action(a, addr, action, crit, extra, lid)} {action}")
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
