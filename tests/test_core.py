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
            ai.KEY, ai._ask = "k", lambda prompt: {"keep": [1, "3", "x"]}
            self.assertEqual(ai.classify("keep articles", [("a", ""), ("b", ""), ("c", "")]), [True, False, True])
        finally:
            ai.KEY, ai._ask = old_key, old_ask


class Rules(unittest.TestCase):
    def test_digest_is_stable_and_sensitive(self):
        r = {f: None for f in rulesync.RULE_FIELDS} | {"name": "A"}
        acts = [{"type": "LABEL", "label": "Receipts"}]
        self.assertEqual(rulesync.digest(r, acts), rulesync.digest(dict(r), list(acts)))
        self.assertNotEqual(rulesync.digest(r, acts), rulesync.digest(r, [{"type": "LABEL", "label": "Other"}]))


class FakeCursor:
    """Records SQL; enough for rulesync._write_rule (no database)."""
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self


class RulesV230(unittest.TestCase):
    def test_copied_actions_carry_the_target_account_id(self):
        r = {f: None for f in rulesync.RULE_FIELDS} | {"name": "A", "enabled": True}
        acts = [{f: None for f in rulesync.ACTION_FIELDS} | {"type": "ARCHIVE"},
                {f: None for f in rulesync.ACTION_FIELDS} | {"type": "STAR"}]
        c = FakeCursor()
        rulesync._write_rule(c, "rule-t", "acc-t", r, acts, insert=True)
        inserts = [(s, p) for s, p in c.calls if s.startswith('INSERT INTO "Action"')]
        self.assertEqual(len(inserts), 2)
        for sql, params in inserts:
            cols = sql.split("(", 1)[1].split(")", 1)[0].replace('"', "").split(", ")
            self.assertIn("emailAccountId", cols)
            vals = dict(zip(cols[:1] + cols[2:], [params[0], *params[1:]]))  # updatedAt is now(), not a param
            self.assertEqual(vals["ruleId"], "rule-t")
            self.assertEqual(vals["emailAccountId"], "acc-t")
            self.assertEqual(sql.count("%s"), len(params))
        self.assertEqual([p[-len(rulesync.ACTION_FIELDS)] for _, p in inserts], ["ARCHIVE", "STAR"])

    def test_messaging_channel_actions_are_not_copied(self):
        old = rulesync.labels
        rulesync.labels = lambda acc: {"L1": "Receipts"}
        try:
            r = {"actions": [{"type": "LABEL", "label": None, "labelId": "L1"},
                             {"type": "NOTIFY_MESSAGING_CHANNEL", "label": None, "labelId": None},
                             {"type": "DRAFT_MESSAGING_CHANNEL", "label": None, "labelId": None},
                             {"type": "STAR", "label": None, "labelId": None}]}
            out = rulesync.master_actions("master", r)
        finally:
            rulesync.labels = old
        self.assertEqual([a["type"] for a in out], ["LABEL", "STAR"])
        self.assertEqual(out[0]["label"], "Receipts")
        self.assertNotIn("labelId", out[0])

    def test_supported_version_is_v2_30_0(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(open(os.path.join(here, "inbox-zero.version")).read().strip(), "v2.30.0")


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
