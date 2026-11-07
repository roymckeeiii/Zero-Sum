"""Push the master account's Inbox Zero rules (Rule + Action rows) down to every other linked account.

- Rules are authored in Inbox Zero itself on the master account (MASTER_EMAIL); this only copies them.
- unified.rule_map records which target rule is the copy of which master rule, with a content hash: a copy is only
  rewritten when the master changed, and only copies made here are ever updated or deleted. A target rule with the
  same system type or name that existed before the first sync is adopted (overwritten with the master's version).
- Gmail label ids differ per account: LABEL actions are matched by label name and the label is created when missing.
- Learned sender patterns (Rule.groupId) and deprecated category filters are per-account history: not copied.
- Opt-in: a master rule is copied only to the accounts ticked for it (unified.rule_include); untick and its copy
  there is deleted. Nothing is shared by default: each address usually gets different mail.
"""
import hashlib, json, threading

from core import ACC, MASTER, db, label_id, labels, log, new_id, setting

MASTER_HINT = bool(MASTER)

RULE_FIELDS = ["name", "instructions", "automate", "runOnThreads", "body", "from", "subject", "to", "enabled",
               "conditionalOperator", "systemType", "promptText"]
ENUM = {"conditionalOperator": "LogicalOperator", "systemType": "SystemType", "type": "ActionType"}
ACTION_FIELDS = ["type", "label", "subject", "content", "to", "cc", "bcc", "url", "delayInMinutes", "folderName",
                 "folderId"]
LOCK = threading.Lock()


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


def _write_rule(c, target_id, account_id, rule, actions, insert):
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
    acasts = ", ".join(f'%s::"{ENUM[f]}"' if f in ENUM else "%s" for f in ACTION_FIELDS)
    for a in actions:
        lid = label_id(account_id, a["label"]) if a["type"] == "LABEL" and a.get("label") else None
        c.execute(f'INSERT INTO "Action" (id, "updatedAt", "ruleId", "labelId", {", ".join(q(f) for f in ACTION_FIELDS)}) '
                  f"VALUES (%s, now(), %s, %s, {acasts})", [new_id(), target_id, lid, *[a[f] for f in ACTION_FIELDS]])


def sync(apply=True, only_account=None):
    """Plan (apply=False) or run the sync. Returns the list of operations."""
    master = ACC.master()
    if not master:
        raise PermissionError("master account not linked in Inbox Zero")
    with LOCK:
        ops = []
        with db() as c:
            mrules = load_rules(c, master["id"])
            incl = set(c.execute("SELECT master_rule_id, account_id FROM unified.rule_include").fetchall())
        for acc in [a for a in ACC.list() if not a["master"] and (not only_account or a["id"] == only_account)]:
            try:
                ops += _sync_account(master, acc, mrules, incl, apply)
            except Exception as e:  # noqa: BLE001 - one account must not stop the others
                ops.append({"op": "error", "account": acc["email"], "rule": "", "detail": str(e)[:300]})
                log("rules-error", str(e)[:500], acc["email"])
        return {"ops": ops, "applied": apply}


def _sync_account(master, acc, mrules, incl, apply):
    ops = []
    with db() as c:
        trules = load_rules(c, acc["id"])
        rmap = {m: (t, h) for m, t, h in c.execute(
            "SELECT master_rule_id, target_rule_id, hash FROM unified.rule_map WHERE account_id = %s",
            (acc["id"],)).fetchall()}
        mapped_targets = {t for t, _ in rmap.values()}
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
                clash = [t for t in trules.values() if t["name"] == r["name"] and t["id"] != target]
                if clash:
                    ops.append({"op": "error", "account": acc["email"], "rule": r["name"],
                                "detail": "another rule in this account already has this name; rename one of them"})
                    continue
            ops.append({"op": op, "account": acc["email"], "rule": r["name"],
                        "detail": ", ".join(a["type"] + (f" {a['label']}" if a.get("label") else "") for a in actions)})
            if apply:
                _write_rule(c, target, acc["id"], r, actions, insert=(op == "create"))
                c.execute("INSERT INTO unified.rule_map VALUES (%s, %s, %s, %s, now()) ON CONFLICT (master_rule_id, "
                          "account_id) DO UPDATE SET target_rule_id = EXCLUDED.target_rule_id, hash = EXCLUDED.hash, "
                          "synced_at = now()", (mid, acc["id"], target, h))
                mapped_targets.add(target)
        # copies whose master rule is gone or is no longer ticked for this account
        for mid, (target, _) in rmap.items():
            if mid in mrules and (mid, acc["id"]) in incl:
                continue
            name = trules.get(target, {}).get("name", target)
            ops.append({"op": "delete", "account": acc["email"], "rule": name,
                        "detail": "master rule removed" if mid not in mrules else "no longer shared with this account"})
            if apply:
                c.execute('DELETE FROM "Rule" WHERE id = %s AND "emailAccountId" = %s', (target, acc["id"]))
                c.execute("DELETE FROM unified.rule_map WHERE master_rule_id = %s AND account_id = %s", (mid, acc["id"]))
        if apply:
            c.commit()
    if apply:
        for o in ops:
            if o["op"] != "error":
                log("rules", f"{o['op']} {o['rule']}", acc["email"])
    return ops
