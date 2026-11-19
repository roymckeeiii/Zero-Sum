"""AI sort: a sender's mail judged email by email against a plain-English rule (ai.classify), for the existing mail in
a background pass and for new mail every collector run. One pass per sender; a pass stopped by a restart, the daily
AI limit or a busy provider is picked up again (resume_ai_sorts). State lives in unified.ai_sort."""
import os, re, threading, urllib.error

import psycopg

import ai
from collector import SKIP, qs
from core import ACC, db, gmail_list, label_id, log
from gmailops import MAX_BULK, QUARANTINE, _ACTION, _batch, sender_recent
from guard import frm, is_protected, own_address, sender_matches
from inbox import meta


def stop_automation(key):
    """Drop the AI sorts covered by `key` (an address or "@domain"); a running pass stops before its next batch."""
    with db() as c:
        for (addr,) in c.execute("SELECT addr FROM unified.ai_sort").fetchall():
            if sender_matches(addr, key):
                c.execute("DELETE FROM unified.ai_sort WHERE addr = %s", (addr,))


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
                "SELECT account_id, id, subject, coalesce(snippet, '') FROM unified.msg "
                "WHERE account_id = ANY(%s) AND id = ANY(%s) AND subject IS NOT NULL",
                (sorted({a["id"] for a, _ in chunk}), [mid for _, mid in chunk])).fetchall()}
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
                _batch(acc_id, ids, add=add, remove=rem, sender=addr)
        with db() as c:
            c.execute("UPDATE unified.ai_sort SET done = done + %s, kept = kept + %s, updated_at = now() WHERE addr = %s",
                      (len(chunk), sum(1 for (_, _m), k in zip(metas, verdicts) if k), addr))
    return kept


def _ai_sort_existing(addr, rule, action, token=None):
    _ACTION.note = f"AI sort {addr}"
    try:
        pairs = []
        for a in ACC.list():
            q = f"{frm(addr)} -in:trash -in:spam {SKIP}" + (f" -label:{QUARANTINE}" if action == "quarantine" else "")
            pairs += [(a, m["id"]) for m in gmail_list(a["id"], qs(q), "messages", MAX_BULK)]
        with db() as c:
            c.execute("UPDATE unified.ai_sort SET total = %s, done = 0, kept = 0, status = 'sorting' WHERE addr = %s",
                      (len(pairs), addr))
        kept = _ai_sort_ids(addr, rule, action, pairs, token)
        with db() as c:
            c.execute("UPDATE unified.ai_sort SET status = 'active', retries = 0, last_ts = now(), updated_at = now() "
                      "WHERE addr = %s", (addr,))
        log("ai-sort", f"{addr}: sorted {len(pairs)} existing, kept {kept}, rest {action}")
    except Exception as e:  # noqa: BLE001
        if "a newer pass took over" not in str(e):   # the newer pass owns the row's status now
            with db() as c:
                if transient(e):  # a busy AI provider or Gmail: parked as "retry:", picked up again by resume_ai_sorts
                    c.execute("UPDATE unified.ai_sort SET retries = retries + 1, updated_at = now(), status = CASE "
                              "WHEN retries >= %s THEN %s ELSE %s END WHERE addr = %s",
                              (SORT_RETRIES, f"error: gave up after {SORT_RETRIES} retries: {e}"[:200], f"retry: {e}"[:200], addr))
                else:
                    c.execute("UPDATE unified.ai_sort SET status = %s, updated_at = now() WHERE addr = %s",
                              (f"error: {e}"[:200], addr))
        log("ai-sort-error", f"{addr}: {e}"[:500])
    finally:
        with _SORT_LOCK:
            if token is not None and _SORT_RUNS.get(addr) is token:
                del _SORT_RUNS[addr]


SORT_RETRIES = int(os.environ.get("AI_SORT_RETRIES", "5"))  # tries after a busy AI provider or Gmail, per pass


def transient(e):
    """Is this failure worth another try later: the AI provider or Gmail busy or unreachable, or the database?"""
    if isinstance(e, (ai.AITransient, urllib.error.URLError, TimeoutError, ConnectionError, psycopg.OperationalError)):
        return True
    text = str(e).lower()
    return isinstance(e, RuntimeError) and text.startswith("gmail ") and bool(
        re.search(r": (429|5\d\d) ", text) or "quota exceeded" in text or "ratelimit" in text.replace(" ", ""))


def resume_ai_sorts():
    """Pick up passes that died: the container restarted mid-pass (status still 'sorting', no pass running here), the
    daily AI limit stopped one (retried from the next UTC day), or the AI provider or Gmail was busy (status 'retry:',
    tried again after 15 minutes, then 30, ... up to AI_SORT_RETRIES times). Before, such a sender stayed half-sorted
    and its new mail was never sorted either, because only 'active' sorts are served."""
    with db() as c:
        rows = c.execute("SELECT addr, rule, action FROM unified.ai_sort WHERE "
                         "(status = 'sorting' AND updated_at < now() - interval '10 minutes') OR "
                         "(status LIKE 'error: daily AI limit%%' AND (updated_at AT TIME ZONE 'utc')::date < (now() AT TIME ZONE 'utc')::date) OR "
                         "(status LIKE 'retry:%%' AND updated_at < now() - make_interval(mins => 15 * greatest(retries, 1)))"
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
                pairs += [(a, m["id"]) for m in gmail_list(a["id"], qs(f"{frm(addr)} in:inbox after:{since}"), "messages", 500)]
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


def classify_preview(d):
    """Tag the 20 most recent emails of a sender with the rule, for the plan preview."""
    items = sender_recent({"addr": [str(d.get("addr") or "")], "n": ["20"]})["items"]
    verdicts = ai.classify(d.get("rule"), [(it["subject"], it["snippet"]) for it in items])
    return {"verdicts": {it["id"]: v for it, v in zip(items, verdicts)}}
