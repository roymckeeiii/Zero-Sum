"""The confirmation dialog's numbers come from the same Gmail searches the action runs: the preview and the action
share one query builder, so what is shown is what is touched."""
import os, sys, unittest, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import cleanup  # noqa: E402
from fakes import Accounts, Db, Gmail, Patched  # noqa: E402

S = "deals@shop.com"


class World(unittest.TestCase):
    def setUp(self):
        self.db, self.g, self.searches = Db(), Gmail(), []
        self.db.protected = {"@bank.com"}
        self.db.on("SELECT DISTINCT account_id FROM unified.msg", [("acc1",), ("acc2",)]).on("DISTINCT ON (account_id)", [])
        self.sizes = {"acc1": 3, "acc2": 2}

        def gmail_list(acc, path, key, limit):
            self.searches.append((acc, urllib.parse.unquote(path.split("q=", 1)[1]), limit))
            return [{"id": f"{acc}m{i}"} for i in range(min(self.sizes[acc], limit))]
        self.p = Patched(cleanup, db=self.db, gmail=self.g, ACC=Accounts("me@x.com", "two@x.com"), log=lambda *a, **k: None,
                         gmail_list=gmail_list, label_id=lambda a, name, create=True: "Label_Q", _one_click=lambda u: True)
        self.p.__enter__()
        self.addCleanup(self.p.__exit__)

    def queries(self):
        return sorted({q for _, q, _ in self.searches})


class SenderQueries(World):
    def test_each_action_searches_exactly_this(self):
        f, skip = 'from:"deals@shop.com"', "-in:sent -in:drafts -in:chats"
        want = {"delete": f"{f} -in:trash", "block": f"{f} -in:trash", "unsub_delete": f"{f} -in:trash",
                "quarantine": f"{f} -in:trash -in:spam -label:Quarantine", "unsub_quarantine": f"{f} -in:trash -in:spam -label:Quarantine",
                "archive": f"{f} in:inbox", "autoarchive": f"{f} in:inbox", "unsub_archive": f"{f} in:inbox",
                "spam": f"{f} -in:spam -in:trash", "star": f"{f} in:inbox -is:starred", "unstar": f"{f} is:starred"}
        for act, q in want.items():
            self.assertEqual(cleanup.sender_query(act, S), q, act)
            self.assertEqual(cleanup.bulk_query(cleanup.sender_query(act, S)), f"{q} {skip}")
        for act in ("unsubscribe", "keep", "reset", "protect"):
            self.assertIsNone(cleanup.sender_query(act, S), act)
        self.assertEqual(cleanup.sender_query("delete", S, "subject:(order)"), f"{f} -in:trash -subject:(order)")
        self.assertEqual(cleanup.sender_query("star", S, "subject:(order)"), f"{f} in:inbox -is:starred")   # not destructive
        self.assertEqual(cleanup.kept_query(S, "subject:(order)"), f"{f} subject:(order) -is:starred -in:trash")

    def test_the_preview_runs_the_same_searches_as_the_action(self):
        for act in sorted(cleanup.EXISTING):
            for keep in ([], ["order", "gift card"]):
                self.searches.clear()
                cleanup.preview({"addr": S, "action": act, "keep": keep})
                previewed = self.queries()
                self.searches.clear()
                cleanup.sender_action({"addr": S, "action": act, "keep": keep})
                self.assertEqual(previewed, self.queries(), (act, keep))
                self.assertTrue(previewed)

    def test_a_plan_preview_runs_the_same_searches_as_applying_it(self):
        plan = {"addr": S, "default": "quarantine", "groups": [{"label": "Orders", "words": ["order"], "action": "keep"},
                                                               {"label": "Sales", "words": ["sale", "% off"], "action": "delete"}]}
        r = cleanup.preview(plan)
        previewed = self.queries()
        self.searches.clear()
        cleanup.apply_plan(plan)
        self.assertEqual(previewed, self.queries())
        self.assertEqual([(s["what"], s["action"], s["total"]) for s in r["steps"]],
                         [("Orders", "keep", 5), ("Sales", "delete", 5), ("everything else", "quarantine", 5)])
        self.assertIn("subject:(sale OR \"% off\") -subject:(order)", previewed[1] + previewed[2] + previewed[0])


class Preview(World):
    def test_it_reports_the_real_count_per_account_and_changes_nothing(self):
        r = cleanup.preview({"addr": S, "action": "delete", "keep": ["order"]})
        main, kept = r["steps"]
        self.assertEqual((main["what"], main["total"], kept["what"], kept["total"]), ("delete", 5, "kept and starred", 5))
        self.assertEqual([(a["email"], a["count"], a["capped"]) for a in main["accounts"]],
                         [("me@x.com", 3, False), ("two@x.com", 2, False)])
        self.assertEqual(self.g.calls, [])
        self.assertEqual(self.db.said("INSERT"), [])
        self.assertEqual(self.db.said("UPDATE"), [])

    def test_a_count_at_the_limit_says_so_and_only_the_chosen_accounts_are_counted(self):
        self.sizes["acc1"] = 10 ** 6
        with Patched(cleanup, PREVIEW_MAX=50):
            r = cleanup.preview({"addr": S, "action": "block", "accounts": ["acc1"]})
        st = r["steps"][0]
        self.assertEqual((st["total"], st["capped"], [a["email"] for a in st["accounts"]]), (50, True, ["me@x.com"]))
        self.assertEqual({limit for _, _, limit in self.searches}, {50})

    def test_one_account_failing_is_shown_not_hidden(self):
        def gmail_list(acc, path, key, limit):
            if acc == "acc2":
                raise RuntimeError("Gmail GET /messages: 500 backend")
            return [{"id": "m"}]
        with Patched(cleanup, gmail_list=gmail_list):
            st = cleanup.preview({"addr": S, "action": "archive"})["steps"][0]
        self.assertEqual((st["total"], st["failed"]), (1, True))
        self.assertIn("500", st["accounts"][1]["error"])

    def test_actions_without_existing_mail_have_no_steps(self):
        self.assertEqual(cleanup.preview({"addr": S, "action": "unsubscribe"})["steps"], [])
        self.assertEqual(cleanup.preview({"addr": S, "default": "keep", "groups": []})["steps"], [])

    def test_it_is_held_to_the_same_refusals_as_the_action(self):
        for bad in ({"addr": "alerts@bank.com", "action": "delete"}, {"addr": "me@x.com", "action": "archive"},
                    {"addr": "alerts@bank.com", "default": "delete"}):
            with self.assertRaises(PermissionError, msg=bad):
                cleanup.preview(bad)
        for bad in ({"addr": "a@b.com OR x", "action": "delete"}, {"addr": S, "action": "protect"}, {"addr": S, "action": "nope"},
                    {"addr": S, "action": "delete", "keep": [f"w{i}" for i in range(11)]},
                    {"addr": S, "default": "delete", "groups": [{"words": ["a"], "action": "x"}]}):
            with self.assertRaises(ValueError, msg=bad):
                cleanup.preview(bad)
        self.assertEqual(self.searches, [])
        self.assertEqual(cleanup.preview({"addr": "alerts@bank.com", "action": "star"})["steps"][0]["total"], 5)

    def test_the_dialog_asks_the_server_instead_of_quoting_the_recent_window(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertNotIn("recent emails across", js)
        self.assertIn('api("/api/sender/preview"', js)
        import app
        self.assertIs(app.POST["/api/sender/preview"], cleanup.preview)   # the facade hands out the real function


if __name__ == "__main__":
    unittest.main()
