"""Stand-ins for Postgres and Gmail, shared by the tests. Nothing here opens a socket."""
import re


class Result:
    def __init__(self, rows):
        self.rows = list(rows or [])

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Db:
    """core.db() stand-in. Keeps unified.filter and unified.protected in memory; any other statement is answered by
    the first handler whose text occurs in the SQL (`on("FROM unified.msg", rows_or_function)`), else with no rows.
    Every statement is kept in .sql as (text, params)."""

    def __init__(self):
        self.sql, self.handlers, self.filters, self.protected, self.commits = [], [], [], set(), 0

    def on(self, text, rows):
        self.handlers.insert(0, (text, rows))
        return self

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self

    def commit(self):
        self.commits += 1

    def executemany(self, sql, rows):
        for r in rows:
            self.execute(sql, r)

    def said(self, text):
        return [(s, p) for s, p in self.sql if text in s]

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        for text, rows in self.handlers:
            if text in sql:
                return Result(rows(params) if callable(rows) else rows)
        p = params or ()
        if "INSERT INTO unified.filter" in sql:
            acc, fid, addr, kind = p[:4]
            source = p[4] if len(p) > 4 else "adopted"
            if not any(f[0] == acc and f[1] == fid for f in self.filters):
                self.filters.append((acc, fid, addr, kind, source))
        elif "DELETE FROM unified.filter" in sql:
            self.filters = [f for f in self.filters if not (f[0] == p[0] and f[1] == p[1])]
        elif "FROM unified.filter" in sql:
            rows = [f for f in self.filters if "account_id = %s" not in sql or f[0] == p[0]]
            if "kind = ANY" in sql:
                rows = [f for f in rows if f[3] in p[1]]
            if "SELECT filter_id, addr" in sql:
                return Result([(f[1], f[2]) for f in rows])
            if "SELECT filter_id" in sql:
                return Result([(f[1],) for f in rows])
            if "SELECT DISTINCT account_id" in sql:
                return Result(sorted({(f[0],) for f in self.filters if f[2] == p[0]}))
        elif "SELECT key FROM unified.protected" in sql:
            return Result([(k,) for k in sorted(self.protected)])
        elif "INSERT INTO unified.protected" in sql:
            self.protected.add(p[0])
        elif "DELETE FROM unified.protected" in sql:
            self.protected.discard(p[0])
        return Result([])


class Gmail:
    """core.gmail() stand-in for one or more accounts: Gmail filters (create / list / delete), message searches
    answered from .found ({substring of the decoded query: [ids]}), and every changing call kept in .calls."""

    def __init__(self):
        self.filters, self.calls, self.found, self.n, self.labels = {}, [], {}, 0, {"Quarantine": "Label_Q"}
        self.meta = {}  # message id -> From header, for metadata reads

    def changes(self, part=""):
        return [c for c in self.calls if part in c[1]]

    def __call__(self, acc, path, method="GET", body=None, retry=True):
        import urllib.parse
        if method != "GET":
            self.calls.append((acc, path, method, body))
        fl = self.filters.setdefault(acc, [])
        if path == "/settings/filters" and method == "GET":
            return {"filter": [dict(f) for f in fl]}
        if path == "/settings/filters" and method == "POST":
            if any(f["criteria"] == body["criteria"] and f["action"] == body["action"] for f in fl):
                raise RuntimeError("Gmail POST /settings/filters: 400 Filter already exists")
            self.n += 1
            f = {"id": f"F{self.n}", **body}
            fl.append(f)
            return dict(f)
        if path.startswith("/settings/filters/") and method == "DELETE":
            fid = path.rsplit("/", 1)[-1]
            if not any(f["id"] == fid for f in fl):
                raise RuntimeError(f"Gmail DELETE {path}: 404 Not Found")
            self.filters[acc] = [f for f in fl if f["id"] != fid]
            return {}
        if path.startswith("/labels") and method == "GET":
            return {"labels": [{"id": i, "name": n, "type": "user"} for n, i in self.labels.items()]}
        if path.startswith("/messages?"):
            q = urllib.parse.unquote(path)
            for part, ids in self.found.items():
                if part in q:
                    return {"messages": [{"id": i} for i in ids]}
            return {}
        m = re.match(r"/messages/([^/?]+)\?", path)
        if m:
            if m.group(1) not in self.meta:
                raise RuntimeError(f"Gmail GET /messages/{m.group(1)}: 404 Not Found")
            return {"id": m.group(1), "threadId": "t", "labelIds": [], "internalDate": "0",
                    "payload": {"headers": [{"name": "From", "value": self.meta[m.group(1)]}]}}
        return {}


class Accounts:
    def __init__(self, *emails):
        self.accs = [{"id": f"acc{i + 1}", "email": e, "name": e, "master": i == 0} for i, e in enumerate(emails)]

    def list(self):
        return [dict(a) for a in self.accs]

    def master(self):
        return dict(self.accs[0])

    def row(self, account_id):
        return next(a for a in self.accs if a["id"] == account_id)

    def email(self, account_id):
        return self.row(account_id)["email"]


class Patched:
    """with Patched(module, name=value, ...): set module attributes, restore them on exit."""

    def __init__(self, module, **names):
        self.module, self.names, self.old = module, names, {}

    def __enter__(self):
        for k, v in self.names.items():
            self.old[k] = getattr(self.module, k)
            setattr(self.module, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(self.module, k, v)
        return False
