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

    def __init__(self, messages, fail=(), meta_fail=(), gone=()):
        self.messages, self.fail, self.calls = messages, set(fail), []
        self.meta_fail, self.gone = set(meta_fail), set(gone)

    def __call__(self, acc, path, *a, **k):
        if acc in self.fail:
            raise RuntimeError("Gmail GET /messages: 401 token revoked")
        if path.startswith("/messages/") and acc in self.meta_fail:
            raise RuntimeError("Gmail GET /messages/x: 401 token revoked")
        if path.startswith("/messages/") and path.split("/")[2].split("?")[0] in self.gone:
            raise RuntimeError("Gmail GET /messages/x: 404 Not Found")
        base, _, qs = path.partition("?")
        if base == "/messages":
            q = urllib.parse.parse_qs(qs)
            self.calls.append((acc, q))
            rows = self.messages.get(acc, [])
            for lid in q.get("labelIds", []):
                rows = [r for r in rows if lid in r[2]]
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
                "payload": {"headers": [{"name": "From", "value": "x@y.com"}, {"name": "Subject", "value": mid},
                                        {"name": "To", "value": f"Pat Doe <pat@z.com>, ops@z.com, {mid}@z.com"}]}}


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
            self.assertEqual(q["maxResults"], ["15"])   # capped token paging: 30 // 2 accounts (RFC-001886)
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
        self.assertEqual(inbox.scope_query("label", "Receipts/2026 Tax"), ("", ""))   # resolved per account to an id
        self.assertEqual(inbox.label_term('x" OR in:anywhere'), "label:x-or-inanywhere")  # no operator smuggled in
        with self.assertRaises(ValueError):
            inbox.label_term('"')
        for bad in (("label", "  "), ("everything", "")):
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

    def test_paging_is_globally_ordered_capped_and_complete(self):
        # RFC-001885 final review: A's mail is all newer than B's from #10 on; per-account pages used to show B's old
        # mail before A's newer second page. Now every page is newest-first across accounts and pages never overlap.
        msgs = {"A": [(f"a{i:02d}", 1000 - i, ["SENT"]) for i in range(25)],
                "B": [(f"b{i:02d}", 995 - 50 * i, ["Label_1"]) for i in range(12)]}
        fake = FakeGmail(msgs)
        seen, page, pages = [], "", 0
        while True:
            fake.calls.clear()
            d = self.run_search(fake, scope="all", limit=20, **({"page": page} if page else {}))
            pages += 1
            self.assertLessEqual(len(d["items"]), 20)
            self.assertLessEqual(len(fake.calls), 2)                      # <= one list call per account per page
            seen += [(i["ts"], i["id"]) for i in d["items"]]
            page = d["next"]
            if not page or pages > 10:
                break
        self.assertEqual(len(seen), 37)
        self.assertEqual(len({x[1] for x in seen}), 37)                   # every message exactly once
        self.assertEqual([x[0] for x in seen], sorted((x[0] for x in seen), reverse=True))   # global order

    def test_held_back_ids_are_shown_on_the_next_page(self):
        msgs = {"A": [(f"a{i:02d}", 1000 - i, ["SENT"]) for i in range(10)],
                "B": [(f"b{i:02d}", 100 - i, ["SENT"]) for i in range(30)]}
        fake = FakeGmail(msgs)
        d1 = self.run_search(fake, scope="sent", limit=20)
        self.assertEqual([i["id"] for i in d1["items"]][:10], [f"a{i:02d}" for i in range(10)])
        nxt = inbox.decode_page(d1["next"])
        self.assertEqual(nxt["B"]["t"], "10")
        self.assertNotIn("A", nxt)                                        # A is exhausted and fully shown
        self.assertTrue(all(i["id"].startswith("b") for i in self.run_search(fake, scope="sent", limit=20, page=d1["next"])["items"]))

    def test_many_accounts_page_cap_and_every_message_exactly_once(self):
        global ACCOUNTS
        saved = ACCOUNTS
        self.addCleanup(lambda: globals().__setitem__("ACCOUNTS", saved))
        ACCOUNTS = [{"id": f"K{k}", "email": f"k{k}@x.com", "master": k == 0} for k in range(7)]
        msgs = {f"K{k}": [(f"k{k}-{i:02d}", 10000 - 37 * i - k, ["SENT" if i % 2 else "Label_1"]) for i in range(9 + k)]
                for k in range(7)}
        total = sum(len(v) for v in msgs.values())
        fake, seen, page, pages = FakeGmail(msgs), [], "", 0
        while True:
            fake.calls.clear()
            d = self.run_search(fake, scope="all", limit=30, **({"page": page} if page else {}))
            pages += 1
            self.assertLessEqual(len(d["items"]), 30)
            per = {int(q["maxResults"][0]) for _, q in fake.calls}
            self.assertEqual(per, {30 // len(fake.calls)})  # 30 split over the accounts still searched (4 for 7)
            self.assertLessEqual(sum(int(q["maxResults"][0]) for _, q in fake.calls), 30)
            self.assertLessEqual(len(fake.calls), 7)  # one list call per account per page
            seen += [i["id"] for i in d["items"]]
            page = d["next"]
            if not page:
                break
        self.assertEqual(len(seen), total)
        self.assertEqual(sorted(seen), sorted(r[0] for v in msgs.values() for r in v))  # each exactly once
        self.assertGreater(pages, 1)

    def test_search_page_is_capped_even_after_inbox_load_more(self):
        msgs = {"A": [(f"a{i:03d}", 1000 - i, ["SENT"]) for i in range(120)], "B": []}
        fake = FakeGmail(msgs)
        d = self.run_search(fake, scope="sent", limit=200)  # the inbox's Load more can leave limit at 200
        self.assertEqual(len(d["items"]), 25)  # 50 // 2 accounts
        self.assertTrue(all(q["maxResults"] == ["25"] for _, q in fake.calls))

    def test_label_scope_with_unread_end_to_end(self):
        fake = FakeGmail({"A": [("a1", 300, ["Label_1"])], "B": [("b1", 200, ["Label_9"])]})
        inbox.labels = lambda acc: {"A": {"Label_1": "Receipts 2026"}, "B": {"Label_9": "Receipts 2026"}}[acc]
        d = self.run_search(fake, scope="label", label="Receipts 2026", q="from:shop", unread=1)
        self.assertEqual(len(d["items"]), 2)
        self.assertTrue(all(q["q"] == ["from:shop is:unread"] for _, q in fake.calls))
        self.assertEqual(sorted(q["labelIds"][0] for _, q in fake.calls), ["Label_1", "Label_9"])  # each account's own id
        with self.assertRaises(ValueError):  # app.py turns ValueError into a 400
            self.run_search(fake, scope="label", label="")

    # RFC-001886 final review (1): Inbox searches page with capped per-account tokens like every other scope
    def test_inbox_pages_with_capped_tokens_and_load_more_continues(self):
        msgs = {"A": [(f"a{i:03d}", 1000 - 2 * i, ["INBOX"]) for i in range(60)],
                "B": [(f"b{i:03d}", 999 - 2 * i, ["INBOX"]) for i in range(60)]}
        fake = FakeGmail(msgs)
        d = self.run_search(fake, limit=200)          # no scope = inbox; an old client's big limit is capped
        self.assertLessEqual(len(d["items"]), inbox.SEARCH_PAGE_MAX)
        self.assertTrue(all(q["maxResults"] == ["25"] for _, q in fake.calls))
        self.assertTrue(d["next"])
        seen, page = [i["id"] for i in d["items"]], d["next"]
        while page:
            d = self.run_search(fake, limit=30, page=page)
            seen += [i["id"] for i in d["items"]]
            page = d["next"]
        self.assertEqual(len(seen), 120)
        self.assertEqual(len(set(seen)), 120)            # nothing repeated, nothing lost
        ts = {r[0]: r[1] for rows in msgs.values() for r in rows}
        self.assertEqual(seen, sorted(seen, key=lambda m: -ts[m]))   # one global newest-first order across pages

    # RFC-001886 final review (2): distinct label names whose Gmail search forms collide are not merged
    def test_label_names_that_slug_alike_stay_distinct(self):
        fake = FakeGmail({"A": [("a1", 300, ["Label_1"]), ("a2", 200, ["Label_2"])], "B": [("b1", 100, ["Label_2"])]})
        inbox.labels = lambda acc: {"A": {"Label_1": "Work/2026", "Label_2": "Work-2026"},
                                    "B": {"Label_2": "Work-2026"}}[acc]
        self.assertEqual(inbox.label_term("Work/2026"), inbox.label_term("Work-2026"))   # the old collision
        d = self.run_search(fake, scope="label", label="Work/2026")
        self.assertEqual([i["id"] for i in d["items"]], ["a1"])
        self.assertEqual([acc for acc, _ in fake.calls], ["A"])    # B has no "Work/2026": not searched at all
        self.assertEqual(d["errors"], [])
        d = self.run_search(fake, scope="label", label="work-2026")    # Gmail names are case-insensitive
        self.assertEqual(sorted(i["id"] for i in d["items"]), ["a2", "b1"])

    # RFC-001886 final review (3): an auth failure while fetching message metadata is reported for that account
    def test_metadata_auth_failure_is_reported_per_account_and_404_is_not(self):
        fake = FakeGmail({"A": [("a1", 300, ["SENT"])], "B": [("b1", 200, ["SENT"]), ("b2", 100, ["SENT"])]},
                         meta_fail={"A"}, gone={"b2"})
        d = self.run_search(fake, scope="sent")
        self.assertEqual([i["id"] for i in d["items"]], ["b1"])
        self.assertEqual([e["email"] for e in d["errors"]], ["a@x.com"])
        self.assertIn("401", d["errors"][0]["error"])

    def test_a_label_lookup_failure_is_reported_and_other_accounts_answer(self):
        fake = FakeGmail({"A": [("a1", 300, ["Label_1"])], "B": [("b1", 200, ["Label_1"])]})

        def labels(acc):
            if acc == "A":
                raise PermissionError("Google refused the login for a@x.com (401)")
            return {"Label_1": "Receipts"}
        inbox.labels = labels
        d = self.run_search(fake, scope="label", label="Receipts")
        self.assertEqual([i["id"] for i in d["items"]], ["b1"])
        self.assertEqual([e["email"] for e in d["errors"]], ["a@x.com"])

    def test_results_carry_recipients_for_sent_mail(self):
        fake = FakeGmail({"A": [("a1", 300, ["SENT"])], "B": []})
        (it,) = self.run_search(fake, scope="sent")["items"]
        self.assertEqual((it["folder"], it["to"]), ("Sent", "Pat Doe, ops@z.com, a1@z.com"))
        self.assertEqual(inbox.recipients("a@x.com, b@x.com, C <c@x.com>, d@x.com, e@x.com"), "a@x.com, b@x.com, C +2")
        self.assertEqual(inbox.recipients(""), "")

    def test_bad_page_token_is_refused(self):
        for bad in ("!!!", inbox.encode_page({"A": {"t": "1", "b": []}})[:-3] + "xyz", "WzFd",
                    inbox.encode_page({"A": "1"}), inbox.encode_page({"A": {"t": 1}})):  # old/odd shapes refused
            with self.assertRaises(ValueError):
                inbox.decode_page(bad)


if __name__ == "__main__":
    unittest.main()
