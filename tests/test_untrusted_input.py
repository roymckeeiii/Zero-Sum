"""Input that comes from strangers or from the request: the List-Unsubscribe URL the app POSTs to, and sender
addresses that end up in Gmail searches."""
import os, socket, sys, unittest, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import ai, cleanup  # noqa: E402
from fakes import Accounts, Db, Gmail, Patched  # noqa: E402


def dns(table):
    def getaddrinfo(host, port, *a, **k):
        if host not in table:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in table[host]]
    return getaddrinfo


class Conn:
    made = []

    def __init__(self, host, ip, timeout):
        self.host, self.ip, self.timeout, self.sent = host, ip, timeout, None
        Conn.made.append(self)

    def request(self, method, path, body=None, headers=None):
        self.sent = (method, path, body)

    def getresponse(self):
        return type("R", (), {"status": Conn.status})()

    def close(self):
        pass


class OneClick(unittest.TestCase):
    DNS = {"news.shop.com": ["93.184.216.34"], "inside.evil.com": ["10.0.0.5"], "meta.evil.com": ["169.254.169.254"],
           "loop.evil.com": ["127.0.0.1"], "mixed.evil.com": ["93.184.216.34", "192.168.1.1"], "v6.evil.com": ["::1"],
           "mapped.evil.com": ["::ffff:127.0.0.1"], "ula.evil.com": ["fd00::1"], "cgn.evil.com": ["100.64.0.1"],
           "zero.evil.com": ["0.0.0.0"], "v6ok.shop.com": ["2606:2800:220:1:248:1893:25c8:1946"]}

    def setUp(self):
        Conn.made, Conn.status = [], 200
        for mod, names in ((cleanup.socket, dict(getaddrinfo=dns(self.DNS))), (cleanup, dict(_Pinned=Conn))):
            p = Patched(mod, **names)
            p.__enter__()
            self.addCleanup(p.__exit__)

    def test_a_public_https_link_is_posted_to_the_checked_address(self):
        self.assertTrue(cleanup._one_click("https://news.shop.com/u?id=1"))
        c = Conn.made[0]
        self.assertEqual((c.host, c.ip, c.sent), ("news.shop.com", "93.184.216.34", ("POST", "/u?id=1", b"List-Unsubscribe=One-Click")))
        self.assertLessEqual(c.timeout, 10)
        self.assertTrue(cleanup._one_click("https://v6ok.shop.com/u"))

    def test_private_loopback_link_local_and_metadata_addresses_are_refused(self):
        for url in ("https://inside.evil.com/u", "https://meta.evil.com/latest/meta-data/", "https://loop.evil.com/",
                    "https://mixed.evil.com/u", "https://v6.evil.com/u", "https://mapped.evil.com/u", "https://ula.evil.com/u",
                    "https://cgn.evil.com/u", "https://zero.evil.com/u", "https://127.0.0.1/u", "https://[::1]/u",
                    "https://169.254.169.254/u", "https://10.1.2.3/u", "https://nowhere.invalid/u"):
            with self.assertRaises(ValueError, msg=url):
                cleanup.unsub_target(url)
            self.assertFalse(cleanup._one_click(url), url)
        self.assertEqual(Conn.made, [])                           # nothing was even connected to

    def test_only_plain_https_on_443(self):
        for url in ("http://news.shop.com/u", "ftp://news.shop.com/u", "file:///etc/passwd", "mailto:u@shop.com",
                    "https://news.shop.com:8443/u", "https://user:pw@news.shop.com/u", "https:///u", "", None, "gopher://x"):
            with self.assertRaises(ValueError, msg=url):
                cleanup.unsub_target(url)
        self.assertEqual(Conn.made, [])

    def test_a_redirect_is_not_followed_and_not_counted_as_unsubscribed(self):
        for status in (301, 302, 303, 307, 308, 404, 500):
            Conn.status = status
            self.assertFalse(cleanup._one_click("https://news.shop.com/u"), status)
        self.assertEqual(len(Conn.made), 7)                       # one request each: never a second hop

    def test_the_real_connection_is_plain_http_client_which_has_no_redirect_handling(self):
        import http.client
        with open(cleanup.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("class _Pinned(http.client.HTTPSConnection)", src)
        self.assertNotIn("urlopen(", src.split("def _one_click(url):")[1].split("\n\n\n")[0])
        self.assertTrue(issubclass(http.client.HTTPSConnection, http.client.HTTPConnection))


class SenderAddresses(unittest.TestCase):
    BAD = ["a@b.com OR from:boss@work.com", "a@b.com)", "{a@b.com", "a@b.com|c@d.com", '"a"@b.com', "a@b", "@b.com", "a b@c.com",
           "a@b.com -in:inbox", "a:b@c.com", "a@b.com,c@d.com", "a@(b).com", "", None, "x" * 70 + "@b.com", "unknown"]

    def test_only_plain_addresses_are_accepted(self):
        for ok in ("deals@shop.com", "First.Last+tag@mail.shop.co.uk", "o'brien@x.org", "A_B-c@X.io"):
            self.assertEqual(cleanup.sender_addr(ok), ok.lower())
        for bad in self.BAD:
            with self.assertRaises(ValueError, msg=bad):
                cleanup.sender_addr(bad)

    def test_the_search_term_is_quoted_and_revalidated(self):
        self.assertEqual(cleanup.frm("Deals@Shop.com"), 'from:"deals@shop.com"')
        with self.assertRaises(ValueError):
            cleanup.frm("a@b.com OR in:inbox")

    def test_every_entry_point_refuses_them_before_gmail_is_asked(self):
        db, g = Db(), Gmail()
        with Patched(cleanup, db=db, gmail=g, ACC=Accounts("me@x.com"), log=lambda *a, **k: None,
                     gmail_list=lambda *a: self.fail("Gmail was searched")), Patched(ai, KEY="k", db=db):
            for bad in self.BAD:
                for call in (lambda: cleanup.sender_action({"addr": bad, "action": "delete"}),
                             lambda: cleanup.sender_action({"addr": bad, "action": "star"}),
                             lambda: cleanup.apply_plan({"addr": bad, "default": "delete"}),
                             lambda: cleanup.sender_recent({"addr": [bad or ""]}),
                             lambda: ai.suggest({"addr": bad})):
                    with self.assertRaises(ValueError, msg=bad):
                        call()
                if bad:
                    with self.assertRaises(ValueError, msg=bad):
                        cleanup.quarantine_action({"action": "release", "sender": bad})
            for bad in ("a@b.com OR x", "@(b).com", "a@b", "{x}@b.com"):
                with self.assertRaises(ValueError, msg=bad):
                    cleanup.sender_action({"addr": bad, "action": "protect"})
        self.assertEqual(g.calls, [])

    def test_searches_carry_the_quoted_term(self):
        db, g, seen = Db(), Gmail(), []
        db.on("SELECT DISTINCT account_id FROM unified.msg", [("acc1",)]).on("DISTINCT ON (account_id)", [])
        with Patched(cleanup, db=db, gmail=g, ACC=Accounts("me@x.com"), log=lambda *a, **k: None,
                     label_id=lambda *a, **k: "Label_Q",
                     gmail_list=lambda a, path, key, limit: seen.append(urllib.parse.unquote(path)) or []):
            for act in ("delete", "block", "archive", "quarantine", "spam", "star", "unstar"):
                cleanup.sender_action({"addr": "deals@shop.com", "action": act, "keep": ["order"]})
            cleanup.apply_plan({"addr": "deals@shop.com", "default": "archive"})
            cleanup.sender_recent({"addr": ["deals@shop.com"]})
        self.assertTrue(seen)
        for q in seen:
            self.assertIn('from:"deals@shop.com"', q, q)


if __name__ == "__main__":
    unittest.main()
