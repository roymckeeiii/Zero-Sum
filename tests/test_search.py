"""RFC-001853: search every linked account at once, in every folder. A fake Gmail per account; no network or DB."""
import os, sys, unittest, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import cleanup, inbox  # noqa: E402

ACCOUNTS = [{"id": "A", "email": "a@x.com", "master": True}, {"id": "B", "email": "b@x.com", "master": False}]


class FakeAcc:
    def list(self):
        return ACCOUNTS


class FakeGmail:
    """messages: {account: [(id, ts, labelIds)]}, newest first. Records every list call's parsed query string."""

    def __init__(self, messages, fail=()):
        self.messages, self.fail, self.calls = messages, set(fail), []

    def __call__(self, acc, path, *a, **k):
        if acc in self.fail:
            raise RuntimeError("Gmail GET /messages: 401 token revoked")
        base, _, qs = path.partition("?")
        if base == "/messages":
            q = urllib.parse.parse_qs(qs)
            self.calls.append((acc, q))
            rows = self.messages.get(acc, [])
            if "INBOX" in q.get("labelIds", []):
                rows = [r for r in rows if "INBOX" in r[2]]
            term = q.get("q", [""])[0]
            if "in:sent" in term:
                rows = [r for r in rows if "SENT" in r[2]]
            if "includeSpamTrash" not in q:
                rows = [r for r in rows if not {"SPAM", "TRASH"} & set(r[2])]
            start = int(q.get("pageToken", ["0"])[0])
            n = int(q["maxResults"][0])
            out = {"messages": [{"id": r[0]} for r in rows[start:start + n]]}
            if start + n < len(rows):
                out["nextPageToken"] = str(start + n)
            return out
        mid = base.rsplit("/", 1)[1]
        r = next(r for r in self.messages[acc] if r[0] == mid)
        return {"id": r[0], "threadId": "t" + r[0], "internalDate": str(r[1] * 1000), "labelIds": r[2], "snippet": "",
                "payload": {"headers": [{"name": "From", "value": "x@y.com"}, {"name": "Subject", "value": mid}]}}


class Search(unittest.TestCase):
    def setUp(self):
        saved = {(inbox, "ACC"): inbox.ACC, (inbox, "gmail"): inbox.gmail, (inbox, "labels"): inbox.labels,
                 (inbox, "rules_for"): inbox.rules_for, (cleanup, "protected_set"): cleanup.protected_set}
        self.addCleanup(lambda: [setattr(m, n, v) for (m, n), v in saved.items()])
        inbox.ACC, inbox.labels = FakeAcc(), lambda acc: {}
        inbox.rules_for, cleanup.protected_set = lambda items: {}, lambda: set()
        inbox._meta.clear()

    def run_search(self, fake, **params):
        inbox.gmail = fake
        return inbox.messages({k: [str(v)] for k, v in params.items()})

    def test_sent_in_one_account_and_archived_in_another_both_found_in_date_order(self):
        fake = FakeGmail({"A": [("a1", 300, ["SENT"]), ("a0", 100, ["INBOX"])],
                          "B": [("b1", 200, ["Label_1"]), ("b0", 50, ["INBOX"])]})
        d = self.run_search(fake, q="invoice", scope="all")
        self.assertEqual([i["id"] for i in d["items"]], ["a1", "b1", "a0", "b0"])
        got = {i["id"]: (i["email"], i["folder"]) for i in d["items"]}
        self.assertEqual(got["a1"], ("a@x.com", "Sent"))
        self.assertEqual(got["b1"], ("b@x.com", "Archived"))
        for acc, q in fake.calls:
            self.assertNotIn("labelIds", q)
            self.assertEqual(q["q"], ["invoice"])  # the user's query passes through unchanged

    def test_inbox_scope_is_todays_behaviour(self):
        fake = FakeGmail({"A": [("a1", 300, ["SENT"]), ("a0", 100, ["INBOX"])], "B": [("b0", 50, ["INBOX"])]})
        d = self.run_search(fake, q="from:boss has:attachment", limit=30)  # no scope = inbox
        self.assertEqual([i["id"] for i in d["items"]], ["a0", "b0"])
        for _, q in fake.calls:
            self.assertEqual(q["labelIds"], ["INBOX"])
            self.assertEqual(q["maxResults"], ["30"])
            self.assertEqual(q["q"], ["from:boss has:attachment"])

    def test_sent_scope_returns_only_sent(self):
        fake = FakeGmail({"A": [("a1", 300, ["SENT"]), ("a0", 100, ["INBOX"])], "B": [("b2", 400, ["SENT", "INBOX"])]})
        d = self.run_search(fake, scope="sent")
        self.assertEqual([i["id"] for i in d["items"]], ["b2", "a1"])
        self.assertTrue(all(q["q"] == ["in:sent"] for _, q in fake.calls))

    def test_scope_to_query_translation(self):
        self.assertEqual(inbox.scope_query("inbox"), ("", "&labelIds=INBOX"))
        self.assertEqual(inbox.scope_query("all"), ("", ""))
        self.assertEqual(inbox.scope_query("sent"), ("in:sent", ""))
        self.assertEqual(inbox.scope_query("spamtrash"), ("{in:spam in:trash}", "&includeSpamTrash=true"))
        self.assertEqual(inbox.scope_query("label", "Receipts/2026 Tax"), ("label:receipts-2026-tax", ""))
        self.assertEqual(inbox.scope_query("label", 'x" OR in:anywhere'), ("label:x-or-inanywhere", ""))  # no operator smuggled in
        for bad in (("label", "  "), ("label", '"'), ("everything", "")):
            with self.assertRaises(ValueError):
                inbox.scope_query(*bad)

    def test_spam_and_trash_only_with_their_scope(self):
        msgs = {"A": [("a1", 300, ["TRASH"]), ("a0", 100, ["SPAM"]), ("a2", 50, ["INBOX"])], "B": []}
        self.assertEqual([i["id"] for i in self.run_search(FakeGmail(msgs), scope="all")["items"]], ["a2"])
        d = self.run_search(FakeGmail(msgs), scope="spamtrash")
        self.assertEqual({i["id"]: i["folder"] for i in d["items"] if i["id"] != "a2"}, {"a1": "Trash", "a0": "Spam"})

    def test_failing_account_is_isolated(self):
        fake = FakeGmail({"A": [("a1", 300, ["SENT"])], "B": [("b1", 200, ["INBOX"])]}, fail={"B"})
        d = self.run_search(fake, scope="all")
        self.assertEqual([i["id"] for i in d["items"]], ["a1"])
        self.assertEqual([e["email"] for e in d["errors"]], ["b@x.com"])
        self.assertIn("401", d["errors"][0]["error"])

    def test_paging_continues_each_account_where_it_stopped(self):
        msgs = {"A": [(f"a{i:02d}", 1000 - i, ["SENT"]) for i in range(25)],
                "B": [(f"b{i:02d}", 500 - i, ["Label_1"]) for i in range(12)]}
        fake = FakeGmail(msgs)
        d1 = self.run_search(fake, scope="all", limit=20)  # 20 split over 2 accounts, at least 10 each
        self.assertEqual(len(fake.calls), 2)  # one list call per account per page
        self.assertTrue(all(q["maxResults"] == ["10"] for _, q in fake.calls))
        self.assertEqual(len(d1["items"]), 20)
        page = inbox.decode_page(d1["next"])
        self.assertEqual(page, {"A": "10", "B": "10"})
        fake.calls.clear()
        d2 = self.run_search(fake, scope="all", limit=20, page=d1["next"])
        self.assertEqual(sorted(a for a, _ in fake.calls), ["A", "B"])
        self.assertEqual(len(d2["items"]), 12)  # A 10-19, B 10-11
        self.assertEqual(inbox.decode_page(d2["next"]), {"A": "20"})  # B is exhausted
        fake.calls.clear()
        d3 = self.run_search(fake, scope="all", limit=20, page=d2["next"])
        self.assertEqual([a for a, _ in fake.calls], ["A"])  # only the account that still has results
        self.assertEqual([i["id"] for i in d3["items"]], [f"a{i:02d}" for i in range(20, 25)])
        self.assertEqual(d3["next"], "")
        ids = [i["id"] for d in (d1, d2, d3) for i in d["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 37)

    def test_bad_page_token_is_refused(self):
        for bad in ("!!!", inbox.encode_page({"A": "1"})[:-3] + "xyz", "WzFd"):  # WzFd = base64 of a JSON list
            with self.assertRaises(ValueError):
                inbox.decode_page(bad)


if __name__ == "__main__":
    unittest.main()
