"""Zero Sum: a unified view over every Gmail account linked in a self-hosted Inbox Zero.

  python app.py            serve (default)
  python app.py init-db    create/update its database role and schema (needs ADMIN_DATABASE_URL), then exit

Tabs: Inbox (merged, read + triage), Cleanup (bulk unsubscribe / auto-archive / archive across accounts), Analytics,
Rules (the master account's rules pushed to every account). Nothing here sends email.

Configuration is environment only (see .env.example). It must sit behind an authenticating proxy (e.g. Cloudflare
Access, oauth2-proxy) that sets AUTH_EMAIL_HEADER; the app requires that header to be in ALLOWED_EMAILS and a custom
header on every POST (no cross-site form posts). Background jobs: collector every COLLECT_EVERY_MIN, rule sync every
RULES_EVERY_MIN (only when auto sync is on in the Rules tab).
"""
import json, os, sys, threading, time, traceback, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ai, analytics, cleanup, core, inbox, rulesync
from core import init_schema, log, set_setting

HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWED = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}
AUTH_HEADER = os.environ.get("AUTH_EMAIL_HEADER", "Cf-Access-Authenticated-User-Email")
INBOX_ZERO_URL = os.environ.get("INBOX_ZERO_URL", "").rstrip("/")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data: https:; "
       "connect-src 'self'; frame-src 'self' about:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

GET = {
    "/api/config": lambda q: {"inboxZeroUrl": INBOX_ZERO_URL, "master": core.MASTER or None, "ai": bool(ai.KEY)},
    "/api/accounts": lambda q: inbox.accounts(),
    "/api/messages": inbox.messages,
    "/api/message": inbox.message,
    "/api/senders": cleanup.senders,
    "/api/analytics": analytics.overview,
    "/api/rules": lambda q: rulesync.state(),
    "/api/protected": cleanup.protected_list,
    "/api/quarantine": cleanup.quarantine_list,
    "/api/undo": cleanup.undo_list,
    "/api/sender/recent": cleanup.sender_recent,
    "/api/ai/plans": ai.plans,
    "/api/ai/sorting": cleanup.ai_sort_status,
    "/api/ai/progress": ai.progress,
    "/api/rules/preview": lambda q: rulesync.sync(apply=False),
}


def _auto(d):
    set_setting("rules_auto", "1" if d.get("on") else "0")
    return {"ok": True}


def _collect(d):
    threading.Thread(target=cleanup.collect_all, daemon=True).start()
    return {"ok": True, "started": True}


POST = {
    "/api/action": inbox.action,
    "/api/sender": cleanup.sender_action,
    "/api/quarantine": cleanup.quarantine_action,
    "/api/undo": cleanup.undo_action,
    "/api/ai/suggest": ai.suggest,
    "/api/ai/analyze": ai.analyze,
    "/api/ai/apply": cleanup.apply_plan,
    "/api/ai/skip": ai.skip,
    "/api/ai/classify": ai.classify_preview,
    "/api/ai/stop-sort": cleanup.stop_ai_sort,
    "/api/ai/background": ai.set_background,
    "/api/rules/sync": lambda d: rulesync.sync(apply=True),
    "/api/rules/include": rulesync.set_include,
    "/api/rules/auto": _auto,
    "/api/collect": _collect,
}


class H(BaseHTTPRequestHandler):
    server_version = "zero-sum"

    def log_message(self, fmt, *args):
        if len(args) > 1 and str(args[1])[:1] in "45":
            print(f"{fmt % args}", flush=True)

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(b))), ("Cache-Control", "no-store"),
                     ("Content-Security-Policy", CSP), ("X-Content-Type-Options", "nosniff"),
                     ("Referrer-Policy", "no-referrer")):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def allowed(self):
        if (self.headers.get(AUTH_HEADER) or "").lower() in ALLOWED:
            return True
        self.send(403, {"error": "not allowed"})
        return False

    def run(self, fn, arg):
        try:
            self.send(200, fn(arg))
        except PermissionError as e:
            self.send(409, {"error": str(e)})
        except (KeyError, ValueError) as e:
            self.send(400, {"error": str(e)[:200]})
        except Exception as e:  # noqa: BLE001
            print("error:", repr(e)[:300], traceback.format_exc()[-800:], flush=True)
            self.send(502, {"error": f"{type(e).__name__}: {str(e)[:200]}"})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/healthz":
            return self.send(200, {"ok": True})
        if not self.allowed():
            return
        if u.path in STATIC:
            name, ctype = STATIC[u.path]
            with open(os.path.join(HERE, name), "rb") as f:
                return self.send(200, f.read(), ctype)
        if u.path in GET:
            return self.run(GET[u.path], urllib.parse.parse_qs(u.query))
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.allowed():
            return
        if self.path not in POST or self.headers.get("X-Requested-With") != "zero-sum":
            return self.send(400, {"error": "bad request"})
        n = min(int(self.headers.get("Content-Length") or 0), 20000)
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send(400, {"error": "bad json"})
        self.run(POST[self.path], d)


def every(seconds, fn, first_delay):
    def loop():
        time.sleep(first_delay)
        while True:
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                log("job-error", f"{fn.__name__}: {e}"[:500])
            time.sleep(seconds)
    threading.Thread(target=loop, daemon=True).start()


def auto_rules():
    from core import setting
    if setting("rules_auto", "0") == "1":
        res = rulesync.sync(apply=True)
        changed = [o for o in res["ops"] if o["op"] != "same"]
        if changed:
            print(f"[rules] auto sync: {len(changed)} change(s)", flush=True)


def init_db():
    """Run grants.sql as Inbox Zero's database owner and give the role the password in DATABASE_URL."""
    import psycopg
    from psycopg import sql
    admin = os.environ.get("ADMIN_DATABASE_URL", "").strip()
    if not admin:
        raise SystemExit("init-db needs ADMIN_DATABASE_URL: Inbox Zero's owner connection (its DIRECT_URL / DATABASE_URL)")
    pw = urllib.parse.urlparse(os.environ["DATABASE_URL"]).password
    if not pw:
        raise SystemExit("DATABASE_URL must contain the iz_unified password (postgresql://iz_unified:<password>@...)")
    with open(os.path.join(HERE, "grants.sql")) as f, psycopg.connect(admin, autocommit=True) as c:
        c.execute(f.read())
        c.execute(sql.SQL("ALTER ROLE iz_unified WITH LOGIN PASSWORD {}").format(sql.Literal(pw)))
    init_schema()
    print("init-db: role iz_unified and schema unified are ready", flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["init-db"]:
        init_db()
        raise SystemExit(0)
    init_schema()
    if not ALLOWED:
        raise SystemExit("ALLOWED_EMAILS is empty: set it to the address(es) your auth proxy lets in")
    every(60 * int(os.environ.get("COLLECT_EVERY_MIN", "15")), cleanup.collect_all, 20)
    every(60 * int(os.environ.get("COLLECT_EVERY_MIN", "15")), cleanup.ai_sort_new, 120)
    every(60 * int(os.environ.get("COLLECT_EVERY_MIN", "15")), ai.background_plan, 300)
    every(60 * int(os.environ.get("RULES_EVERY_MIN", "10")), auto_rules, 60)
    port = int(os.environ.get("PORT", "8080"))
    print(f"zero-sum on :{port}, allowed: {len(ALLOWED)} address(es)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
