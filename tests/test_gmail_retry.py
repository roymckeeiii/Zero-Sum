"""core.gmail: busy answers (429, 5xx including 502 and 504) are retried with back-off; a dropped connection is
retried for reads only, since a change may already have been applied; other errors are raised at once."""
import io, os, sys, unittest, urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import core  # noqa: E402
from fakes import Patched  # noqa: E402


class Token:
    def token(self, account_id, force=False):
        return "tok"


class Retry(unittest.TestCase):
    def call(self, answers, method="GET"):
        """core.gmail with urlopen answering from `answers` in turn (an exception, or a JSON reply)."""
        seen, slept = [], []

        def urlopen(req, timeout=None):
            seen.append(req.get_method())
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return io.BytesIO(a.encode())
        with Patched(core.urllib.request, urlopen=urlopen), Patched(core.time, sleep=slept.append), \
                Patched(core, ACC=Token()):
            try:
                return core.gmail("acc-retry", "/labels/INBOX", method, {} if method != "GET" else None), seen, slept
            except Exception as e:  # noqa: BLE001
                return e, seen, slept

    def http(self, code):
        return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b"busy"))

    def test_bad_gateway_and_gateway_timeout_are_retried_like_other_5xx(self):
        for code in (500, 502, 503, 504):
            out, seen, slept = self.call([self.http(code), '{"ok": 1}'])
            self.assertEqual((out, len(seen), slept), ({"ok": 1}, 2, [1]), code)

    def test_a_dropped_connection_is_retried_for_a_read(self):
        for err in (urllib.error.URLError("reset"), TimeoutError("read timed out"), ConnectionResetError()):
            out, seen, _ = self.call([err, '{"ok": 1}'])
            self.assertEqual((out, len(seen)), ({"ok": 1}, 2), err)

    def test_a_dropped_connection_on_a_change_is_not_sent_twice(self):
        out, seen, _ = self.call([TimeoutError("read timed out"), '{"ok": 1}'], method="POST")
        self.assertIsInstance(out, TimeoutError)
        self.assertEqual(seen, ["POST"])

    def test_retries_stop_after_six_tries_and_other_errors_are_not_retried(self):
        out, seen, _ = self.call([self.http(502)] * 6)
        self.assertIsInstance(out, RuntimeError)
        self.assertIn("502", str(out))
        self.assertEqual(len(seen), 6)
        out, seen, _ = self.call([self.http(400)])
        self.assertIsInstance(out, RuntimeError)
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
