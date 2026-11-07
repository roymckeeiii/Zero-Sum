"""Merged inbox: newest inbox mail across every linked account, read, and thread-level triage (no sending)."""
import base64, email.utils, re, time, urllib.parse

from core import ACC, POOL, db, gmail, header, labels

_meta = {}  # (account id, message id) -> (time, item)


def meta(account, mid):
    k = (account["id"], mid)
    t, item = _meta.get(k, (0, None))
    if item and time.time() - t < 120:
        return item
    m = gmail(account["id"], f"/messages/{mid}?format=metadata&metadataHeaders=From&metadataHeaders=Subject")
    hs = m.get("payload", {}).get("headers", [])
    name, addr = email.utils.parseaddr(header(hs, "from"))
    lab = labels(account["id"])
    ids = m.get("labelIds", [])
    item = {"account": account["id"], "email": account["email"], "id": m["id"], "threadId": m["threadId"],
            "from": name or addr, "fromAddr": addr, "subject": header(hs, "subject") or "(no subject)",
            "snippet": m.get("snippet", ""), "ts": int(m.get("internalDate", 0)) // 1000,
            "unread": "UNREAD" in ids, "labels": sorted(lab[x] for x in ids if x in lab)}
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
    gq = " ".join(x for x in (search, "is:unread" if q.get("unread", ["0"])[0] == "1" else "") if x)
    accs = [a for a in ACC.list() if not only or a["id"] == only]
    errors = []

    def ids(a):
        p = f"/messages?maxResults={limit}&labelIds=INBOX" + (f"&q={urllib.parse.quote(gq)}" if gq else "")
        try:
            return [(a, m["id"]) for m in gmail(a["id"], p).get("messages", [])]
        except Exception as e:  # noqa: BLE001
            errors.append({"email": a["email"], "error": str(e)[:200]})
            return []

    def safe_meta(p):
        try:
            return meta(*p)
        except Exception:  # noqa: BLE001 - message vanished between list and get
            return None
    pairs = [p for lst in POOL.map(ids, accs) for p in lst]
    items = sorted((i for i in POOL.map(safe_meta, pairs) if i), key=lambda i: -i["ts"])
    rules = rules_for(items)
    for it in items:
        it["rules"] = rules.get((it["account"], it["id"]), [])
    return {"items": items, "errors": errors}


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
           "read": {"removeLabelIds": ["UNREAD"]}, "unread": {"addLabelIds": ["UNREAD"]}}


def action(d):
    if d.get("action") not in ACTIONS or not re.fullmatch(r"[0-9a-f]{6,32}", d.get("threadId", "")):
        raise ValueError("bad action")
    ACC.row(d["account"])
    gmail(d["account"], f"/threads/{d['threadId']}/modify", "POST", ACTIONS[d["action"]])
    for k in [k for k, (_, it) in list(_meta.items()) if it["threadId"] == d["threadId"] and k[0] == d["account"]]:
        _meta.pop(k, None)
    return {"ok": True}
