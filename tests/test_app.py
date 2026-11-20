"""app.py's gate, over a real loopback socket: who is signed in (verified Access token, or the plain header only with
the explicit override), the custom header every POST needs, and the refusal to start without verified sign-in."""
import contextlib, http.client, io, json, os, socket, sys, threading, time, unittest
from unittest import mock
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import access, app  # noqa: E402
from fakes import Patched  # noqa: E402

ME, HDR = "roy@example.com", "Cf-Access-Authenticated-User-Email"


class Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quiet = Patched(app.H, log_message=lambda self, *a: None)
        cls.quiet.__enter__()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.quiet.__exit__()

    def setUp(self):
        self.ran = []
        self.mode(token=False, header=False)
        for mod, names in ((app, dict(ALLOWED={ME})),):
            p = Patched(mod, **names)
            p.__enter__()
            self.addCleanup(p.__exit__)
        old_get, old_post = dict(app.GET), dict(app.POST)
        app.GET["/api/config"] = lambda q: {"ok": "config"}
        app.POST["/api/sender"] = lambda d: self.ran.append(d) or {"ok": "ran"}
        self.addCleanup(lambda: (app.GET.clear(), app.GET.update(old_get), app.POST.clear(), app.POST.update(old_post)))

    def mode(self, token, header):
        for mod, names in ((access, dict(ENABLED=token, email_from=lambda tok: {"good": ME, "other": "eve@x.com"}.get(tok))),
                           (app, dict(HEADER_TRUST=header))):
            p = Patched(mod, **names)
            p.__enter__()
            self.addCleanup(p.__exit__)

    def call(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        try:
            c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers or {})
            r = c.getresponse()
            return r.status, json.loads(r.read() or b"{}")
        finally:
            c.close()

    def test_health_needs_no_sign_in(self):
        self.assertEqual(self.call("GET", "/healthz"), (200, {"ok": True}))

    def test_with_nothing_configured_nobody_gets_in_whatever_they_send(self):
        for headers in ({}, {HDR: ME}, {"Cf-Access-Jwt-Assertion": "good"}, {HDR: ME, "Cf-Access-Jwt-Assertion": "good"}):
            self.assertEqual(self.call("GET", "/api/config", headers)[0], 403, headers)
            self.assertEqual(self.call("GET", "/", headers)[0], 403)
            self.assertEqual(self.call("POST", "/api/sender", {**headers, "X-Requested-With": "zero-sum"}, {})[0], 403)
        self.assertEqual(self.ran, [])

    def test_token_mode_believes_the_verified_token_and_never_the_plain_header(self):
        self.mode(token=True, header=False)
        self.assertEqual(self.call("GET", "/api/config", {"Cf-Access-Jwt-Assertion": "good"}), (200, {"ok": "config"}))
        for headers in ({HDR: ME}, {}, {"Cf-Access-Jwt-Assertion": "forged", HDR: ME},
                        {"Cf-Access-Jwt-Assertion": "other"}):            # a valid token for someone not allowed
            self.assertEqual(self.call("GET", "/api/config", headers)[0], 403, headers)

    def test_token_mode_wins_even_if_the_override_is_also_set(self):
        self.mode(token=True, header=True)
        self.assertEqual(self.call("GET", "/api/config", {HDR: ME})[0], 403)

    def test_header_mode_is_only_the_explicit_override(self):
        self.mode(token=False, header=True)
        self.assertEqual(self.call("GET", "/api/config", {HDR: ME.upper()})[0], 200)
        self.assertEqual(self.call("GET", "/api/config", {HDR: "eve@x.com"})[0], 403)
        self.assertEqual(self.call("GET", "/api/config", {})[0], 403)

    def test_a_post_needs_the_custom_header_and_a_known_path(self):
        self.mode(token=True, header=False)
        tok = {"Cf-Access-Jwt-Assertion": "good"}
        self.assertEqual(self.call("POST", "/api/sender", tok, {"a": 1})[0], 400)                 # a cross-site form post
        self.assertEqual(self.call("POST", "/api/sender", {**tok, "X-Requested-With": "other"}, {})[0], 400)
        self.assertEqual(self.call("POST", "/api/nope", {**tok, "X-Requested-With": "zero-sum"}, {})[0], 400)
        self.assertEqual(self.ran, [])
        self.assertEqual(self.call("POST", "/api/sender", {**tok, "X-Requested-With": "zero-sum"}, {"a": 1}), (200, {"ok": "ran"}))
        self.assertEqual(self.ran, [{"a": 1}])
        self.assertEqual(self.call("POST", "/api/sender", {"X-Requested-With": "zero-sum"}, {})[0], 403)   # not signed in

    def test_errors_map_to_statuses_without_leaking_a_trace(self):
        self.mode(token=True, header=False)
        tok = {"Cf-Access-Jwt-Assertion": "good", "X-Requested-With": "zero-sum"}
        for exc, code in ((PermissionError("x is protected"), 409), (ValueError("bad sender address"), 400)):
            def boom(d, exc=exc):
                raise exc
            app.POST["/api/sender"] = boom
            status, body = self.call("POST", "/api/sender", tok, {})
            self.assertEqual((status, body["error"]), (code, str(exc)))

    def test_an_unexpected_error_reaches_the_page_only_as_a_reference(self):
        self.mode(token=True, header=False)
        tok = {"Cf-Access-Jwt-Assertion": "good", "X-Requested-With": "zero-sum"}

        def boom(d):
            raise RuntimeError("Gmail POST /messages/batchModify: 400 secret-subject from alice@example.org")
        app.POST["/api/sender"] = boom
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status, body = self.call("POST", "/api/sender", tok, {})
        self.assertEqual(status, 502)
        self.assertNotIn("alice@example.org", json.dumps(body))
        self.assertNotIn("RuntimeError", json.dumps(body))
        self.assertIn(body["ref"], body["error"])
        self.assertIn(f"ref {body['ref']}", out.getvalue())                     # the log keeps the details
        self.assertIn("alice@example.org", out.getvalue())

    def test_failed_requests_are_logged_without_their_query_string(self):
        h = app.H.__new__(app.H)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.quiet.old["log_message"](h, '"%s" %s %s', "GET /api/messages?q=alice%40example.org&account=acc1 HTTP/1.1",
                                          "502", "-")
            self.quiet.old["log_message"](h, '"%s" %s %s', "GET /api/messages?q=x HTTP/1.1", "200", "-")
        self.assertEqual(out.getvalue(), '"GET /api/messages HTTP/1.1" 502 -\n')

    def raw_post(self, length_header, body=b"", extra=()):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        try:
            c.putrequest("POST", "/api/sender")
            for k, v in (("Cf-Access-Jwt-Assertion", "good"), ("X-Requested-With", "zero-sum"), ("Content-Length", length_header),
                         *extra):
                c.putheader(k, v)
            c.endheaders(body)
            r = c.getresponse()
            return r.status, json.loads(r.read() or b"{}")
        finally:
            c.close()

    def test_a_long_paste_is_read_whole_and_an_oversized_or_bad_body_is_refused_not_cut(self):
        self.mode(token=True, header=False)
        tok = {"Cf-Access-Jwt-Assertion": "good", "X-Requested-With": "zero-sum"}
        text = "\n".join(f"sender{i}@example{i}.com, a note about why it is kept" for i in range(2000))   # ~100 KB
        self.assertGreater(len(text), 20000)
        self.assertEqual(self.call("POST", "/api/sender", tok, {"text": text}), (200, {"ok": "ran"}))
        self.assertEqual(self.ran, [{"text": text}])
        status, body = self.raw_post(str(app.MAX_BODY + 1), b"{}")
        self.assertEqual(status, 413)
        self.assertIn("too large", body["error"])
        self.assertEqual(self.raw_post("-5")[0], 400)          # read(-1) would hang until the client hung up
        self.assertEqual(self.raw_post("abc")[0], 400)
        self.assertEqual(len(self.ran), 1)

    def test_an_answer_sent_before_the_body_was_read_drains_it_first(self):
        # closing with the body unread makes the system send a reset, which can arrive before the 413 / 400 / 403
        self.mode(token=True, header=False)
        drained = []
        # only this test's requests count: an earlier test's handler may still be finishing its own drain
        probe = ("X-Probe", "drain")
        with Patched(app.H, drain=lambda h, s: h.headers.get("X-Probe") == "drain" and drained.append(s)):
            self.assertEqual(self.raw_post(str(app.MAX_BODY + 1), b"{}", [probe])[0], 413)
            self.assertEqual(self.raw_post("-5", b"", [probe])[0], 400)
            self.assertEqual(self.call("POST", "/api/nope", {"Cf-Access-Jwt-Assertion": "good",
                                                            "X-Requested-With": "zero-sum", "X-Probe": "drain"}, {})[0], 400)
            self.assertEqual(self.call("POST", "/api/sender", {"X-Requested-With": "zero-sum", "X-Probe": "drain"},
                                       {"a": 1})[0], 403)
            self.assertEqual(self.call("GET", "/api/accounts", {"X-Probe": "drain"})[0], 403)   # nothing follows a GET
            end = time.monotonic() + 2     # a handler drains just after its answer went out: give it a moment
            while len(drained) < 4 and time.monotonic() < end:
                time.sleep(0.01)
        self.assertEqual(drained, [app.DRAIN_408] * 4)

    def test_a_large_refused_body_still_gets_its_413(self):
        self.mode(token=True, header=False)
        port, n = self.srv.server_address[1], app.MAX_BODY * 3
        for _ in range(5):
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            try:
                s.sendall(b"POST /api/sender HTTP/1.1\r\nHost: x\r\nCf-Access-Jwt-Assertion: good\r\n"
                          b"X-Requested-With: zero-sum\r\nContent-Length: %d\r\n\r\n" % n)
                s.sendall(b"x" * n)      # like a browser: the whole body first, then the answer
                got = s.recv(200)
            finally:
                s.close()
            self.assertTrue(got.startswith(b"HTTP/1.0 413"), got)

    def test_a_body_that_is_not_a_json_object_is_refused(self):
        self.mode(token=True, header=False)
        for body in (b"[1, 2]", b'"text"', b"42", b"null", b"true"):
            status, out = self.raw_post(str(len(body)), body)
            self.assertEqual((status, out), (400, {"error": "bad json"}), body)
        self.assertEqual(self.ran, [])

    def slow_post(self, body, chunks, pause):
        """POST `body` in `chunks` pieces (then stop) with `pause` seconds between them; the raw answer."""
        s = socket.create_connection(("127.0.0.1", self.srv.server_address[1]), timeout=5)
        try:
            s.sendall((f"POST /api/sender HTTP/1.1\r\nHost: x\r\nCf-Access-Jwt-Assertion: good\r\n"
                       f"X-Requested-With: zero-sum\r\nContent-Length: {len(body)}\r\n\r\n").encode())
            size = -(-len(body) // 10)
            for i in range(chunks):
                s.sendall(body[i * size:(i + 1) * size])
                time.sleep(pause)
            return s.recv(200)
        except OSError as e:
            return repr(e).encode()
        finally:
            s.close()

    def test_a_large_body_gets_time_by_its_size_and_a_late_one_is_answered_408(self):
        self.mode(token=True, header=False)
        body = json.dumps({"text": "a" * 10000}).encode()
        with Patched(app.H, read_deadline=0.4), Patched(app, BODY_MIN_RATE=10000), \
                contextlib.redirect_stdout(io.StringIO()):
            # ~10 KB over ~0.8 s: past the bare 0.4 s, inside 0.4 s + 10 KB at 10 KB/s
            self.assertTrue(self.slow_post(body, 10, 0.08).startswith(b"HTTP/1.0 200"))
            # the client stops half way: answered 408 at the deadline, not dropped
            t = time.monotonic()
            self.assertTrue(self.slow_post(body, 5, 0.01).startswith(b"HTTP/1.0 408"))
            self.assertLess(time.monotonic() - t, 3)
        self.assertEqual(len(self.ran), 1)

    def test_a_client_still_sending_after_the_deadline_gets_the_408_not_a_reset(self):
        self.mode(token=True, header=False)
        body = json.dumps({"text": "a" * 20000}).encode()
        with Patched(app.H, read_deadline=0.3), Patched(app, BODY_MIN_RATE=10 ** 9), \
                contextlib.redirect_stdout(io.StringIO()):
            # keeps sending past the deadline (10 pieces over ~0.6 s), then reads the answer
            self.assertTrue(self.slow_post(body, 10, 0.06).startswith(b"HTTP/1.0 408"))
        self.assertEqual(self.ran, [])

    def test_the_longest_read_deadline_is_what_the_readme_says(self):
        longest = app.READ_DEADLINE + app.MAX_BODY / app.BODY_MIN_RATE
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md")) as f:
            self.assertIn(f"{longest:.0f} seconds at most", " ".join(f.read().split()))

    def test_a_body_nested_too_deep_is_refused_not_dropped(self):
        self.mode(token=True, header=False)
        body = b"[" * 100000
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                out = self.raw_post(str(len(body)), body)
            except (http.client.HTTPException, OSError) as e:   # the old code hung up without an answer
                out = repr(e)
        self.assertEqual(out, (400, {"error": "bad json"}))
        self.assertEqual(self.ran, [])


class Limits(unittest.TestCase):
    """Unauthenticated connections cannot tie the server up: an idle or slow one is dropped and holds no request slot,
    requests worked on and connections open are capped, and /healthz answers while every request slot is busy."""

    def serve(self, **kw):
        srv = app.Server(("127.0.0.1", 0), app.H, **kw)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return srv.server_address[1]

    def test_the_handler_has_a_socket_timeout(self):
        self.assertTrue(0 < app.H.timeout <= 60)
        self.assertTrue(0 < app.MAX_THREADS <= 100)

    def test_an_idle_connection_is_closed(self):
        with Patched(app.H, timeout=0.3):
            port = self.serve()
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            self.addCleanup(s.close)
            self.assertEqual(s.recv(10), b"")                                 # the server hung up on it

    def get(self, port, path="/healthz", timeout=5):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        self.addCleanup(c.close)
        c.request("GET", path)
        return c.getresponse().status

    def test_trickling_clients_hold_no_slot_and_are_dropped_after_the_read_deadline(self):
        port = self.serve(max_threads=2)
        socks = []
        with Patched(app.H, read_deadline=1.0, timeout=5), contextlib.redirect_stdout(io.StringIO()):
            for _ in range(3):   # more slow clients than request slots, each sending a byte well inside the idle limit
                s = socket.create_connection(("127.0.0.1", port), timeout=5)
                self.addCleanup(s.close)
                s.sendall(b"GET /healthz HTTP/1.1\r\n")
                socks.append(s)
            self.assertEqual(self.get(port), 200)                               # still answered
            t = time.monotonic()
            while time.monotonic() - t < 3:
                try:
                    for s in socks:
                        s.sendall(b"X")
                except OSError:
                    break
                if all(_closed(s) for s in socks):
                    break
                time.sleep(0.2)
            self.assertLess(time.monotonic() - t, 2.5)
            self.assertTrue(all(_closed(s) for s in socks))                     # dropped at the deadline

    def test_a_request_waits_for_a_free_slot_but_healthz_does_not(self):
        srv = app.Server(("127.0.0.1", 0), app.H, max_threads=1)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        port = srv.server_address[1]
        srv.slots.acquire()                                                      # every request slot is busy
        self.addCleanup(srv.slots.release)
        with Patched(app, SLOT_WAIT=0.2), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.get(port, "/"), 503)
            self.assertEqual(self.get(port), 200)

    def test_connections_beyond_the_connection_cap_are_turned_away_at_once(self):
        port = self.serve(max_connections=1)
        idle = socket.create_connection(("127.0.0.1", port), timeout=5)       # holds the only connection
        self.addCleanup(idle.close)
        time.sleep(0.2)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.get(port, timeout=2), 503)
            idle.close()
            time.sleep(0.2)
            self.assertEqual(self.get(port), 200)


def _closed(s):
    """Has the server closed this socket (EOF, or reset)?"""
    s.setblocking(False)
    try:
        return s.recv(100) == b"" or _closed(s)
    except BlockingIOError:
        return False
    except OSError:
        return True
    finally:
        s.setblocking(True)


class Startup(unittest.TestCase):
    def test_it_refuses_to_start_without_verified_sign_in(self):
        with Patched(access, ENABLED=False), Patched(app, HEADER_TRUST=False):
            with self.assertRaises(SystemExit) as e:
                app.sign_in_mode()
            self.assertIn("CF_ACCESS_TEAM_DOMAIN", str(e.exception))
        with Patched(access, ENABLED=False), Patched(app, HEADER_TRUST=True):
            self.assertEqual(app.sign_in_mode(), "header")
        with Patched(access, ENABLED=True), Patched(app, HEADER_TRUST=True):
            self.assertEqual(app.sign_in_mode(), "token")

    def test_the_override_is_exactly_1_and_the_default_bind_is_loopback(self):
        import subprocess
        code = ("import os,sys; sys.path.insert(0, %r); import app; "
                "print(app.HEADER_TRUST, app.LISTEN_HOST)" % os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        def run(**env):
            base = {k: v for k, v in os.environ.items() if k not in ("ALLOW_HEADER_TRUST", "LISTEN_HOST")}
            return subprocess.run([sys.executable, "-c", code], env={**base, **env}, capture_output=True, text=True).stdout.split()
        self.assertEqual(run(), ["False", "127.0.0.1"])
        self.assertEqual(run(ALLOW_HEADER_TRUST="true"), ["False", "127.0.0.1"])
        self.assertEqual(run(ALLOW_HEADER_TRUST="1", LISTEN_HOST="0.0.0.0"), ["True", "0.0.0.0"])

    def test_the_image_listens_on_every_interface_and_the_app_itself_does_not(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "Containerfile")) as f:
            self.assertIn("ENV LISTEN_HOST=0.0.0.0", f.read())
        with open(os.path.join(root, "app.py")) as f:
            self.assertNotIn('("0.0.0.0"', f.read())


class Backlog(unittest.TestCase):
    def test_the_listen_queue_takes_a_burst(self):
        self.assertGreaterEqual(app.Server.request_queue_size, 64)
        srv = app.Server(("127.0.0.1", 0), app.H)
        try:
            # 40 connections at once, none accepted yet: all of them are queued, none refused or left hanging
            srv.socket.settimeout(0)
            socks = [socket.create_connection(srv.server_address, timeout=2) for _ in range(40)]
            for s in socks:
                s.close()
        finally:
            srv.server_close()


class FirstRun(unittest.TestCase):
    """A database that is not set up yet ends the program with one sentence that says what to do, not a traceback."""

    def fail_with(self, err, during="start"):
        def step():
            raise err
        with self.assertRaises(SystemExit) as e:
            app.first_run(step, during)
        return str(e.exception)

    def test_starting_before_init_db_says_to_run_it(self):
        import psycopg
        for err in (psycopg.errors.InvalidSchemaName('schema "unified" does not exist'),
                    psycopg.errors.InsufficientPrivilege("permission denied for schema unified"),
                    psycopg.OperationalError('connection failed: FATAL:  password authentication failed for user '
                                             '"iz_unified"')):
            msg = self.fail_with(err)
            self.assertIn("python app.py init-db", msg)
            self.assertNotIn("Traceback", msg)

    def test_init_db_without_inbox_zeros_tables_says_where_to_point_it(self):
        import psycopg
        msg = self.fail_with(psycopg.errors.UndefinedTable('relation "Account" does not exist'), "init-db")
        self.assertIn("Inbox Zero's tables are not in this database", msg)
        self.assertIn('relation "Account" does not exist', msg)
        msg = self.fail_with(psycopg.errors.InsufficientPrivilege("must be owner of table Rule"), "init-db")
        self.assertIn("owner connection", msg)

    def test_a_refused_password_is_not_called_a_missing_set_up(self):
        import psycopg
        refused = 'connection failed: FATAL:  password authentication failed for user "postgres"'
        err = psycopg.OperationalError(refused)
        err.zs_conn = "admin"
        msg = self.fail_with(err, "init-db")
        self.assertIn("ADMIN_DATABASE_URL was refused: wrong user or password", msg)
        self.assertNotIn("not set up", msg)
        err = psycopg.OperationalError(refused.replace("postgres", "iz_unified"))
        msg = self.fail_with(err)            # at start: the password in DATABASE_URL may be the wrong one
        self.assertIn("refused DATABASE_URL's login", msg)
        self.assertIn("fix DATABASE_URL", msg)
        self.assertNotIn("not set up", msg)
        err = psycopg.errors.InsufficientPrivilege("permission denied for schema unified")
        err.zs_conn = "app"                  # init_schema after the grants: not ADMIN_DATABASE_URL's fault
        msg = self.fail_with(err, "init-db")
        self.assertIn("ALTER SCHEMA unified OWNER TO iz_unified", msg)
        self.assertNotIn("ADMIN_DATABASE_URL may not", msg)

    def test_init_db_marks_which_connection_failed(self):
        import psycopg

        def refuse(*a, **k):
            raise psycopg.OperationalError("password authentication failed for user \"postgres\"")
        env = {"ADMIN_DATABASE_URL": "postgresql://postgres:x@h/db", "DATABASE_URL": "postgresql://iz_unified:pw@h/db"}
        with mock.patch.dict(os.environ, env), mock.patch.object(psycopg, "connect", refuse):
            with self.assertRaises(SystemExit) as e:
                app.first_run(app.init_db, "init-db")
        self.assertIn("ADMIN_DATABASE_URL was refused", str(e.exception))

    def test_other_errors_are_not_hidden(self):
        import psycopg

        def step():
            raise psycopg.errors.SyntaxError("syntax error at or near x")
        with self.assertRaises(psycopg.errors.SyntaxError):
            app.first_run(step)

    def test_the_entry_point_runs_its_set_up_through_first_run(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")) as f:
            main = f.read().split('if __name__ == "__main__":')[1]
        self.assertIn('first_run(init_db, "init-db")', main)
        self.assertIn("first_run(init_schema)", main)


if __name__ == "__main__":
    unittest.main()
