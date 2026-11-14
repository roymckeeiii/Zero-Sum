"""Unit tests that need no database or Gmail: token decryption (checked against Inbox Zero's Node implementation when
node is available), message parsing, unsubscribe headers, rule hashing and the Gmail rate limiter."""
import base64, json, os, shutil, subprocess, sys, time, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
        # Since the app keeps a record of the filters it creates (unified.filter), "this sender's trash filters" are
        # the recorded ones: 1, 3 and 4 below were made by the app, 2 is its star filter, 5 was made by hand in Gmail.
        filters = [{"id": "1", "criteria": {"from": "deals@shop.com"}, "action": {"addLabelIds": ["TRASH"]}},
                   {"id": "2", "criteria": {"from": "deals@shop.com"}, "action": {"addLabelIds": ["STARRED"]}},
                   {"id": "3", "criteria": {"from": "x@other.com"}, "action": {"addLabelIds": ["TRASH"]}},
                   {"id": "4", "criteria": {"from": "news@mail.shop.com", "negatedQuery": "subject:(order)"},
                    "action": {"addLabelIds": ["TRASH"]}},
                   {"id": "5", "criteria": {"from": "deals@shop.com", "hasAttachment": True},
                    "action": {"addLabelIds": ["TRASH"]}}]
        from fakes import Db
        deleted = []

        def gmail(acc, path, method="GET", body=None):
            if method == "DELETE":
                deleted.append(path.rsplit("/", 1)[-1])
            return {"filter": filters}

        def recorded():
            fake = Db()
            fake.filters = [("a", "1", "deals@shop.com", "trash", "block"), ("a", "2", "deals@shop.com", "star", "star"),
                            ("a", "3", "x@other.com", "trash", "block"), ("a", "4", "news@mail.shop.com", "trash", "block")]
            return fake
        old = (cleanup.gmail, cleanup.db)
        cleanup.gmail, cleanup.db = gmail, recorded()
        try:
            self.assertEqual(cleanup.remove_trash_filters("a", "deals@shop.com"), 1)
            self.assertEqual(deleted, ["1"])
            del deleted[:]
            cleanup.db = recorded()
            self.assertEqual(cleanup.remove_trash_filters("a", "@shop.com"), 2)   # the domain and its subdomains
            self.assertEqual(deleted, ["1", "4"])
        finally:
            cleanup.gmail, cleanup.db = old


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


class Previews(unittest.TestCase):
    def test_preview_matches_whole_words_like_the_gmail_search(self):
        for words, subject, expect in ((["order"], "Your order shipped", True), (["order"], "Reorder now", False),
                                       (["order"], "Orders update", False), (["free shipping"], "Get FREE  Shipping", True),
                                       (["50%"], "Save 50% now", True), (["order"], "pre-order: go", True),
                                       ([""], "anything", False), (["café"], "Le Café ouvre", True)):
            self.assertEqual(cleanup.subject_has(words, subject), expect, (words, subject))

    def test_the_browser_preview_uses_the_same_rule(self):
        js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.js"), encoding="utf-8").read()
        self.assertIn("function subjectHas(", js)
        self.assertNotIn("toLowerCase().includes(x)", js)       # the old substring previews are gone
        self.assertNotIn("sub.includes(w.toLowerCase())", js)


class SortPasses(unittest.TestCase):
    def test_a_newer_pass_stops_the_older_one_before_it_touches_mail(self):
        fake = FakeDb()
        fake.execute = lambda sql, params=None: type("R", (), {
            "fetchall": staticmethod(lambda: []), "fetchone": staticmethod(lambda: ("keep news", "delete"))})()
        old = (cleanup.db, cleanup.is_protected, cleanup._batch)
        touched = []
        cleanup.db, cleanup.is_protected = fake, (lambda addr, prot=None: False)
        cleanup._batch = lambda *a, **k: touched.append(a)
        mine, newer = object(), object()
        cleanup._SORT_RUNS["a@x.com"] = newer
        try:
            with self.assertRaises(RuntimeError) as e:
                cleanup._ai_sort_ids("a@x.com", "keep news", "delete", [({"id": "acc"}, "m1")], mine)
            self.assertIn("newer pass", str(e.exception))
            self.assertEqual(touched, [])
        finally:
            cleanup._SORT_RUNS.pop("a@x.com", None)
            cleanup.db, cleanup.is_protected, cleanup._batch = old


class Undo(unittest.TestCase):
    """Every bulk change is written down, with the messages it touches, before it happens; undo reverses exactly it."""

    def setUp(self):
        self.calls, self.sql = [], []
        fake = FakeDb()
        fake.execute = lambda sql, params=None: (self.sql.append((sql, params)) or type("R", (), {
            "fetchall": staticmethod(lambda: []),
            "fetchone": staticmethod(lambda: ("acc", ["TRASH"], ["INBOX"], ["m1", "m2"], None))})())
        self.old = (cleanup.db, cleanup.gmail, cleanup._store, cleanup.log)
        self.old_acc, cleanup.ACC = cleanup.ACC, type("A", (), {"email": staticmethod(lambda a: "me@x.com")})()
        cleanup.db = fake
        cleanup.gmail = lambda acc, path, method="GET", body=None: self.calls.append((path, body)) or {}
        cleanup._store, cleanup.log = (lambda *a, **k: 0), (lambda *a, **k: None)

    def tearDown(self):
        cleanup.db, cleanup.gmail, cleanup._store, cleanup.log = self.old
        cleanup.ACC = self.old_acc

    def test_a_bulk_change_is_recorded_before_gmail_is_called(self):
        cleanup._ACTION.note = "block deals@shop.com"
        order = []
        cleanup.gmail = lambda *a, **k: order.append("gmail") or {}
        real = cleanup.db.execute
        cleanup.db.execute = lambda sql, params=None: (order.append("db") if "INSERT INTO unified.action_log" in sql else None) or real(sql, params)
        self.assertEqual(cleanup._batch("acc", ["m1", "m2"], add=["TRASH"]), 2)
        self.assertEqual(order[:2], ["db", "gmail"])
        ins = [p for s, p in self.sql if "INSERT INTO unified.action_log" in s][0]
        self.assertEqual(ins, ("acc", "block deals@shop.com", ["TRASH"], [], ["m1", "m2"]))

    def test_undo_reverses_the_labels_and_is_not_recorded_again(self):
        r = cleanup.undo_action({"id": 7})
        self.assertEqual(r, {"ok": True, "restored": 2})
        path, body = self.calls[0]
        self.assertEqual((path, body), ("/messages/batchModify", {"ids": ["m1", "m2"], "addLabelIds": ["INBOX"],
                                                                 "removeLabelIds": ["TRASH"]}))
        self.assertFalse([s for s, _ in self.sql if "INSERT INTO unified.action_log" in s])
        self.assertTrue([s for s, _ in self.sql if "SET undone_at" in s])


class AccessToken(unittest.TestCase):
    """access.py: the signed Cloudflare Access token, not the plain email header, says who is signed in."""

    @classmethod
    def setUpClass(cls):
        import access, json as _json
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        cls.access = access
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()

        def token(payload, key=None, alg="RS256", kid="k1"):
            head = b64(_json.dumps({"alg": alg, "kid": kid}).encode())
            body = b64(_json.dumps(payload).encode())
            sig = (key or cls.key).sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
            return f"{head}.{body}.{b64(sig) if alg != 'none' else ''}"
        cls.token = staticmethod(token)
        cls.lookup = staticmethod(lambda kid: cls.key.public_key() if kid == "k1" else None)

    def check(self, tok):
        return self.access.email_from(tok, team="t.cloudflareaccess.com", aud="AUD1", now=1000, key_lookup=self.lookup)

    def good(self, **over):
        return {"aud": ["AUD1"], "iss": "https://t.cloudflareaccess.com", "exp": 2000, "email": "Roy@Example.com", **over}

    def test_a_valid_token_gives_its_email(self):
        self.assertEqual(self.check(self.token(self.good())), "roy@example.com")
        self.assertEqual(self.check(self.token(self.good(aud="AUD1"))), "roy@example.com")

    def test_everything_else_is_not_signed_in(self):
        bad = [self.token(self.good(), key=self.other),            # signed by someone else
               self.token(self.good(aud=["OTHER-APP"])),           # a token for another Access application
               self.token(self.good(iss="https://evil.cloudflareaccess.com")),
               self.token(self.good(exp=900)),                     # expired
               self.token(self.good(nbf=5000)),                    # not valid yet
               self.token(self.good(email="")), self.token({k: v for k, v in self.good().items() if k != "exp"}),
               self.token(self.good(), alg="none"), self.token(self.good(), kid="unknown"),
               "", None, "not.a.token", "a.b"]
        head, body, sig = self.token(self.good()).split(".")
        forged = base64.urlsafe_b64encode(json.dumps(self.good(email="attacker@x.com")).encode()).rstrip(b"=").decode()
        bad.append(f"{head}.{forged}.{sig}")                       # payload swapped under a real signature
        for tok in bad:
            self.assertIsNone(self.check(tok), str(tok)[:40])

    def test_off_unless_both_settings_are_present(self):
        self.assertFalse(self.access.ENABLED)   # the test environment sets neither: the header mode is unchanged


class BulkProtect(unittest.TestCase):
    def test_a_pasted_keep_list_becomes_protect_keys(self):
        text = ("Sender (address or domain),Notes,Added\n"
                "pleasantview.org,Nursing home (see portal.example.com),2026-11-06\n"
                "docusign.net,DocuSign — consents,2026-11-06\n"
                "Jane.Doe@Law-Firm.com , lawyer\n"
                "@sofi.com; cursor.com  databricks.com\n"
                "\n  not-a-domain  \n"
                "docusign.net\n")
        keys, rejected = cleanup.parse_protect_keys(text)
        self.assertEqual(keys, ["@pleasantview.org", "@docusign.net", "jane.doe@law-firm.com", "@sofi.com",
                                "@cursor.com", "@databricks.com"])
        self.assertNotIn("@portal.example.com", keys)     # notes are not read, so nothing in them gets protected
        self.assertEqual(rejected, ["sender", "(address", "or", "domain)", "not-a-domain"])
        for k in keys:
            self.assertTrue(cleanup.is_protected(k.lstrip("@") if "@" in k[1:] else "x" + k, set(keys)), k)

    def test_nothing_usable_is_an_error_not_a_silent_success(self):
        old = cleanup.db
        cleanup.db = FakeDb()
        try:
            with self.assertRaises(ValueError):
                cleanup.protect_many({"text": "hello there\n\n"})
        finally:
            cleanup.db = old
