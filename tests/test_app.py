"""app.py's gate, over a real loopback socket: who is signed in (verified Access token, or the plain header only with
the explicit override), the custom header every POST needs, and the refusal to start without verified sign-in."""
import http.client, json, os, sys, threading, unittest
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

    def raw_post(self, length_header, body=b""):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        try:
            c.putrequest("POST", "/api/sender")
            for k, v in (("Cf-Access-Jwt-Assertion", "good"), ("X-Requested-With", "zero-sum"), ("Content-Length", length_header)):
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


if __name__ == "__main__":
    unittest.main()
