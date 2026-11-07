"""Merged analytics across every linked account: mail volume, read rate, top senders and domains (from the collector's
unified.msg), what Inbox Zero's rules did (ExecutedRule, grouped by rule name, which the rule sync keeps identical
across accounts), and cleanup totals (Newsletter statuses). Every number comes with a per-account breakdown."""
from core import ACC, POOL, db, gmail


def overview(q):
    days = max(1, min(int(q.get("days", ["30"])[0]), 180))
    accs = {a["id"]: a["email"] for a in ACC.list()}
    with db() as c:
        daily = c.execute(
            "SELECT to_char(date_trunc('day', ts), 'YYYY-MM-DD'), account_id, count(*) FROM unified.msg "
            "WHERE ts > now() - make_interval(days => %s) GROUP BY 1, 2 ORDER BY 1", (days,)).fetchall()
        per = c.execute(
            "SELECT account_id, count(*), sum(unread::int), sum(inbox::int), count(DISTINCT from_addr) "
            "FROM unified.msg WHERE ts > now() - make_interval(days => %s) GROUP BY 1", (days,)).fetchall()
        top_senders = c.execute(
            "SELECT from_addr, max(from_name), count(*), sum(unread::int), count(DISTINCT account_id) FROM unified.msg "
            "WHERE ts > now() - make_interval(days => %s) GROUP BY 1 ORDER BY 3 DESC LIMIT 15", (days,)).fetchall()
        top_domains = c.execute(
            "SELECT domain, count(*), sum(unread::int), count(DISTINCT from_addr) FROM unified.msg "
            "WHERE ts > now() - make_interval(days => %s) GROUP BY 1 ORDER BY 2 DESC LIMIT 15", (days,)).fetchall()
        rules = c.execute(
            'SELECT coalesce(r.name, \'(deleted rule)\'), er."emailAccountId", count(*) FROM "ExecutedRule" er '
            'LEFT JOIN "Rule" r ON r.id = er."ruleId" WHERE er."createdAt" > now() - make_interval(days => %s) '
            "AND er.status::text = 'APPLIED' GROUP BY 1, 2", (days,)).fetchall()
        cleanup = c.execute('SELECT status::text, "emailAccountId", count(*) FROM "Newsletter" '
                            "WHERE status IS NOT NULL GROUP BY 1, 2").fetchall()
        n_senders = c.execute("SELECT count(DISTINCT from_addr) FROM unified.msg "
                              "WHERE ts > now() - make_interval(days => %s)", (days,)).fetchone()[0]
        collect = c.execute("SELECT count(*) FILTER (WHERE backfill_done), count(*), min(oldest) "
                            "FROM unified.collect_state").fetchone()

    def inbox_now(a):
        try:
            d = gmail(a, "/labels/INBOX")
            return a, d.get("messagesTotal", 0), d.get("threadsUnread", 0)
        except Exception:  # noqa: BLE001
            return a, None, None
    now = {a: (t, u) for a, t, u in POOL.map(inbox_now, list(accs))}

    by_rule = {}
    for name, acc, n in rules:
        by_rule.setdefault(name, {"name": name, "total": 0, "accounts": {}})
        by_rule[name]["total"] += n
        by_rule[name]["accounts"][accs.get(acc, acc)] = n
    clean = {}
    for st, acc, n in cleanup:
        clean.setdefault(st, {"total": 0, "accounts": {}})
        clean[st]["total"] += n
        clean[st]["accounts"][accs.get(acc, acc)] = n
    accounts = []
    for acc_id, em in accs.items():
        p = next((r for r in per if r[0] == acc_id), (acc_id, 0, 0, 0, 0))
        accounts.append({"email": em, "received": p[1], "unread": p[2], "inInbox": p[3], "senders": p[4],
                         "readPct": round(100 * (p[1] - p[2]) / p[1]) if p[1] else None,
                         "inboxNow": now[acc_id][0], "unreadThreadsNow": now[acc_id][1]})
    total = sum(a["received"] for a in accounts)
    unread = sum(a["unread"] for a in accounts)
    return {
        "days": days,
        "totals": {"received": total, "readPct": round(100 * (total - unread) / total) if total else None,
                   "inboxNow": sum(a["inboxNow"] or 0 for a in accounts),
                   "senders": n_senders,
                   "ruleActions": sum(r["total"] for r in by_rule.values()),
                   "unsubscribed": clean.get("UNSUBSCRIBED", {}).get("total", 0),
                   "autoArchived": clean.get("AUTO_ARCHIVED", {}).get("total", 0)},
        "daily": [{"day": d, "email": accs.get(a, a), "count": n} for d, a, n in daily],
        "accounts": sorted(accounts, key=lambda a: -a["received"]),
        "topSenders": [{"addr": a, "name": nm, "count": n, "unread": u, "accounts": k} for a, nm, n, u, k in top_senders],
        "topDomains": [{"domain": d, "count": n, "unread": u, "senders": s} for d, n, u, s in top_domains],
        "rules": sorted(by_rule.values(), key=lambda r: -r["total"]),
        "cleanup": clean,
        "coverage": {"backfilled": collect[0], "accounts": collect[1],
                     "since": collect[2].isoformat() if collect[2] else None},
    }
