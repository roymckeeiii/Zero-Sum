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
    - by the automatic sync, at most AUTO_MAX_DELETES per run; the rest wait ("held") and are logged.
  When MASTER_EMAIL names another account than the last sync ran for, every copy looks orphaned: no copy is removed
  until a "Sync now" confirms the change ("master-change" in confirm).
- Every backup can be restored from the Rules tab (backups() / restore()); backups of rules still mapped are kept.
"""
import hashlib, json, os, threading

from core import ACC, MASTER, db, label_id, labels, log, new_id, setting
from izdb import ACTION_FIELDS, ENUM, RULE_FIELDS, has_column  # the upstream columns this copies: declared in izdb.py

MASTER_HINT = bool(MASTER)

# per-account action types (they reference the master's own messaging channel): never copied
SKIP_ACTION_TYPES = {"DRAFT_MESSAGING_CHANNEL", "NOTIFY_MESSAGING_CHANNEL"}
LOCK = threading.Lock()
# Most copies the automatic sync deletes in one run; more wait for "Sync now". DEFAULT - Roy may change: 3
AUTO_MAX_DELETES = int(os.environ.get("RULES_AUTO_MAX_DELETES", "3"))
MASTER_KEY = "rules_master"   # unified.settings: the master account's e-mail the last confirmed sync ran for


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
        targets = {a["id"]: set(r[0] for r in c.execute('SELECT id FROM "Rule" WHERE "emailAccountId" = %s',
                                                         (a["id"],)).fetchall()) for a in accounts}
        logs = c.execute("SELECT at, kind, account, msg FROM unified.log WHERE kind LIKE 'rules%%' "
                         "ORDER BY id DESC LIMIT 40").fetchall()
    rules = []
    for r in mrules.values():
        h = digest(r, master_actions(master["id"], r))
        cells = {}
        for a in accounts:
            m = rmap.get((r["id"], a["id"]))
            if (r["id"], a["id"]) not in incl:
                cells[a["id"]] = "removing" if m and m[0] in targets[a["id"]] else "off"
            elif not m or m[0] not in targets[a["id"]]:
                cells[a["id"]] = "pending"
            else:
                cells[a["id"]] = "synced" if m[1] == h else "changed"
        rules.append({"id": r["id"], "name": r["name"], "enabled": r["enabled"], "systemType": r["systemType"],
                      "instructions": r["instructions"], "actions": [a["type"] for a in r["actions"]], "cells": cells})
    return {"master": master, "accounts": accounts, "rules": rules, "auto": setting("rules_auto", "0") == "1",
            "log": [{"at": a.isoformat(), "kind": k, "account": acc, "msg": m} for a, k, acc, m in logs]}


def set_include(d):
    with db() as c:
        if d.get("include"):
            c.execute("INSERT INTO unified.rule_include VALUES (%s, %s) ON CONFLICT DO NOTHING",
                      (d["rule"], d["account"]))
        else:
            c.execute("DELETE FROM unified.rule_include WHERE master_rule_id = %s AND account_id = %s",
                      (d["rule"], d["account"]))
    return {"ok": True}


def _backup_rule(c, target_id, account_id, reason):
    """Save a target rule and its actions, every column as it is now, before it is overwritten."""
    c.execute('INSERT INTO unified.rule_backup (account_id, rule_id, reason, rule, actions) '
              'SELECT r."emailAccountId", r.id, %s, to_jsonb(r), '
              '(SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a."createdAt", a.id), \'[]\'::jsonb) FROM "Action" a '
              'WHERE a."ruleId" = r.id) FROM "Rule" r WHERE r.id = %s AND r."emailAccountId" = %s',
              (reason, target_id, account_id))


def _write_rule(c, target_id, account_id, rule, actions, insert, lids, action_account=None):
    """Write one copy. `lids` maps label names to this account's Gmail label ids, resolved by the caller beforehand:
    no Gmail call happens while the transaction is open. `action_account`: whether "Action" has "emailAccountId"
    (Inbox Zero v2.30+); None asks the database."""
    cols = RULE_FIELDS
    vals = [rule[f] for f in cols]
    casts = [f'%s::"{ENUM[f]}"' if f in ENUM else "%s" for f in cols]
    if insert:
        c.execute(f'INSERT INTO "Rule" (id, "updatedAt", "emailAccountId", {", ".join(q(f) for f in cols)}) '
                  f'VALUES (%s, now(), %s, {", ".join(casts)})', [target_id, account_id, *vals])
    else:
        sets = ", ".join(f"{q(f)} = {cast}" for f, cast in zip(cols, casts))
        c.execute(f'UPDATE "Rule" SET {sets}, "updatedAt" = now() WHERE id = %s AND "emailAccountId" = %s',
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
        c.execute(f'INSERT INTO "Action" (id, "updatedAt", {cols}) VALUES (%s, now(), %s, {"%s, " * len(extra)}%s, '
                  f"{acasts})", [new_id(), target_id, *([account_id] if extra else []), lid,
                                 *[a[f] for f in ACTION_FIELDS]])


def sync(apply=True, only_account=None, confirm=None, auto=False):
    """Plan (apply=False) or run the sync. Returns {"ops", "applied", "masterChanged"}.
    confirm: the delete keys (op["key"]) the user saw and confirmed, plus "master-change" to accept a new master.
    auto: the background run, which may delete up to AUTO_MAX_DELETES copies without confirmation."""
    master = ACC.master()
    if not master:
        raise PermissionError("master account not linked in Inbox Zero")
    confirm = set(confirm or ())
    with LOCK:
        ops = []
        with db() as c:
            mrules = load_rules(c, master["id"])
            incl = set(c.execute("SELECT master_rule_id, account_id FROM unified.rule_include").fetchall())
            row = c.execute("SELECT v FROM unified.settings WHERE k = %s", (MASTER_KEY,)).fetchone()
        prev = row[0] if row else None
        changed = bool(prev) and prev != master["email"].lower()
        allow = {"keys": confirm, "budget": AUTO_MAX_DELETES if auto else 0,
                 "removals": not changed or (not auto and "master-change" in confirm)}
        for acc in [a for a in ACC.list() if not a["master"] and (not only_account or a["id"] == only_account)]:
            try:
                ops += _sync_account(master, acc, mrules, incl, apply, allow)
            except Exception as e:  # noqa: BLE001 - one account must not stop the others
                ops.append({"op": "error", "account": acc["email"], "rule": "", "detail": str(e)[:300]})
                log("rules-error", str(e)[:500], acc["email"])
        if apply and allow["removals"] and prev != master["email"].lower():
            with db() as c:   # remember whose rules these copies are, so a later MASTER_EMAIL change is noticed
                c.execute("INSERT INTO unified.settings (k, v) VALUES (%s, %s) ON CONFLICT (k) DO UPDATE SET v = "
                          "EXCLUDED.v", (MASTER_KEY, master["email"].lower()))
        return {"ops": ops, "applied": apply, "masterChanged": {"from": prev, "to": master["email"]} if changed else None}


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
    allow = allow or {"keys": set(), "budget": 0, "removals": True}
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
                    "detail": ", ".join(a["type"] + (f" {a['label']}" if a.get("label") else "") for a in actions)})
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
            o = {**o, "op": "held", "detail": f"{op} waits: MASTER_EMAIL changed since the last sync; confirm with Sync now"}
        elif apply and op == "delete":
            if o["key"] in allow["keys"]:
                pass
            elif allow["budget"] > 0:
                allow["budget"] -= 1
            else:
                o = {**o, "op": "held", "detail": f"delete waits for confirmation (Sync now); {why}"}
        ops.append(o)
        if o["op"] != "held":
            removals.append((o["op"], mid, target, b))
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
            if a["type"] == "LABEL" and a.get("label") and a["label"] not in lids:
                lids[a["label"]] = label_id(acc["id"], a["label"])
    with db() as c:  # then one short transaction on Inbox Zero's tables
        action_account = has_column(c, "Action", "emailAccountId")  # once per pass: an upgrade is seen next sync
        for op, mid, target, r, actions, h, adopted in writes:
            if op == "adopt":  # a rule this app did not make is about to be overwritten: keep what it was
                _backup_rule(c, target, acc["id"], f"adopted as a copy of master rule {r['name']}"[:200])
            _write_rule(c, target, acc["id"], r, actions, insert=(op == "create"), lids=lids,
                        action_account=action_account)
            c.execute("INSERT INTO unified.rule_map (master_rule_id, account_id, target_rule_id, hash, synced_at, "
                      "adopted) VALUES (%s, %s, %s, %s, now(), %s) ON CONFLICT (master_rule_id, account_id) DO UPDATE "
                      "SET target_rule_id = EXCLUDED.target_rule_id, hash = EXCLUDED.hash, synced_at = now(), "
                      "adopted = EXCLUDED.adopted", (mid, acc["id"], target, h, adopted))
        for op, mid, target, b in removals:
            if op == "delete":
                c.execute('DELETE FROM "Rule" WHERE id = %s AND "emailAccountId" = %s', (target, acc["id"]))
            elif op == "restore":
                _restore(c, acc["id"], target, b[1], b[2], True, f"replaced by its pre-sync version (backup {b[0]})",
                         action_account)
            c.execute("DELETE FROM unified.rule_map WHERE master_rule_id = %s AND account_id = %s", (mid, acc["id"]))
        _prune_backups(c)
        c.commit()
    for o in ops:
        if o["op"] not in ("error", "held"):
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
