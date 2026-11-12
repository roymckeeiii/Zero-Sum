"""Unit tests that need no database or Gmail: token decryption (checked against Inbox Zero's Node implementation when
node is available), message parsing, unsubscribe headers, rule hashing and the Gmail rate limiter."""
import base64, os, shutil, subprocess, sys, time, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import core, inbox, cleanup, rulesync  # noqa: E402

from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402


def encrypt_like_inbox_zero(text, versioned=False):
    iv = os.urandom(16)
    ct = AESGCM(core.key()).encrypt(iv, text.encode(), None)  # ciphertext + 16-byte tag
    hexed = (iv + ct[-16:] + ct[:-16]).hex()
    return f"v1:{hexed}" if versioned else hexed


class Decrypt(unittest.TestCase):
    def test_legacy_and_versioned(self):
        self.assertEqual(core.decrypt(encrypt_like_inbox_zero("ya29.abc")), "ya29.abc")
        self.assertEqual(core.decrypt(encrypt_like_inbox_zero("1//refresh", versioned=True)), "1//refresh")

    def test_plaintext_and_garbage(self):
        self.assertEqual(core.decrypt("ya29.plain"), "ya29.plain")
        self.assertIsNone(core.decrypt("00ff"))
        self.assertIsNone(core.decrypt(None))

    def test_unknown_version_is_loud(self):
        with self.assertRaises(ValueError):
            core.decrypt("v2:" + "00" * 40)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_matches_node(self):
        js = ("const c=require('crypto');const k=c.scryptSync(process.env.EMAIL_ENCRYPT_SECRET,process.env.EMAIL_ENCRYPT_SALT,32);"
              "const iv=c.randomBytes(16);const x=c.createCipheriv('aes-256-gcm',k,iv);"
              "const e=Buffer.concat([x.update('ya29.node','utf8'),x.final()]);"
              "process.stdout.write(Buffer.concat([iv,x.getAuthTag(),e]).toString('hex'))")
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
        self.assertEqual(core.decrypt(out), "ya29.node")
        self.assertEqual(core.decrypt("v1:" + out), "ya29.node")


class Parsing(unittest.TestCase):
    def test_body_prefers_html(self):
        b = lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")
        p = {"mimeType": "multipart/alternative", "parts": [{"mimeType": "text/plain", "body": {"data": b("plain")}},
                                                            {"mimeType": "text/html", "body": {"data": b("<b>x</b>")}}]}
        self.assertEqual(inbox.body_of(p), ("html", "<b>x</b>"))
        self.assertEqual(inbox.body_of({"mimeType": "text/plain", "body": {"data": b("only")}}), ("text", "only"))

    def test_unsubscribe_header(self):
        self.assertEqual(cleanup.parse_unsub("<mailto:u@x.com>, <https://x.com/u?a=1>"), "https://x.com/u?a=1")
        self.assertEqual(cleanup.parse_unsub("<mailto:u@x.com>"), "mailto:u@x.com")
        self.assertIsNone(cleanup.parse_unsub(""))

    def test_protection_matches_address_and_domain(self):
        prot = {"a@x.com", "@bank.com"}
        self.assertTrue(cleanup.is_protected("a@x.com", prot))
        self.assertTrue(cleanup.is_protected("alerts@bank.com", prot))
        self.assertFalse(cleanup.is_protected("b@x.com", prot))


class KeepWords(unittest.TestCase):
    def test_keep_query(self):
        self.assertEqual(cleanup.keep_query(["gift", "surprise"]), "subject:(gift OR surprise)")
        self.assertEqual(cleanup.keep_query(["order shipped"]), 'subject:("order shipped")')
        self.assertIsNone(cleanup.keep_query([]))
        self.assertIsNone(cleanup.keep_query(["   ", "()"]))
        self.assertNotIn(")", cleanup.keep_query(["a) OR from:(x"])[len("subject:("):-1])  # no query injection


class AiSort(unittest.TestCase):
    def test_labels_for_actions(self):
        self.assertEqual(cleanup._labels_for("keep", "L"), ([], []))
        self.assertEqual(cleanup._labels_for("quarantine", "L"), (["L"], ["INBOX"]))
        self.assertEqual(cleanup._labels_for("delete", None), (["TRASH"], []))

    def test_classify_parses_kept_numbers(self):
        import ai
        old_key, old_ask = ai.KEY, ai._ask
        try:
            ai.KEY, ai._ask = "k", lambda prompt: {"keep": [1, "3"], "drop": [2]}
            self.assertEqual(ai.classify("keep articles", [("a", ""), ("b", ""), ("c", "")]), [True, False, True])
        finally:
            ai.KEY, ai._ask = old_key, old_ask

    def test_classify_refuses_an_answer_that_is_not_a_full_verdict(self):
        # The mail not kept gets trashed, so a malformed answer must never read as "keep none".
        import ai
        old_key, old_ask = ai.KEY, ai._ask
        try:
            ai.KEY = "k"
            for bad in ({}, {"keep": []}, {"keep": "all", "drop": []}, {"keep": None, "drop": [1, 2, 3]},
                        {"keep": [1], "drop": [2]}, {"keep": [1, 2], "drop": [2, 3]}, {"keep": [1, 2, 3, 4], "drop": []},
                        {"keep": [1, "x"], "drop": [2, 3]}, [1, 2, 3]):
                ai._ask = lambda prompt, bad=bad: bad
                with self.assertRaises(ValueError, msg=repr(bad)):
                    ai.classify("keep articles", [("a", ""), ("b", ""), ("c", "")])
            ai._ask = lambda prompt: {"keep": [], "drop": [1, 2, 3]}  # an explicit "drop all" is still a valid verdict
            self.assertEqual(ai.classify("r", [("a", ""), ("b", ""), ("c", "")]), [False, False, False])
        finally:
            ai.KEY, ai._ask = old_key, old_ask


class Guards(unittest.TestCase):
    def test_domain_protection_covers_subdomains(self):
        prot = {"@chase.com", "one@x.org"}
        self.assertTrue(cleanup.is_protected("alerts@chase.com", prot))
        self.assertTrue(cleanup.is_protected("no-reply@alerts.mail.chase.com", prot))
        self.assertFalse(cleanup.is_protected("a@notchase.com", prot))
        self.assertFalse(cleanup.is_protected("a@chase.com.evil.net", prot))
        self.assertFalse(cleanup.is_protected("a@other.com", {"@com"}))  # a bare TLD protects nothing
        self.assertTrue(cleanup.is_protected("one@x.org", prot))
        self.assertFalse(cleanup.is_protected("two@x.org", prot))

    def test_keep_words_are_refused_not_truncated(self):
        with self.assertRaises(ValueError):
            cleanup.keep_query([f"w{i}" for i in range(11)])
        self.assertEqual(cleanup.keep_query(["-order", " receipt "]), "subject:(order OR receipt)")


class Rules(unittest.TestCase):
    def test_digest_is_stable_and_sensitive(self):
        r = {f: None for f in rulesync.RULE_FIELDS} | {"name": "A"}
        acts = [{"type": "LABEL", "label": "Receipts"}]
        self.assertEqual(rulesync.digest(r, acts), rulesync.digest(dict(r), list(acts)))
        self.assertNotEqual(rulesync.digest(r, acts), rulesync.digest(r, [{"type": "LABEL", "label": "Other"}]))


class Quota(unittest.TestCase):
    def test_costs(self):
        self.assertEqual(core._cost("GET", "/messages/x?format=metadata"), 5)
        self.assertEqual(core._cost("POST", "/messages/batchModify"), 50)
        self.assertEqual(core._cost("GET", "/labels/INBOX"), 1)

    def test_bucket_paces(self):
        b = core.Bucket()
        b.RATE, b.tokens, b.at = 200.0, 0.0, time.monotonic()
        t = time.monotonic()
        for _ in range(4):
            b.take(50)
        self.assertGreater(time.monotonic() - t, 0.8)


if __name__ == "__main__":
    unittest.main()


class FakeDb:
    """Stands in for core.db(): records inserts, answers every SELECT with no rows."""
    def __init__(self):
        self.rows = []

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        return type("R", (), {"fetchall": staticmethod(lambda: []), "fetchone": staticmethod(lambda: None)})()

    def cursor(self):
        return self

    def executemany(self, sql, rows):
        self.rows += list(rows)


class Cleared(unittest.TestCase):
    def test_sender_matches_addresses_and_domains(self):
        self.assertTrue(cleanup.sender_matches("Deals@Shop.com", "deals@shop.com"))
        self.assertFalse(cleanup.sender_matches("other@shop.com", "deals@shop.com"))
        self.assertTrue(cleanup.sender_matches("deals@mail.shop.com", "@shop.com"))
        self.assertFalse(cleanup.sender_matches("deals@notshop.com", "@shop.com"))
        self.assertFalse(cleanup.sender_matches("", "@shop.com"))

    def test_protecting_removes_only_this_senders_trash_filters(self):
        filters = [{"id": "1", "criteria": {"from": "deals@shop.com"}, "action": {"addLabelIds": ["TRASH"]}},
                   {"id": "2", "criteria": {"from": "deals@shop.com"}, "action": {"addLabelIds": ["STARRED"]}},
                   {"id": "3", "criteria": {"from": "x@other.com"}, "action": {"addLabelIds": ["TRASH"]}},
                   {"id": "4", "criteria": {"from": "news@mail.shop.com", "negatedQuery": "subject:(order)"},
                    "action": {"addLabelIds": ["TRASH"]}}]
        deleted = []

        def gmail(acc, path, method="GET", body=None):
            if method == "DELETE":
                deleted.append(path.rsplit("/", 1)[-1])
            return {"filter": filters}
        old = cleanup.gmail
        cleanup.gmail = gmail
        try:
            self.assertEqual(cleanup.remove_trash_filters("a", "deals@shop.com"), 1)
            self.assertEqual(deleted, ["1"])
            del deleted[:]
            self.assertEqual(cleanup.remove_trash_filters("a", "@shop.com"), 2)   # the domain and its subdomains
            self.assertEqual(deleted, ["1", "4"])
        finally:
            cleanup.gmail = old


class Collector(unittest.TestCase):
    def run_store(self, answers):
        """_store with Gmail answering each message id from `answers` (a dict, or an exception to raise)."""
        def gmail(acc, path, method="GET", body=None):
            mid = path.split("/messages/")[1].split("?")[0]
            a = answers[mid]
            if isinstance(a, Exception):
                raise a
            return a
        fake, old = FakeDb(), (cleanup.gmail, cleanup.db)
        cleanup.gmail, cleanup.db = gmail, fake
        try:
            return cleanup._store("acc", list(answers)), fake
        finally:
            cleanup.gmail, cleanup.db = old

    MSG = {"id": "m1", "threadId": "t", "internalDate": "1000", "labelIds": ["INBOX"], "snippet": "s",
           "payload": {"headers": [{"name": "From", "value": "A <a@x.com>"}, {"name": "Subject", "value": "hi"}]}}

    def test_a_deleted_message_is_skipped(self):
        n, fake = self.run_store({"m1": self.MSG, "gone": RuntimeError("Gmail GET /messages/gone: 404 Not Found")})
        self.assertEqual((n, len(fake.rows)), (1, 1))

    def test_a_quota_error_is_not_treated_as_deleted(self):
        # The fetched message is still stored, but the run fails, so the collector's newest/oldest marks stay where
        # they were and the unread id is listed again next time.
        with self.assertRaises(RuntimeError) as e:
            self.run_store({"m1": self.MSG, "m2": RuntimeError("Gmail GET /messages/m2: 429 Quota exceeded")})
        self.assertIn("could not be read", str(e.exception))

    def test_new_mail_pass_is_not_capped_at_2000(self):
        seen = []
        old = (cleanup.gmail_list, cleanup._store, cleanup.db)
        fake = FakeDb()
        fake.execute = lambda sql, params=None: type("R", (), {
            "fetchall": staticmethod(lambda: []),
            "fetchone": staticmethod(lambda: (1700000000, 1600000000, True) if "collect_state" in sql and "SELECT" in sql else None)})()
        cleanup.gmail_list = lambda acc, path, key, limit: seen.append((path, limit)) or []
        cleanup._store, cleanup.db = (lambda *a, **k: 0), fake
        try:
            cleanup.collect_account({"id": "acc"})
        finally:
            cleanup.gmail_list, cleanup._store, cleanup.db = old
        new_pass = [limit for path, limit in seen if "after%3A" in path]
        self.assertEqual(new_pass, [cleanup.NEW_MAIL_MAX])
        self.assertGreaterEqual(cleanup.NEW_MAIL_MAX, 100000)
