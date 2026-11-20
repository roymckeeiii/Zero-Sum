"""core.gmail: busy answers (429, 500, 503) are retried with back-off; 502 and 504, like a dropped connection, only
for requests that are harmless to send twice, since the change may already have been applied; other errors are raised
at once."""
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
    def call(self, answers, method="GET", path="/labels/INBOX"):
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
                return core.gmail("acc-retry", path, method, {} if method != "GET" else None), seen, slept
            except Exception as e:  # noqa: BLE001
                return e, seen, slept

    def http(self, code):
        return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b"busy"))

    def test_busy_answers_to_a_read_are_retried(self):
        for code in (500, 502, 503, 504):
            out, seen, slept = self.call([self.http(code), '{"ok": 1}'])
            self.assertEqual((out, len(seen), slept), ({"ok": 1}, 2, [1]), code)

    def test_a_gateway_error_on_a_new_filter_or_label_is_not_sent_again(self):
        for path in ("/settings/filters", "/labels"):
            for code in (502, 504):
                out, seen, _ = self.call([self.http(code), '{"id": "F2"}'], method="POST", path=path)
                self.assertIsInstance(out, RuntimeError, (path, code))
                self.assertEqual(seen, ["POST"], (path, code))
                self.assertFalse(out.retried)

    def test_a_gateway_error_on_a_label_change_is_sent_again(self):
        for path in ("/messages/batchModify", "/messages/abc/trash", "/messages/abc/untrash"):
            out, seen, _ = self.call([self.http(504), '{}'], method="POST", path=path)
            self.assertEqual((out, len(seen)), ({}, 2), path)

    def test_an_error_after_a_repeat_says_so(self):
        out, _, _ = self.call([self.http(503), urllib.error.HTTPError("u", 400, "x", {}, io.BytesIO(b"exists"))],
                              method="POST", path="/settings/filters")
        self.assertTrue(out.retried)

    def test_an_error_after_a_rate_limit_is_not_marked_as_a_repeat(self):
        # 429 and a 403 rate limit are refusals: Gmail did nothing, so "exists" on the next try is not this app's
        for first in (self.http(429), urllib.error.HTTPError("u", 403, "x", {}, io.BytesIO(b"rateLimitExceeded"))):
            out, seen, _ = self.call([first, urllib.error.HTTPError("u", 400, "x", {}, io.BytesIO(b"exists"))],
                                     method="POST", path="/settings/filters")
            self.assertEqual(len(seen), 2)
            self.assertFalse(out.retried, first.code)

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


class Documented(unittest.TestCase):
    def test_the_readme_says_what_is_asked_again(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "README.md"), encoding="utf-8") as f:
            text = " ".join(f.read().split())
        # core.gmail asks again after a dropped connection only for a GET, not for a label change
        self.assertIn("A dropped connection (reset, timeout) is asked again only for reads:", text)
        self.assertNotIn("a dropped connection is asked again only for reads and label changes", text)


class LabelExists(unittest.TestCase):
    def test_a_label_made_meanwhile_is_used_instead_of_failing(self):
        made = []

        def gmail(acc, path, method="GET", body=None, retry=True):
            if method == "POST":
                made.append(body["name"])
                raise RuntimeError("Gmail POST /labels: 409 Label name exists or conflicts")
            return {"labels": [{"id": "Label_9", "name": n, "type": "user"} for n in made]}
        with Patched(core, gmail=gmail, _labels={}):
            self.assertEqual(core.label_id("acc-l", "Receipts"), "Label_9")


if __name__ == "__main__":
    unittest.main()
