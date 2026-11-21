"""core.Accounts: which mailboxes exist for the app (tenant scope) and how a Google access token is obtained."""
import datetime, io, json, os, sys, time, unittest, urllib.error, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import core  # noqa: E402
from fakes import Db, Patched  # noqa: E402


class Scope(unittest.TestCase):
    def test_default_is_unchanged_every_google_mailbox(self):
        sql, params = core.accounts_query([])
        self.assertEqual(params, ())
        self.assertNotIn("userId", sql)
        self.assertNotIn('"User"', sql)
        self.assertIn("WHERE a.provider = 'google' ORDER BY ea.email", sql)

    def test_owners_are_applied_in_that_one_query_by_id_or_login_email(self):
        sql, params = core.accounts_query(["cuser123", "Roy@Example.com"])
        self.assertIn('ea."userId" = ANY(%s)', sql)
        self.assertIn('lower(u.email) = ANY(%s)', sql)
        self.assertEqual(params, (["cuser123"], ["roy@example.com"]))
        self.assertEqual(sql.count("%s"), 2)                       # values are parameters, never pasted into the SQL

    def test_the_account_list_uses_it_and_a_failing_scoped_query_gives_no_accounts(self):
        db = Db()
        db.on('FROM "EmailAccount"', [("a1", "me@x.com", "Me", None, None, None)])
        with Patched(core, db=db, OWNERS=["roy@example.com"]):
            acc = core.Accounts()
            self.assertEqual([a["email"] for a in acc.list()], ["me@x.com"])
            self.assertEqual(db.said('FROM "EmailAccount"')[0][1], ([], ["roy@example.com"]))

        def broken(params):
            raise RuntimeError('column ea.userId does not exist')
        db.on('FROM "EmailAccount"', broken)
        with Patched(core, db=db, OWNERS=["roy@example.com"]):
            with self.assertRaises(RuntimeError):                  # never a silent fall-back to every mailbox
                core.Accounts().list()


def encrypted(text):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(16)
    ct = AESGCM(core.key()).encrypt(iv, text.encode(), None)
    return (iv + ct[-16:] + ct[:-16]).hex()


class Token(unittest.TestCase):
    def setUp(self):
        self.posts, self.answer = [], {"access_token": "ya29.fresh", "expires_in": 3600}

        def urlopen(url, body=None, timeout=None):
            self.posts.append((url, urllib.parse.parse_qs(body.decode())))
            if isinstance(self.answer, Exception):
                raise self.answer
            return io.BytesIO(json.dumps(self.answer).encode())
        os.environ.update(GOOGLE_CLIENT_ID="cid", GOOGLE_CLIENT_SECRET="csecret")
        p = Patched(core.urllib.request, urlopen=urlopen)
        p.__enter__()
        self.addCleanup(p.__exit__)
        self.acc = core.Accounts()
        self.acc.at = time.time()                                  # rows are "fresh": no database read

    def row(self, expires_in, access="ya29.stored", refresh="1//refresh"):
        exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires_in)
        self.acc.rows = [{"id": "a1", "email": "me@x.com", "name": "Me", "access": encrypted(access) if access else None,
                          "refresh": encrypted(refresh) if refresh else None, "expires": exp}]

    def test_a_stored_token_that_is_still_valid_is_used_without_asking_google(self):
        self.row(3600)
        self.assertEqual(self.acc.token("a1"), "ya29.stored")
        self.assertEqual(self.posts, [])

    def test_an_expiring_token_is_refreshed_with_the_refresh_token_and_kept_in_memory_only(self):
        self.row(60)
        self.assertEqual(self.acc.token("a1"), "ya29.fresh")
        url, form = self.posts[0]
        self.assertEqual(url, "https://oauth2.googleapis.com/token")
        self.assertEqual(form, {"client_id": ["cid"], "client_secret": ["csecret"], "refresh_token": ["1//refresh"],
                                "grant_type": ["refresh_token"]})
        self.assertEqual(self.acc.token("a1"), "ya29.fresh")       # cached: no second request
        self.assertEqual(len(self.posts), 1)

    def test_force_refreshes_even_a_valid_token(self):
        self.row(3600)
        self.assertEqual(self.acc.token("a1", force=True), "ya29.fresh")
        self.assertEqual(len(self.posts), 1)

    def test_a_cached_token_about_to_expire_is_refreshed(self):
        self.row(60)
        self.answer = {"access_token": "ya29.short", "expires_in": 30}
        self.assertEqual(self.acc.token("a1"), "ya29.short")
        self.answer = {"access_token": "ya29.next", "expires_in": 3600}
        self.assertEqual(self.acc.token("a1"), "ya29.next")

    def test_no_usable_refresh_token_is_a_clear_refusal(self):
        self.row(60, refresh=None)
        with self.assertRaises(PermissionError) as e:
            self.acc.token("a1")
        self.assertIn("re-link", str(e.exception))
        self.assertEqual(self.posts, [])

    def test_google_refusing_the_refresh_is_a_refusal_that_does_not_leak_the_token(self):
        self.row(60)
        self.answer = urllib.error.HTTPError("u", 400, "invalid_grant", None, io.BytesIO(b"{}"))
        with self.assertRaises(PermissionError) as e:
            self.acc.token("a1")
        self.assertIn("400", str(e.exception))
        self.assertNotIn("1//refresh", str(e.exception))

    def test_an_unknown_account_is_refused(self):
        self.acc.rows = []
        with self.assertRaises(KeyError):
            self.acc.token("nope")


if __name__ == "__main__":
    unittest.main()
