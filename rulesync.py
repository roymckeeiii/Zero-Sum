"""Push the master account's Inbox Zero rules (Rule + Action rows) down to every other linked account.

- Rules are authored in Inbox Zero itself on the master account (MASTER_EMAIL); this only copies them.
- unified.rule_map records which target rule is the copy of which master rule, with a content hash: a copy is only
  rewritten when the master changed, and only copies made here are ever updated or deleted. A target rule with the
  same system type or name that existed before the first sync is adopted (overwritten with the master's version);
  what it was before is saved first, whole, in unified.rule_backup.
- Gmail label ids differ per account: LABEL actions are matched by label name and the label is created when missing.
  Labels are looked up (a Gmail call) before the database transaction on Inbox Zero's tables is opened, never inside.
- Learned sender patterns (Rule.groupId) and deprecated category filters are per-account history: not copied.
- Actions that post to a messaging channel (Slack/Teams/Telegram: DRAFT_/NOTIFY_MESSAGING_CHANNEL, Inbox Zero
  v2.30+) point at the master account's own channel, so they are not copied. A rule whose only actions are of that
  kind is copied with no actions (its conditions still apply).
- Inbox Zero v2.30 ties every Action to its account (Action.emailAccountId): copies carry the target account's id
  when the column exists (izdb.VERSIONED, checked on every sync pass), so the same code runs on v2.25.8 and v2.30.
- Opt-in: a master rule is copied only to the accounts ticked for it (unified.rule_include); untick and its copy
  there is removed. Nothing is shared by default: each address usually gets different mail.
- Removing a copy never deletes a rule the user had written: an adopted rule (rule_map.adopted, or one with an
  "adopted" backup) is put back as it was from unified.rule_backup ("restore"), or, with no backup, left in place and
  no longer managed ("detach"). Only copies this app created are deleted, and only
    - on "Sync now", for the deletes the user confirmed after seeing them (confirm=[op["key"], ...]);
    - by the automatic sync, at most AUTO_MAX_DELETES in total until someone runs "Sync now" (counted across runs in
      unified.settings), and none in a run that would go over; the rest wait ("held") and are logged.
  When the master looks like another account than the last sync ran for (another MASTER_EMAIL, the same e-mail under a
  new Inbox Zero account id, or every master rule the copies came from gone at once), every copy looks orphaned: no
  copy is removed until a "Sync now" confirms the change ("master-change" in confirm).
- A copy is saved in unified.rule_backup before it is deleted, and before an update when it was changed in that
  account since it was synced.
- Actions that send mail or data out of the account (FORWARD, SEND_EMAIL, REPLY, CALL_WEBHOOK, and any action type not
  known to stay inside the account) carry the master's destinations. The Rules tab shows where each one goes, and a
  rule with any of them is copied to an account only after that is allowed for it separately
  (unified.rule_include.outbound). The OK covers the destinations it was given for (outbound_key): when the master
  later sends somewhere else, it is needed again. Without a current OK the rule is reported, not copied, and a copy
  already there that sends anywhere not OK'd (sending was taken back, or the copy predates the OK) has those actions
  removed ("stop-sending"); the copy is backed up first.
- Every backup can be restored from the Rules tab (backups() / restore()); backups of rules still mapped are kept.
"""
import hashlib, json, os, threading

from core import ACC, MASTER, db, label_id, labels, log, new_id, setting
from izdb import ACTION_FIELDS, ENUM, RULE_FIELDS, has_column  # the upstream columns this copies: declared in izdb.py

MASTER_HINT = bool(MASTER)

# per-account action types (they reference the master's own messaging channel): never copied
SKIP_ACTION_TYPES = {"DRAFT_MESSAGING_CHANNEL", "NOTIFY_MESSAGING_CHANNEL"}
# action types known to act only inside the account. Every other type (FORWARD, SEND_EMAIL, REPLY, CALL_WEBHOOK, and
# any type a later Inbox Zero adds) is treated as sending mail or data out: copied only where explicitly allowed
LOCAL_ACTION_TYPES = {"LABEL", "ARCHIVE", "MARK_READ", "MARK_SPAM", "DRAFT_EMAIL", "DIGEST", "MOVE_FOLDER",
                      "TRACK_THREAD"}
OUTBOUND_ACTION_TYPES = {"FORWARD", "SEND_EMAIL", "REPLY", "CALL_WEBHOOK"}   # the known ones, described by name
LOCK = threading.Lock()
# Most copies the automatic sync deletes, in total, until someone runs "Sync now" (not per run); a run that would
# delete more than is left deletes none, and they wait for "Sync now". DEFAULT - Roy may change: 3
AUTO_MAX_DELETES = int(os.environ.get("RULES_AUTO_MAX_DELETES", "3"))
MASTER_KEY = "rules_master"   # unified.settings: the master account's e-mail the last confirmed sync ran for
MASTER_ID_KEY = "rules_master_id"   # and its Inbox Zero account id (re-linking the same e-mail gives a new one)
AUTO_USED_KEY = "rules_auto_deleted"   # copies the automatic sync deleted since someone last ran Sync now


NOW = "now() AT TIME ZONE 'UTC'"   # Inbox Zero (Prisma) keeps "updatedAt" as UTC in a timestamp without time zone


def q(col):
    return f'"{col}"'


def sel(col):
    return f'{q(col)}::text' if col in ENUM else q(col)


def load_rules(c, account_id):
    rules = {}
    for row in c.execute(f'SELECT id, {", ".join(sel(f) for f in RULE_FIELDS)} FROM "Rule" '
                         'WHERE "emailAccountId" = %s ORDER BY "createdAt"', (account_id,)).fetchall():
        rules[row[0]] = {"id": row[0], **dict(zip(RULE_FIELDS, row[1:])), "actions": []}
    if rules:
        for row in c.execute(f'SELECT "ruleId", {", ".join(sel(f) for f in ACTION_FIELDS)}, "labelId" FROM "Action" '
                             'WHERE "ruleId" = ANY(%s) ORDER BY "createdAt", id', (list(rules),)).fetchall():
            rules[row[0]]["actions"].append({**dict(zip(ACTION_FIELDS, row[1:-1])), "labelId": row[-1]})
    return rules


def master_actions(master_id, rule):
    """Master actions with the label name filled in (the name is what carries across accounts)."""
    lab = labels(master_id)
    out = []
    for a in rule["actions"]:
        if a["type"] in SKIP_ACTION_TYPES:
            continue
        a = dict(a)
        if a["type"] == "LABEL":
            a["label"] = lab.get(a["labelId"]) or a["label"]
        a.pop("labelId", None)
        out.append(a)
    return out


def sends(a):
    """Can this action send mail or data out of the account? Unknown types are assumed to."""
    return a["type"] not in LOCAL_ACTION_TYPES and a["type"] not in SKIP_ACTION_TYPES


def outbound(actions):
    """Where a rule's actions send mail or data, one plain line each ([] when nowhere)."""
    out = []
    for a in actions:
        if not sends(a):
            continue
        to = ", ".join(x for x in (a.get("to"), a.get("cc") and f"cc {a['cc']}", a.get("bcc") and f"bcc {a['bcc']}") if x)
        known = {"FORWARD": f"forward to {to or '(no address)'}", "SEND_EMAIL": f"send email to {to or '(no address)'}",
                 "REPLY": "reply to the sender" + (f" ({to})" if to else ""),
                 "CALL_WEBHOOK": f"webhook {a.get('url') or '(no URL)'}"}
        out.append(known.get(a["type"]) or f"{a['type']} action (not known to stay inside the account)"
                   + (f" to {to}" if to else "") + (f" {a['url']}" if a.get("url") else ""))
    return out


def dest_key(actions):
    """A short fingerprint of where the actions send (type and every address / URL); "" when nowhere. An OK to send
    is stored with it, so it stops counting once the destinations change."""
    dests = sorted(json.dumps([a["type"], a.get("to"), a.get("cc"), a.get("bcc"), a.get("url")]) for a in actions
                   if sends(a))
    return hashlib.sha256("\n".join(dests).encode()).hexdigest()[:20] if dests else ""


def _outbound_allowed(c):
    """{(master rule, account): the dest_key the OK was given for (None for an OK given before keys were kept)}."""
    return {(m, a): k for m, a, k in c.execute(
        "SELECT master_rule_id, account_id, outbound_key FROM unified.rule_include WHERE outbound").fetchall()}


def digest(rule, actions):
    body = {f: rule[f] for f in RULE_FIELDS}
    return hashlib.sha256(json.dumps([body, actions], sort_keys=True, default=str).encode()).hexdigest()[:20]


def state():
    """Everything the Rules tab shows: master rules, accounts, per-cell status and which copies are ticked."""
    master = ACC.master()
    if not master:
        return {"error": "MASTER_EMAIL is not set, or that account is not linked in Inbox Zero" if MASTER_HINT else
                "MASTER_EMAIL is not set: rule sync is off", "rules": [], "accounts": []}
    accounts = [a for a in ACC.list() if not a["master"]]
    with db() as c:
        mrules = load_rules(c, master["id"])
        rmap = {(m, a): (t, h, s) for m, a, t, h, s in c.execute(
            "SELECT master_rule_id, account_id, target_rule_id, hash, synced_at FROM unified.rule_map").fetchall()}
        incl = set(c.execute("SELECT master_rule_id, account_id FROM unified.rule_include").fetchall())
        allowed = _outbound_allowed(c)
        targets = {a["id"]: set(r[0] for r in c.execute('SELECT id FROM "Rule" WHERE "emailAccountId" = %s',
                                                         (a["id"],)).fetchall()) for a in accounts}
        logs = c.execute("SELECT at, kind, account, msg FROM unified.log WHERE kind LIKE 'rules%%' "
                         "ORDER BY id DESC LIMIT 40").fetchall()
    rules = []
    for r in mrules.values():
        acts = master_actions(master["id"], r)
        h = digest(r, acts)
        cells = {}
        key = dest_key(acts)
        for a in accounts:
            m = rmap.get((r["id"], a["id"]))
            if (r["id"], a["id"]) not in incl:
                cells[a["id"]] = "removing" if m and m[0] in targets[a["id"]] else "off"
            elif key and allowed.get((r["id"], a["id"]), "") != key:
                cells[a["id"]] = "not sending"   # a copy is not made, or kept, with sending that is not OK'd
            elif not m or m[0] not in targets[a["id"]]:
                cells[a["id"]] = "pending"
            else:
                cells[a["id"]] = "synced" if m[1] == h else "changed"
        rules.append({"id": r["id"], "name": r["name"], "enabled": r["enabled"], "systemType": r["systemType"],
                      "instructions": r["instructions"], "actions": [a["type"] for a in r["actions"]], "cells": cells,
                      "outbound": outbound(acts), "outboundKey": key,
                      "outboundAllowed": {a["id"]: bool(key) and allowed.get((r["id"], a["id"])) == key
                                          for a in accounts},
                      # allowed once, but the rule has sent somewhere else since: needs a new OK
                      "outboundStale": {a["id"]: bool(key) and (r["id"], a["id"]) in allowed and
                                        allowed[(r["id"], a["id"])] != key for a in accounts}})
    return {"master": master, "accounts": accounts, "rules": rules, "auto": setting("rules_auto", "0") == "1",
            "log": [{"at": a.isoformat(), "kind": k, "account": acc, "msg": m} for a, k, acc, m in logs]}


def _current_dest_key(rule_id):
    master = ACC.master()
    if not master:
        raise PermissionError("master account not linked in Inbox Zero")
    with db() as c:
        r = load_rules(c, master["id"]).get(rule_id)
    if not r:
        raise KeyError("no such master rule")
    return dest_key(master_actions(master["id"], r))


def set_include(d):
    """Tick or untick a rule for an account. With "outbound", also allow (or stop allowing) its forward / send /
    reply / webhook actions to be copied there. An OK is stored for the destinations the rule has now; when the page
    sends the key of the destinations it showed ("destinations") and they have changed since, it is refused."""
    if d.get("include") and d.get("outbound"):
        key = _current_dest_key(d["rule"])
        if "destinations" in d and d["destinations"] != key:
            raise ValueError("where this rule sends has changed since the page was loaded: reload and check again")
    with db() as c:
        if d.get("include") and "outbound" in d:
            ok = bool(d["outbound"])
            c.execute("INSERT INTO unified.rule_include (master_rule_id, account_id, outbound, outbound_key) "
                      "VALUES (%s, %s, %s, %s) ON CONFLICT (master_rule_id, account_id) DO UPDATE SET outbound = "
                      "EXCLUDED.outbound, outbound_key = EXCLUDED.outbound_key",
                      (d["rule"], d["account"], ok, key if ok else None))
        elif d.get("include"):
            c.execute("INSERT INTO unified.rule_include (master_rule_id, account_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                      (d["rule"], d["account"]))
        else:
            c.execute("DELETE FROM unified.rule_include WHERE master_rule_id = %s AND account_id = %s",
                      (d["rule"], d["account"]))
    return {"ok": True}


def _backup_rule(c, target_id, account_id, reason, edited_since=None):
    """Save a target rule and its actions, every column as it is now, before it is overwritten. With edited_since (a
    master rule id), only when the rule was changed after its copy was last synced (Rule."updatedAt", which Inbox
    Zero keeps in UTC, against rule_map.synced_at)."""
    since = ""
    if edited_since is not None:
        since = (' AND r."updatedAt" > (SELECT m.synced_at AT TIME ZONE \'UTC\' FROM unified.rule_map m WHERE '
                 'm.master_rule_id = %s AND m.account_id = %s)')
    c.execute('INSERT INTO unified.rule_backup (account_id, rule_id, reason, rule, actions) '
              'SELECT r."emailAccountId", r.id, %s, to_jsonb(r), '
              '(SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a."createdAt", a.id), \'[]\'::jsonb) FROM "Action" a '
              'WHERE a."ruleId" = r.id) FROM "Rule" r WHERE r.id = %s AND r."emailAccountId" = %s' + since,
              (reason, target_id, account_id, *((edited_since, account_id) if since else ())))


def _write_rule(c, target_id, account_id, rule, actions, insert, lids, action_account=None):
    """Write one copy. `lids` maps label names to this account's Gmail label ids, resolved by the caller beforehand:
    no Gmail call happens while the transaction is open. `action_account`: whether "Action" has "emailAccountId"
    (Inbox Zero v2.30+); None asks the database."""
    cols = RULE_FIELDS
    vals = [rule[f] for f in cols]
    casts = [f'%s::"{ENUM[f]}"' if f in ENUM else "%s" for f in cols]
    if insert:
        c.execute(f'INSERT INTO "Rule" (id, "updatedAt", "emailAccountId", {", ".join(q(f) for f in cols)}) '
                  f'VALUES (%s, {NOW}, %s, {", ".join(casts)})', [target_id, account_id, *vals])
    else:
        sets = ", ".join(f"{q(f)} = {cast}" for f, cast in zip(cols, casts))
        c.execute(f'UPDATE "Rule" SET {sets}, "updatedAt" = {NOW} WHERE id = %s AND "emailAccountId" = %s',
                  [*vals, target_id, account_id])
    c.execute('DELETE FROM "Action" WHERE "ruleId" = %s', (target_id,))
    if action_account is None:
        action_account = has_column(c, "Action", "emailAccountId")
    extra = ['"emailAccountId"'] if action_account else []
    cols = ", ".join(['"ruleId"', *extra, '"labelId"', *(q(f) for f in ACTION_FIELDS)])
    acasts = ", ".join(f'%s::"{ENUM[f]}"' if f in ENUM else "%s" for f in ACTION_FIELDS)
    for a in actions:
        if "labelId" in a:   # restoring a backup: the account's own label id, as it was
            lid = a["labelId"]
        else:
            lid = lids[a["label"]] if a["type"] == "LABEL" and a.get("label") else None
        c.execute(f'INSERT INTO "Action" (id, "updatedAt", {cols}) VALUES (%s, {NOW}, %s, {"%s, " * len(extra)}%s, '
                  f"{acasts})", [new_id(), target_id, *([account_id] if extra else []), lid,
                                 *[a[f] for f in ACTION_FIELDS]])


def _setting(c, k):
    row = c.execute("SELECT v FROM unified.settings WHERE k = %s", (k,)).fetchone()
    return row[0] if row else None


def _put(c, k, v):
    c.execute("INSERT INTO unified.settings (k, v) VALUES (%s, %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
              (k, str(v)))


def _master_change(prev, prev_id, master, mapped, mrules):
    """Why the copies may belong to another master than the one linked now (None when they do not):
    another e-mail, the same e-mail under a new Inbox Zero account id (re-linked), or every master rule the copies
    were made from gone at once (rules reset or recreated)."""
    if prev and prev != master["email"].lower():
        return f"MASTER_EMAIL changed from {prev} to {master['email']}"
    if prev_id and prev_id != master["id"]:
        return f"the master account {master['email']} was linked again in Inbox Zero (it has a new account id)"
    if len(mapped) >= 2 and not mapped & set(mrules):
        return (f"none of the {len(mapped)} master rules the copies were made from exist any more (were the master's "
                "rules reset or recreated?)")
    return None


def sync(apply=True, only_account=None, confirm=None, auto=False):
    """Plan (apply=False) or run the sync. Returns {"ops", "applied", "masterChanged"}.
    confirm: the delete keys (op["key"]) the user saw and confirmed, plus "master-change" to accept a new master.
    auto: the background run. It may delete copies without confirmation only while the automatic runs together have
    deleted fewer than AUTO_MAX_DELETES since the last Sync now (unified.settings rules_auto_deleted), and only when
    every delete it plans fits in what is left; otherwise every delete waits for Sync now."""
    master = ACC.master()
    if not master:
        raise PermissionError("master account not linked in Inbox Zero")
    confirm = set(confirm or ())
    with LOCK:
        ops = []
        with db() as c:
            mrules = load_rules(c, master["id"])
            incl = set(c.execute("SELECT master_rule_id, account_id FROM unified.rule_include").fetchall())
            out_ok = _outbound_allowed(c)
            mapped = {m for m, in c.execute("SELECT DISTINCT master_rule_id FROM unified.rule_map").fetchall()}
            prev, prev_id = _setting(c, MASTER_KEY), _setting(c, MASTER_ID_KEY)
            used = int(_setting(c, AUTO_USED_KEY) or 0)
        why = _master_change(prev, prev_id, master, mapped, mrules)
        accounts = [a for a in ACC.list() if not a["master"] and (not only_account or a["id"] == only_account)]
        allow = {"keys": confirm, "budget": 0, "outbound": out_ok, "changed": why,
                 "removals": not why or (not auto and "master-change" in confirm)}
        if auto and apply and allow["removals"]:
            left = max(0, AUTO_MAX_DELETES - used)
            planned = 0
            for acc in accounts:   # look first: a run that would delete more than is left deletes nothing
                try:
                    planned += sum(o["op"] == "delete" for o in _sync_account(master, acc, mrules, incl, False, allow))
                except Exception:  # noqa: BLE001 - reported by the real pass below
                    pass
            allow["budget"] = left if planned <= left else 0
            allow["used"] = used
        budget = allow["budget"]
        for acc in accounts:
            try:
                ops += _sync_account(master, acc, mrules, incl, apply, allow)
            except Exception as e:  # noqa: BLE001 - one account must not stop the others
                ops.append({"op": "error", "account": acc["email"], "rule": "", "detail": str(e)[:300]})
                log("rules-error", str(e)[:500], acc["email"])
        if apply:
            put = []
            if allow["removals"]:   # remember whose rules these copies are, so a later change is noticed
                if prev != master["email"].lower():
                    put.append((MASTER_KEY, master["email"].lower()))
                if prev_id != master["id"]:
                    put.append((MASTER_ID_KEY, master["id"]))
            if auto and budget - allow["budget"]:
                put.append((AUTO_USED_KEY, used + budget - allow["budget"]))
            elif not auto and used and not any(o["op"] == "held" for o in ops):
                put.append((AUTO_USED_KEY, 0))   # someone ran Sync now and nothing is left waiting
            if put:
                with db() as c:
                    for k, v in put:
                        _put(c, k, v)
        return {"ops": ops, "applied": apply,
                "masterChanged": {"from": prev, "to": master["email"], "detail": why} if why else None}


def _adopted_backups(c, account_id):
    """{rule id: (backup id, rule, actions)}: the newest backup saved when each rule was adopted."""
    return {rid: (bid, rule, actions) for bid, rid, rule, actions in c.execute(
        "SELECT DISTINCT ON (rule_id) id, rule_id, rule, actions FROM unified.rule_backup WHERE account_id = %s "
        "AND reason LIKE 'adopted%%' ORDER BY rule_id, id DESC", (account_id,)).fetchall()}


def _clashes(trules, rule_id, name, system_type):
    return [t for t in trules.values() if t["id"] != rule_id and (t["name"] == name or (
        system_type and t["systemType"] == system_type))]


def _restore(c, account_id, rule_id, rule, actions, exists, reason, action_account):
    """Write a backed-up rule back, every copied column and its actions as they were. What is there now is saved
    first (when there is anything)."""
    if exists:
        _backup_rule(c, rule_id, account_id, reason[:200])
    _write_rule(c, rule_id, account_id, {f: rule.get(f) for f in RULE_FIELDS},
                [{**{f: a.get(f) for f in ACTION_FIELDS}, "labelId": a.get("labelId")} for a in actions],
                insert=not exists, lids={}, action_account=action_account)


def _sync_account(master, acc, mrules, incl, apply, allow=None):
    allow = allow or {"keys": set(), "budget": 0, "removals": True, "outbound": {}}
    ops, writes, removals = [], [], []
    with db() as c:  # read only
        trules = load_rules(c, acc["id"])
        rmap = {m: (t, h, bool(ad)) for m, t, h, ad in c.execute(
            "SELECT master_rule_id, target_rule_id, hash, adopted FROM unified.rule_map WHERE account_id = %s",
            (acc["id"],)).fetchall()}
        backups = _adopted_backups(c, acc["id"]) if any(m not in mrules or (m, acc["id"]) not in incl
                                                        for m in rmap) else {}
    mapped_targets = {v[0] for v in rmap.values()}
    for mid, r in mrules.items():
        if (mid, acc["id"]) not in incl:
            continue
        actions = master_actions(master["id"], r)
        h = digest(r, actions)
        cur = rmap.get(mid)
        dest, key = outbound(actions), dest_key(actions)
        ok_key = allow.get("outbound", {}).get((mid, acc["id"]))
        if dest and ok_key != key:
            why = ("sending was allowed for other destinations, or before they were recorded: allow it again for these"
                   if (mid, acc["id"]) in allow.get("outbound", {}) else
                   "not copied until sending is allowed for this account")
            copy = trules.get(cur[0]) if cur else None
            if copy and any(sends(a) for a in copy["actions"]) and dest_key(copy["actions"]) != ok_key:
                # the copy there sends somewhere nobody OK'd (now): take those actions out, keep the rest as it is
                keep = [a for a in copy["actions"] if not sends(a)]
                ops.append({"op": "stop-sending", "account": acc["email"], "rule": r["name"],
                            "detail": "this copy no longer sends: " + "; ".join(outbound(copy["actions"]))
                            + f" (saved in the backups first); {'; '.join(dest)}: {why}"})
                writes.append(("stop-sending", mid, cur[0], copy, keep, digest(copy, keep), cur[2]))
                continue
            ops.append({"op": "outbound", "account": acc["email"], "rule": r["name"],
                        "detail": "; ".join(dest) + ": " + why})
            continue
        if cur and cur[0] in trules:
            if cur[1] == h:
                continue
            op, target = "update", cur[0]
        else:
            # adopt a pre-existing rule with the same system type, else the same name; otherwise create
            cand = [t for t in trules.values() if t["id"] not in mapped_targets and (
                (r["systemType"] and t["systemType"] == r["systemType"]) or t["name"] == r["name"])]
            cand.sort(key=lambda t: t["systemType"] != r["systemType"])
            op, target = ("adopt", cand[0]["id"]) if cand else ("create", new_id())
        # Inbox Zero allows one rule per name (and per system type) in an account: a master rule renamed to the name of
        # another rule there would fail the whole account's transaction, so it is reported and skipped instead
        clash = _clashes(trules, target, r["name"], r["systemType"])
        if clash:
            same = "name" if clash[0]["name"] == r["name"] else "system type"
            ops.append({"op": "error", "account": acc["email"], "rule": r["name"],
                        "detail": f"another rule in this account already has this {same}; rename one of them"})
            continue
        adopted = op == "adopt" or (op == "update" and cur[2])
        ops.append({"op": op, "account": acc["email"], "rule": r["name"],
                    "detail": ", ".join(a["type"] + (f" {a['label']}" if a.get("label") else "") for a in actions)
                    + (f" ({'; '.join(dest)})" if dest else "")})
        writes.append((op, mid, target, r, actions, h, adopted))
        mapped_targets.add(target)
    # copies whose master rule is gone or is no longer ticked for this account
    for mid, (target, _, adopted) in rmap.items():
        if mid in mrules and (mid, acc["id"]) in incl:
            continue
        name = trules.get(target, {}).get("name", target)
        why = "master rule removed" if mid not in mrules else "no longer shared with this account"
        b = backups.get(target)
        if target not in trules:
            op, detail = "detach", f"{why}; the copy is already gone"
        elif adopted or b:   # a rule the user had before the first sync: never deleted
            back = b and _clashes(trules, target, b[1].get("name"), b[1].get("systemType"))
            if b and not back:
                op, detail = "restore", f"{why}; this account's own rule from before the sync is put back"
            else:
                op, detail = "detach", f"{why}; this account's own rule is kept as it is now" + (
                    " (its earlier version clashes with another rule: restore it from the backups list)" if back else "")
        else:
            op, detail = "delete", why
        o = {"op": op, "account": acc["email"], "rule": name, "detail": detail}
        if op == "delete":
            o["key"] = f"{acc['id']}:{target}"
        if apply and not allow["removals"]:
            o = {**o, "op": "held", "detail": f"{op} waits: {allow.get('changed') or 'the master changed'}; "
                                              "confirm with Sync now"}
        elif apply and op == "delete":
            if o["key"] in allow["keys"]:
                pass
            elif allow["budget"] > 0:
                allow["budget"] -= 1
            else:
                o = {**o, "op": "held", "detail": f"delete waits for confirmation (Sync now); {why}" + (
                    f" (the automatic sync deletes at most {AUTO_MAX_DELETES} copies between two Sync now)"
                    if "used" in allow else "")}
        ops.append(o)
        if o["op"] != "held":
            removals.append((o["op"], mid, target, b, why))
    if apply:
        for o in ops:
            if o["op"] == "held":
                log("rules-pending", f"{o['rule']}: {o['detail']}", acc["email"])
    if not apply or not (writes or removals):
        return ops
    # Gmail first, with no transaction open: label ids for every LABEL action about to be written (created if missing)
    lids = {}
    for w in writes:
        for a in w[4]:
            if a["type"] == "LABEL" and a.get("label") and "labelId" not in a and a["label"] not in lids:
                lids[a["label"]] = label_id(acc["id"], a["label"])
    with db() as c:  # then one short transaction on Inbox Zero's tables
        action_account = has_column(c, "Action", "emailAccountId")  # once per pass: an upgrade is seen next sync
        for op, mid, target, r, actions, h, adopted in writes:
            if op == "adopt":  # a rule this app did not make is about to be overwritten: keep what it was
                _backup_rule(c, target, acc["id"], f"adopted as a copy of master rule {r['name']}"[:200])
            elif op == "stop-sending":
                _backup_rule(c, target, acc["id"], "before its sending actions were removed (sending not allowed)")
            elif op == "update":   # a copy someone changed in that account since it was synced: keep their version
                _backup_rule(c, target, acc["id"], "changed in this account since the last sync; overwritten by the "
                             "master's version", edited_since=mid)
            _write_rule(c, target, acc["id"], r, actions, insert=(op == "create"), lids=lids,
                        action_account=action_account)
            c.execute("INSERT INTO unified.rule_map (master_rule_id, account_id, target_rule_id, hash, synced_at, "
                      "adopted) VALUES (%s, %s, %s, %s, now(), %s) ON CONFLICT (master_rule_id, account_id) DO UPDATE "
                      "SET target_rule_id = EXCLUDED.target_rule_id, hash = EXCLUDED.hash, synced_at = now(), "
                      "adopted = EXCLUDED.adopted", (mid, acc["id"], target, h, adopted))
        for op, mid, target, b, why in removals:
            if op == "delete":   # kept in the backups, so it can be put back from the Rules tab
                _backup_rule(c, target, acc["id"], f"deleted: {why}"[:200])
                c.execute('DELETE FROM "Rule" WHERE id = %s AND "emailAccountId" = %s', (target, acc["id"]))
            elif op == "restore":
                _restore(c, acc["id"], target, b[1], b[2], True, f"replaced by its pre-sync version (backup {b[0]})",
                         action_account)
            c.execute("DELETE FROM unified.rule_map WHERE master_rule_id = %s AND account_id = %s", (mid, acc["id"]))
        _prune_backups(c)
        c.commit()
    for o in ops:
        if o["op"] not in ("error", "held", "outbound"):  # stop-sending is logged
            log("rules", f"{o['op']} {o['rule']}" + (" (its previous version is saved in unified.rule_backup)"
                                                     if o["op"] == "adopt" else ""), acc["email"])
    return ops


def _prune_backups(c):
    """Keep the newest 500 backups, and always every backup of a rule that is still mapped (it may be put back)."""
    c.execute("DELETE FROM unified.rule_backup b WHERE b.id NOT IN (SELECT id FROM unified.rule_backup ORDER BY id DESC "
              "LIMIT 500) AND NOT EXISTS (SELECT 1 FROM unified.rule_map m WHERE m.account_id = b.account_id AND "
              "m.target_rule_id = b.rule_id)")


def backups():
    """The saved rules, newest first, for the accounts this app may use (ACCOUNT_OWNERS), never the master's."""
    accs = {a["id"]: a["email"] for a in ACC.list() if not a["master"]}
    with db() as c:
        rows = c.execute("SELECT b.id, b.at, b.account_id, b.rule_id, b.reason, b.rule->>'name', "
                         'EXISTS (SELECT 1 FROM "Rule" r WHERE r.id = b.rule_id AND r."emailAccountId" = b.account_id) '
                         "FROM unified.rule_backup b WHERE b.account_id = ANY(%s) ORDER BY b.id DESC LIMIT 200",
                         (list(accs),)).fetchall()
    return {"backups": [{"id": i, "at": at.isoformat() if hasattr(at, "isoformat") else at, "account": accs[a],
                         "rule": name, "reason": reason, "exists": bool(ex)} for i, at, a, _, reason, name, ex in rows]}


def restore(d):
    """Put one backed-up rule back in its account, as it was when saved. What is there now is saved first; the rule
    is no longer managed by sync (its mapping and tick are removed), so the next sync does not overwrite it again."""
    bid = int(d["id"])
    accs = {a["id"]: a for a in ACC.list() if not a["master"]}
    with LOCK:
        with db() as c:
            row = c.execute("SELECT account_id, rule_id, rule, actions FROM unified.rule_backup WHERE id = %s",
                            (bid,)).fetchone()
        if not row or row[0] not in accs:
            raise KeyError("no such backup")
        account_id, rule_id, rule, actions = row
        with db() as c:
            trules = load_rules(c, account_id)
        clash = _clashes(trules, rule_id, rule.get("name"), rule.get("systemType"))
        if clash:
            raise ValueError(f"another rule in {accs[account_id]['email']} already has this name or system type "
                             f"({clash[0]['name']}): rename it in Inbox Zero first")
        with db() as c:
            _restore(c, account_id, rule_id, rule, actions, rule_id in trules, f"replaced by restoring backup {bid}",
                     has_column(c, "Action", "emailAccountId"))
            for (mid,) in c.execute("DELETE FROM unified.rule_map WHERE account_id = %s AND target_rule_id = %s "
                                    "RETURNING master_rule_id", (account_id, rule_id)).fetchall():
                c.execute("DELETE FROM unified.rule_include WHERE master_rule_id = %s AND account_id = %s",
                          (mid, account_id))
            c.commit()
    log("rules", f"restored {rule.get('name')} from backup {bid}", accs[account_id]["email"])
    return {"ok": True, "rule": rule.get("name"), "account": accs[account_id]["email"]}
