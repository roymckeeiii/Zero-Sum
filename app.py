"""Zero Sum: a unified view over every Gmail account linked in a self-hosted Inbox Zero.

  python app.py            serve (default)
  python app.py init-db    create/update its database role and schema (needs ADMIN_DATABASE_URL), then exit
  python app.py adopt-filters [--apply] [--all]
                           one-off after upgrading: report (or with --apply record) the Gmail filters this app made
                           before it kept a record of them; never deletes or changes a filter

Tabs: Inbox (merged, read + triage), Cleanup (bulk unsubscribe / auto-archive / archive across accounts), Analytics,
Rules (the master account's rules pushed to every account). Nothing here sends email.

Configuration is environment only (see .env.example). It must sit behind an authenticating proxy. With Cloudflare
Access (CF_ACCESS_TEAM_DOMAIN + CF_ACCESS_AUD) the signed Access token says who is signed in; without those it refuses
to start, unless ALLOW_HEADER_TRUST=1 (local development) makes it believe the plain AUTH_EMAIL_HEADER. The user must
be in ALLOWED_EMAILS, and every POST needs a custom header (no cross-site form posts). It listens on LISTEN_HOST
(default 127.0.0.1; the container image sets 0.0.0.0, where the published port decides who can reach it). Background jobs: collector every COLLECT_EVERY_MIN, rule sync every
RULES_EVERY_MIN (only when auto sync is on in the Rules tab).
"""
import io, json, os, re, secrets, sys, threading, time, traceback, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import access, actions, ai, aisort, analytics, collector, core, gmailops, inbox, izdb, rulesync
from core import init_schema, log, set_setting

HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWED = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}
AUTH_HEADER = os.environ.get("AUTH_EMAIL_HEADER", "Cf-Access-Authenticated-User-Email")
HEADER_TRUST = os.environ.get("ALLOW_HEADER_TRUST", "").strip() == "1"  # local development only: see sign_in_mode
LISTEN_HOST = os.environ.get("LISTEN_HOST", "").strip() or "127.0.0.1"
INBOX_ZERO_URL = os.environ.get("INBOX_ZERO_URL", "").rstrip("/")
# Largest POST body read. Protect-many takes up to 2000 pasted senders (with CSV notes), far more than the 20 KB once
# read here: a longer paste was cut short and failed as "bad json".
MAX_BODY = 512 * 1000
READ_DEADLINE = 15   # seconds a client has to send its whole request, headers and body
# ... plus the time a body of Content-Length bytes takes at BODY_MIN_RATE bytes a second (a long paste on a slow
# link). The longest a request can take is then READ_DEADLINE + MAX_BODY / BODY_MIN_RATE = 15 + 64 = 79 seconds.
# DEFAULT - Roy may change: 8 KB/s
BODY_MIN_RATE = 8000
DRAIN_408 = 1.0   # seconds a late request's remaining bytes are read (and dropped) after the 408, so it arrives
SLOT_WAIT = 60   # seconds a request read in full waits for a free request slot before it is answered 503
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data: https:; "
       "connect-src 'self'; frame-src 'self' about:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

GET = {
    "/api/config": lambda q: {"inboxZeroUrl": INBOX_ZERO_URL, "master": core.MASTER or None, "ai": bool(ai.KEY)},
    "/api/accounts": lambda q: inbox.accounts(),
    "/api/messages": inbox.messages,
    "/api/message": inbox.message,
    "/api/senders": actions.senders,
    "/api/analytics": analytics.overview,
    "/api/rules": lambda q: rulesync.state(),
    "/api/protected": actions.protected_list,
    "/api/quarantine": actions.quarantine_list,
    "/api/undo": gmailops.undo_list,
    "/api/sender/recent": gmailops.sender_recent,
    "/api/ai/plans": ai.plans,
    "/api/ai/sorting": aisort.ai_sort_status,
    "/api/ai/progress": ai.progress,
    "/api/rules/preview": lambda q: rulesync.sync(apply=False),
    "/api/rules/backups": lambda q: rulesync.backups(),
}


def _auto(d):
    set_setting("rules_auto", "1" if d.get("on") else "0")
    return {"ok": True}


def _collect(d):
    threading.Thread(target=collector.collect_all, daemon=True).start()
    return {"ok": True, "started": True}


POST = {
    "/api/action": inbox.action,
    "/api/sender": actions.sender_action,
    "/api/sender/preview": actions.preview,
    "/api/quarantine": actions.quarantine_action,
    "/api/undo": gmailops.undo_action,
    "/api/protect/bulk": actions.protect_many,
    "/api/filters/adopt": gmailops.adopt_filters,
    "/api/ai/suggest": ai.suggest,
    "/api/ai/analyze": ai.analyze,
    "/api/ai/apply": actions.apply_plan,
    "/api/ai/skip": ai.skip,
    "/api/ai/classify": aisort.classify_preview,
    "/api/ai/stop-sort": aisort.stop_ai_sort,
    "/api/ai/background": ai.set_background,
    # deletes happen only for the keys the user confirmed after seeing the preview (rulesync.sync)
    "/api/rules/sync": lambda d: rulesync.sync(apply=True, confirm=[str(k) for k in d.get("confirm") or []]),
    "/api/rules/restore": rulesync.restore,
    "/api/rules/include": rulesync.set_include,
    "/api/rules/auto": _auto,
    "/api/collect": _collect,
}


_deep = {"at": 0.0, "result": None, "lock": threading.Lock()}


def deep_health():
    """(status, body) for /healthz?deep=1: 200 only when the database answers and has every Inbox Zero table, column
    and enum this app depends on (izdb.py). Needs no sign-in, so the answer is cached for 30 seconds and says only
    how many problems there are; what they are is in the log."""
    with _deep["lock"]:
        if _deep["result"] is None or time.time() - _deep["at"] > 30:
            try:
                found = izdb.check()
                for p in found:
                    print(f"[schema] {p}", flush=True)
                res = (200, {"ok": True}) if not found else (503, {"ok": False, "error": "schema", "problems": len(found)})
            except Exception as e:  # noqa: BLE001
                print(f"[health] database check failed: {repr(e)[:300]}", flush=True)
                res = (503, {"ok": False, "error": "database"})
            _deep.update(at=time.time(), result=res)
        return _deep["result"]


def check_upstream_schema():
    """At start: say exactly which upstream tables, columns or enums are missing. The app keeps serving (most of it
    still works, and plain /healthz stays up); /healthz?deep=1 is what fails, and what an updater should poll."""
    try:
        found = izdb.check()
    except Exception as e:  # noqa: BLE001
        log("schema-error", f"could not check the Inbox Zero schema: {e}"[:500])
        return None
    for p in found:
        log("schema-error", f"Inbox Zero schema mismatch: {p}")
    if found:
        log("schema-error", f"{len(found)} thing(s) Zero Sum depends on are missing in the Inbox Zero database: is this "
                            "Inbox Zero version supported (inbox-zero.version), and was init-db run again after upgrading?")
    return found


class _Deadline(io.RawIOBase):
    """The socket as a raw stream that gives up once `deadline` (time.monotonic()) has passed: each read may wait
    only for what is left of it (and at most `idle` seconds), so a client sending a byte now and then cannot keep the
    request open."""

    def __init__(self, sock, deadline, idle):
        self.sock, self.deadline, self.idle = sock, deadline, idle

    def readable(self):
        return True

    def readinto(self, b):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("request not received in time")
        self.sock.settimeout(min(left, self.idle))
        return self.sock.recv_into(b)


class H(BaseHTTPRequestHandler):
    server_version = "zero-sum"
    timeout = 30   # seconds a connection may sit without sending: an idle or trickling client cannot hold a thread
    read_deadline = READ_DEADLINE   # and the whole request (headers and body) must arrive within this

    def setup(self):
        super().setup()
        self.rfile.close()   # replaced: same socket, but every read counts against one deadline for the request
        self.started = time.monotonic()
        self.deadline = _Deadline(self.connection, self.started + self.read_deadline, self.timeout)
        self.rfile = io.BufferedReader(self.deadline)

    def work(self, fn, *args):
        """Run fn once the request has been read, in one of the server's request slots (the request cap). /healthz
        does not take one, and a connection still sending its request holds none."""
        slots = getattr(self.server, "slots", None)
        if slots is None:
            return fn(*args)
        if not slots.acquire(timeout=SLOT_WAIT):
            return self.send(503, {"error": "busy, try again"})
        try:
            return fn(*args)
        finally:
            slots.release()

    def log_message(self, fmt, *args):
        # failed requests only, and without their query strings: those carry search words, account and message ids
        if len(args) > 1 and str(args[1])[:1] in "45":
            print(fmt % tuple(re.sub(r"\?[^\s\"']*", "", a) if isinstance(a, str) else a for a in args), flush=True)

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.connection.settimeout(self.timeout)   # the read deadline was for receiving; answering has its own
        self.send_response(code)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(b))), ("Cache-Control", "no-store"),
                     ("Content-Security-Policy", CSP), ("X-Content-Type-Options", "nosniff"),
                     ("Referrer-Policy", "no-referrer")):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def drain(self, seconds):
        """After an early answer: say no more is coming, then read and drop what the client is still sending for a
        moment. Closing with unread bytes would make the system send a reset, which can arrive before the answer."""
        import socket
        try:
            self.connection.shutdown(socket.SHUT_WR)
        except OSError:
            return
        end, buf = time.monotonic() + seconds, bytearray(65536)
        while (left := end - time.monotonic()) > 0:
            try:
                self.connection.settimeout(left)
                if not self.connection.recv_into(buf):
                    return
            except OSError:
                return

    def refuse(self, code, body):
        """An answer sent before the request body was read (403, 400, 413): the body is drained as after a 408, so the
        client gets this answer and not a reset (which the page could only report as "could not reach Zero Sum")."""
        self.close_connection = True
        self.send(code, body)
        self.drain(DRAIN_408)

    def allowed(self, body_follows=False):
        if access.ENABLED:   # the signed Access token decides; the plain email header is not trusted
            user = access.email_from(self.headers.get("Cf-Access-Jwt-Assertion"))
        elif HEADER_TRUST:   # explicit override: whoever can reach the port can write this header
            user = (self.headers.get(AUTH_HEADER) or "").lower()
        else:                # neither configured: nobody is signed in (the process refuses to start like this)
            user = None
        if user and user in ALLOWED:
            return True
        (self.refuse if body_follows else self.send)(403, {"error": "not allowed"})
        return False

    def run(self, fn, arg):
        try:
            self.send(200, fn(arg))
        except PermissionError as e:
            self.send(409, {"error": str(e)})
        except (KeyError, ValueError) as e:
            self.send(400, {"error": str(e)[:200]})
        except Exception as e:  # noqa: BLE001 - the details stay in the server log, under a reference the page shows
            ref = secrets.token_hex(4)
            print(f"error ref {ref} on {urllib.parse.urlparse(self.path).path}:", repr(e)[:300],
                  traceback.format_exc()[-800:], flush=True)
            self.send(502, {"error": f"Something went wrong (reference {ref}); the details are in Zero Sum's log.",
                            "ref": ref})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/healthz":   # plain: the process is up. ?deep=1: the database and the upstream schema too
            if urllib.parse.parse_qs(u.query).get("deep", ["0"])[0] == "1":
                return self.send(*deep_health())
            return self.send(200, {"ok": True})
        self.work(self._get, u)

    def _get(self, u):
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
        if not self.allowed(body_follows=True):
            return
        if self.path not in POST or self.headers.get("X-Requested-With") != "zero-sum":
            return self.refuse(400, {"error": "bad request"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0:            # read(-1) would wait for the client to close the connection
            return self.refuse(400, {"error": "bad request"})
        if n > MAX_BODY:     # refused, never cut short: a truncated body only ever read as "bad json"
            return self.refuse(413, {"error": f"request too large (at most {MAX_BODY // 1000} KB)"})
        # a large body gets more time, by its size: a slow link is not cut off half way, a small one gains nothing
        self.deadline.deadline = max(self.deadline.deadline, self.started + self.read_deadline + n / BODY_MIN_RATE)
        try:
            raw = self.rfile.read(n)
        except TimeoutError:   # answered, not dropped: the page can say what happened
            self.close_connection = True
            self.send(408, {"error": "the request took too long to arrive"})
            return self.drain(DRAIN_408)
        try:
            d = json.loads(raw or b"{}")
        except (ValueError, RecursionError):   # RecursionError: nested too deep ("[[[[..."), not a ValueError
            d = None
        if not isinstance(d, dict):   # every handler takes an object: a list, string or number is refused here
            return self.send(400, {"error": "bad json"})
        self.work(self.run, POST[self.path], d)


MAX_THREADS = 32   # requests worked on at once (after they have been read); more wait for a slot, then get 503
MAX_CONNECTIONS = 256   # connections open at once, including ones still sending their request; more get 503 at once


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer with bounds. A connection gets a thread only while fewer than `max_connections` are open
    (otherwise it is answered 503 and closed, and the accept loop never waits); its request must arrive within
    H.read_deadline; only then does it wait for one of `max_threads` request slots (H.work). /healthz takes no slot.
    So a few slow or idle clients cannot stop the server: they are dropped after the deadline and hold no slot."""
    daemon_threads = True

    def __init__(self, address, handler, max_threads=MAX_THREADS, max_connections=MAX_CONNECTIONS):
        self.slots = threading.BoundedSemaphore(max_threads)
        self.conns = threading.BoundedSemaphore(max_connections)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.conns.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.conns.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.conns.release()


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
        res = rulesync.sync(apply=True, auto=True)   # deletes at most rulesync.AUTO_MAX_DELETES copies between two Sync now
        changed = [o for o in res["ops"] if o["op"] not in ("same", "held")]
        held = [o for o in res["ops"] if o["op"] == "held"]
        if changed or held:
            print(f"[rules] auto sync: {len(changed)} change(s), {len(held)} waiting for Sync now", flush=True)


def sign_in_mode():
    """How requests are authenticated, decided once at start: "token" (the verified Cloudflare Access token) or
    "header" (the plain e-mail header, only with ALLOW_HEADER_TRUST=1). With neither, the app does not start: a
    header alone proves nothing, since anything that can reach the port can send it."""
    if access.ENABLED:
        return "token"
    if HEADER_TRUST:
        return "header"
    raise SystemExit("refusing to start: no verified sign-in is configured. Set CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD "
                     "so the Cloudflare Access token is checked. For local development only, ALLOW_HEADER_TRUST=1 "
                     f"makes the app believe the {AUTH_HEADER} header as sent by whoever can reach the port.")


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
    try:
        with open(os.path.join(HERE, "grants.sql")) as f, psycopg.connect(admin, autocommit=True) as c:
            c.execute(f.read())
            c.execute(sql.SQL("ALTER ROLE iz_unified WITH LOGIN PASSWORD {}").format(sql.Literal(pw)))
    except psycopg.Error as e:
        e.zs_conn = "admin"   # which connection failed, for setup_hint
        raise
    try:
        init_schema()
    except psycopg.Error as e:
        e.zs_conn = "app"
        raise
    print("init-db: role iz_unified and schema unified are ready", flush=True)


def _refused(e, text):
    """True for a login the server refused: a wrong password (Postgres says the same when the role does not exist), or
    none given where one is needed."""
    import psycopg
    return isinstance(e, psycopg.OperationalError) and any(
        x in text for x in ("password authentication failed", "no password supplied"))


def setup_hint(e, during="start"):
    """One plain sentence for a database error a first run (or a wrong DATABASE_URL) meets, or None. A refused login
    (wrong password) and a database that is simply not set up yet get different instructions: running init-db again
    does not help when ADMIN_DATABASE_URL itself is refused."""
    import psycopg
    text = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    conn = getattr(e, "zs_conn", None)   # init-db: "admin" (ADMIN_DATABASE_URL) or "app" (DATABASE_URL, after the grants)
    if during == "init-db" and _refused(e, text):
        if conn == "app":
            return (f"DATABASE_URL was refused right after init-db set the iz_unified password ({text}). Check that "
                    "DATABASE_URL and ADMIN_DATABASE_URL point at the same database server and database.")
        return (f"ADMIN_DATABASE_URL was refused: wrong user or password ({text}). Set it to Inbox Zero's owner "
                "connection (its DIRECT_URL / DATABASE_URL, with that password) and run init-db again.")
    if during == "init-db" and isinstance(e, psycopg.errors.InsufficientPrivilege):
        if conn == "app":
            return (f"the iz_unified role may not create Zero Sum's tables ({text}). The schema \"unified\" probably "
                    "belongs to another role: as Inbox Zero's owner run ALTER SCHEMA unified OWNER TO iz_unified, "
                    "then init-db again.")
        return (f"ADMIN_DATABASE_URL may not set up Zero Sum's role and grants ({text}). Use Inbox Zero's owner "
                "connection (its DIRECT_URL / DATABASE_URL).")
    if _refused(e, text):
        return (f"the database refused DATABASE_URL's login for iz_unified ({text}). Either init-db has not been run "
                "yet (it creates that role and sets its password: run `python app.py init-db` once, with "
                "ADMIN_DATABASE_URL set to Inbox Zero's owner connection), or the password in DATABASE_URL is not the "
                "one init-db set: fix DATABASE_URL, or run init-db again with this DATABASE_URL to set it.")
    if isinstance(e, (psycopg.errors.InvalidSchemaName, psycopg.errors.InsufficientPrivilege)):
        return (f"the database is not set up for Zero Sum yet ({text}). Run `python app.py init-db` once, with "
                "ADMIN_DATABASE_URL set to Inbox Zero's owner connection, then start the app again. If you already did, "
                "check that DATABASE_URL points at Inbox Zero's database.")
    if isinstance(e, psycopg.errors.UndefinedTable):
        return (f"Inbox Zero's tables are not in this database ({text}). Point ADMIN_DATABASE_URL and DATABASE_URL at "
                "Inbox Zero's own database, after Inbox Zero has started once (its migrations create the tables).")
    return None


def first_run(step, during="start"):
    """Run a set-up step; a known first-run database error ends the program with setup_hint, not a traceback."""
    import psycopg
    try:
        return step()
    except psycopg.Error as e:
        hint = setup_hint(e, during)
        if not hint:
            raise
        raise SystemExit("zero-sum: " + hint) from None


def adopt_filters_cli(args):
    init_schema()
    res = gmailops.adopt_filters({"apply": "--apply" in args, "all": "--all" in args})
    for a in res["accounts"]:
        print(f"{a['email']}: {a['filters']} filter(s) in Gmail, {a['recorded']} already on record, "
              f"{a['other']} not of a shape Zero Sum creates (never touched)"
              + (f"; ERROR {a['error']}" if a.get("error") else ""))
        for c in a["candidates"]:
            print(f"  {'adopt' if c['adopt'] else 'skip '}  {c['kind']:<10} from:{c['from']}  filter {c['id']}  "
                  f"({c['evidence'] or 'no record that Zero Sum acted on this sender: --all adopts it too'})")
    print("recorded the filters marked adopt" if res["applied"] else
          "dry run: nothing recorded. Run again with --apply to record the filters marked adopt.", flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["init-db"]:
        first_run(init_db, "init-db")
        raise SystemExit(0)
    if sys.argv[1:2] == ["adopt-filters"]:
        first_run(lambda: adopt_filters_cli(sys.argv[2:]))
        raise SystemExit(0)
    mode = sign_in_mode()
    first_run(init_schema)
    if not ALLOWED:
        raise SystemExit("ALLOWED_EMAILS is empty: set it to the address(es) your auth proxy lets in")
    check_upstream_schema()
    every(60 * int(os.environ.get("COLLECT_EVERY_MIN", "15")), collector.collect_all, 20)
    every(60 * int(os.environ.get("COLLECT_EVERY_MIN", "15")), aisort.ai_sort_new, 120)
    every(60 * int(os.environ.get("COLLECT_EVERY_MIN", "15")), ai.background_plan, 300)
    every(60 * int(os.environ.get("RULES_EVERY_MIN", "10")), auto_rules, 60)
    port = int(os.environ.get("PORT", "8080"))
    print(f"zero-sum on {LISTEN_HOST}:{port}, allowed: {len(ALLOWED)} address(es); sign-in: "
          + (f"Cloudflare Access token ({access.TEAM})" if mode == "token" else
             f"UNVERIFIED: the {AUTH_HEADER} header as sent (ALLOW_HEADER_TRUST=1; local development only)"),
          flush=True)
    Server((LISTEN_HOST, port), H).serve_forever()
