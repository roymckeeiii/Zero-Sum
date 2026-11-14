"""Merged inbox: newest inbox mail across every linked account, read, and thread-level triage (no sending).

Search (RFC-001853): /api/messages takes scope=inbox (default, today's behaviour: INBOX only), all (every folder
but Spam/Trash: Inbox, Sent, archived, labels), sent, spamtrash, or label (with label=<name>). Scopes other than
inbox drop the INBOX label filter and add the scope's Gmail search term to the user's query, which passes through
unchanged (from:, to:, has:attachment, before:, older_than: ...). Every account is searched in parallel; results
merge newest first. Paging: a search page is capped at SEARCH_PAGE_MAX results; each account gets
limit // accounts of them (at least 1), so a page is at most `limit` messages, from one messages.list call per
account plus one metadata get per message. Every fetched message is returned (nothing is cut after the fetch), and
`next` carries each account's nextPageToken, so "load more" continues every account where it stopped. An account
that fails shows in `errors` and the others still answer."""
import base64, binascii, email.utils, json, re, time, urllib.parse

from core import ACC, POOL, db, gmail, header, labels

_meta = {}  # (account id, message id) -> (time, item)


SCOPES = ("inbox", "all", "sent", "spamtrash", "label")
SEARCH_PAGE_MAX = 50  # results per search page, all accounts together


def folder_of(label_ids):
    """Where a message lives, for search results: Trash/Spam win, then Inbox, Sent, Drafts, else Archived."""
    for sys_id, name in (("TRASH", "Trash"), ("SPAM", "Spam"), ("INBOX", "Inbox"), ("SENT", "Sent"), ("DRAFT", "Drafts")):
        if sys_id in label_ids:
            return name
    return "Archived"


def label_term(name):
    """Gmail's search form of a label name: lower case, spaces and slashes become dashes (label:my-label)."""
    t = re.sub(r"[\s/]+", "-", name.strip().lower())
    t = re.sub(r"[^\w&.-]", "", t)
    if not t:
        raise ValueError("bad label")
    return f"label:{t}"


def scope_query(scope, label=""):
    """(extra Gmail q term, URL params) for a search scope; inbox keeps the INBOX label filter it always had."""
    if scope == "inbox":
        return "", "&labelIds=INBOX"
    if scope == "all":
        return "", ""  # messages.list without labelIds already skips Spam and Trash
    if scope == "sent":
        return "in:sent", ""
    if scope == "spamtrash":
        return "{in:spam in:trash}", "&includeSpamTrash=true"
    if scope == "label":
        return label_term(label), ""
    raise ValueError("bad scope")


def encode_page(tokens):
    return base64.urlsafe_b64encode(json.dumps(tokens, sort_keys=True).encode()).decode() if tokens else ""


def decode_page(page):
    if not page:
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(page.encode()))
    except (binascii.Error, ValueError):
        raise ValueError("bad page") from None
    ok = isinstance(d, dict) and all(
        isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("t", ""), str)
        and isinstance(v.get("b", []), list) and all(isinstance(x, str) for x in v.get("b", [])) for k, v in d.items())
    if not ok:
        raise ValueError("bad page")
    return {k: {"t": v.get("t", ""), "b": list(v.get("b", []))} for k, v in d.items()}


def recipients(to_header):
    """Display form of a To header: names (or addresses), comma-separated, at most 3 then "+N"."""
    people = [n or a for n, a in email.utils.getaddresses([to_header or ""]) if n or a]
    return ", ".join(people[:3]) + (f" +{len(people) - 3}" if len(people) > 3 else "")


def meta(account, mid):
    k = (account["id"], mid)
    t, item = _meta.get(k, (0, None))
    if item and time.time() - t < 120:
        return item
    m = gmail(account["id"], f"/messages/{mid}?format=metadata&metadataHeaders=From&metadataHeaders=To&metadataHeaders=Subject")
    hs = m.get("payload", {}).get("headers", [])
    name, addr = email.utils.parseaddr(header(hs, "from"))
    lab = labels(account["id"])
    ids = m.get("labelIds", [])
    item = {"account": account["id"], "email": account["email"], "id": m["id"], "threadId": m["threadId"],
            "from": name or addr, "fromAddr": addr, "to": recipients(header(hs, "to")), "subject": header(hs, "subject") or "(no subject)",
            "snippet": m.get("snippet", ""), "ts": int(m.get("internalDate", 0)) // 1000,
            "unread": "UNREAD" in ids, "starred": "STARRED" in ids, "folder": folder_of(ids),
            "labels": sorted(lab[x] for x in ids if x in lab)}
    _meta[k] = (time.time(), item)
    if len(_meta) > 20000:
        _meta.clear()
    return item


def rules_for(items):
    by_acc = {}
    for it in items:
        by_acc.setdefault(it["account"], []).append(it["id"])
    out = {}
    if not by_acc:
        return out
    with db() as c:
        for acc, ids in by_acc.items():
            for mid, name, status in c.execute(
                    'SELECT er."messageId", r.name, er.status::text FROM "ExecutedRule" er '
                    'LEFT JOIN "Rule" r ON r.id = er."ruleId" '
                    'WHERE er."emailAccountId" = %s AND er."messageId" = ANY(%s)', (acc, ids)).fetchall():
                if name:
                    out.setdefault((acc, mid), []).append({"name": name, "status": status})
    return out


def accounts():
    def one(a):
        try:
            return {**a, "unread": gmail(a["id"], "/labels/INBOX").get("threadsUnread", 0), "ok": True}
        except Exception as e:  # noqa: BLE001 - shown per account in the UI
            return {**a, "unread": 0, "ok": False, "error": str(e)[:200]}
    return list(POOL.map(one, ACC.list()))


def messages(q):
    limit = max(10, min(int(q.get("limit", ["30"])[0]), 200))
    only = q.get("account", [""])[0]
    search = q.get("q", [""])[0].strip()
    scope = q.get("scope", ["inbox"])[0] or "inbox"
    term, params = scope_query(scope, q.get("label", [""])[0])
    page = decode_page(q.get("page", [""])[0])
    gq = " ".join(x for x in (search, term, "is:unread" if q.get("unread", ["0"])[0] == "1" else "") if x)
    accs = [a for a in ACC.list() if not only or a["id"] == only]
    if page is not None:  # "load more": only the accounts that still had results, from where each stopped
        accs = [a for a in accs if a["id"] in page]
    if scope == "inbox" and page is None:
        per = limit  # unchanged inbox paging: the UI asks for a bigger limit
    else:
        limit = min(limit, SEARCH_PAGE_MAX)
        per = max(1, limit // max(1, len(accs)))  # per-account pages add up to at most `limit`
    errors, nxt = [], {}
    paged = not (scope == "inbox" and page is None)
    state = page or {}

    def ids(a):
        """This account's candidates, newest first: ids held back from the last page, then a fresh page from Gmail
        when fewer than `per` are held back. Returns (pairs, gmail token or "", has_more)."""
        st = state.get(a["id"], {"t": "", "b": []})
        held, token = list(st["b"]), st["t"]
        if paged and len(held) >= per:
            return [(a, m) for m in held], token, bool(token)
        if page is not None and not token:
            return [(a, m) for m in held], "", False
        p = f"/messages?maxResults={per}{params}" + (f"&q={urllib.parse.quote(gq)}" if gq else "")
        if token:
            p += f"&pageToken={urllib.parse.quote(token)}"
        try:
            d = gmail(a["id"], p)
        except Exception as e:  # noqa: BLE001
            errors.append({"email": a["email"], "error": str(e)[:200]})
            return [(a, m) for m in held], "", False
        new_token = d.get("nextPageToken") or ""
        return [(a, m) for m in held] + [(a, m["id"]) for m in d.get("messages", [])], new_token, bool(new_token)

    def safe_meta(p):
        try:
            return meta(*p)
        except Exception:  # noqa: BLE001 - message vanished between list and get
            return None
    fetched = list(POOL.map(ids, accs))
    pairs = [p for lst, _, _ in fetched for p in lst]
    items = sorted((i for i in POOL.map(safe_meta, pairs) if i), key=lambda i: -i["ts"])
    if paged:
        # Global order across accounts and pages (RFC-001885 final review): an account that has more mail in Gmail
        # may still hold messages newer than another account's oldest candidate, so nothing older than the newest
        # such boundary is shown yet; the rest is held back in the page token and shown, in order, on "load more".
        by_acc = {}
        for it in items:
            by_acc.setdefault(it["account"], []).append(it)
        cutoff = max((by_acc[a["id"]][-1]["ts"] for (_, _, more), a in zip(fetched, accs)
                      if more and by_acc.get(a["id"])), default=None)
        shown = [it for it in items if cutoff is None or it["ts"] >= cutoff][:limit]
        ids_shown = {(it["account"], it["id"]) for it in shown}
        for (_, token, more), a in zip(fetched, accs):
            held = [it["id"] for it in by_acc.get(a["id"], []) if (a["id"], it["id"]) not in ids_shown]
            if held or token:
                nxt[a["id"]] = {"t": token, "b": held}
        items = shown
    rules = rules_for(items)
    from cleanup import is_protected, protected_set
    prot = protected_set()
    for it in items:
        it["rules"] = rules.get((it["account"], it["id"]), [])
        it["protected"] = is_protected(it["fromAddr"].lower(), prot)
    return {"items": items, "errors": errors, "scope": scope, "next": encode_page(nxt)}


def body_of(payload):
    found = {}

    def walk(p):
        mt, data = p.get("mimeType", ""), p.get("body", {}).get("data")
        if data and mt in ("text/html", "text/plain") and mt not in found:
            found[mt] = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
        for c in p.get("parts", []) or []:
            walk(c)
    walk(payload)
    if "text/html" in found:
        return "html", found["text/html"]
    return "text", found.get("text/plain", "")


def message(q):
    acc, mid = q["account"][0], q["id"][0]
    if not re.fullmatch(r"[0-9a-f]{6,32}", mid):
        raise ValueError("bad id")
    m = gmail(acc, f"/messages/{mid}?format=full")
    hs = m.get("payload", {}).get("headers", [])
    kind, body = body_of(m.get("payload", {}))
    return {"id": m["id"], "threadId": m["threadId"], "kind": kind, "body": body, "from": header(hs, "from"),
            "to": header(hs, "to"), "cc": header(hs, "cc"), "subject": header(hs, "subject"),
            "date": header(hs, "date"), "email": ACC.email(acc)}


ACTIONS = {"archive": {"removeLabelIds": ["INBOX"]}, "inbox": {"addLabelIds": ["INBOX"]},
           "read": {"removeLabelIds": ["UNREAD"]}, "unread": {"addLabelIds": ["UNREAD"]},
           "star": {"addLabelIds": ["STARRED"]}, "unstar": {"removeLabelIds": ["STARRED"]},
           "delete": None, "undelete": None}


def action(d):
    if d.get("action") not in ACTIONS or not re.fullmatch(r"[0-9a-f]{6,32}", d.get("threadId", "")):
        raise ValueError("bad action")
    ACC.row(d["account"])
    if d["action"] == "delete":  # Trash (Gmail empties it after 30 days); gmail.modify cannot delete permanently
        gmail(d["account"], f"/threads/{d['threadId']}/trash", "POST")
    elif d["action"] == "undelete":
        gmail(d["account"], f"/threads/{d['threadId']}/untrash", "POST")
    elif d["action"] in ("star", "unstar") and re.fullmatch(r"[0-9a-f]{6,32}", d.get("id", "")):
        gmail(d["account"], f"/messages/{d['id']}/modify", "POST", ACTIONS[d["action"]])  # Gmail stars one message
    else:
        gmail(d["account"], f"/threads/{d['threadId']}/modify", "POST", ACTIONS[d["action"]])
    for k in [k for k, (_, it) in list(_meta.items()) if it["threadId"] == d["threadId"] and k[0] == d["account"]]:
        _meta.pop(k, None)
    return {"ok": True}
