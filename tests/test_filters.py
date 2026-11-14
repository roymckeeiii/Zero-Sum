"""Gmail filters are the automation state: every filter the app creates is on record (unified.filter), and Protect,
Keep, Reset and a re-applied plan remove exactly the recorded ones, never a filter made by hand in Gmail."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import cleanup  # noqa: E402
from fakes import Accounts, Db, Gmail, Patched  # noqa: E402

S = "deals@shop.com"
HAND_MADE = {"id": "H1", "criteria": {"from": S}, "action": {"addLabelIds": ["TRASH"]}}


class World(unittest.TestCase):
    """cleanup.* wired to an in-memory database and Gmail with two accounts that both get mail from deals@shop.com."""

    def setUp(self):
        self.db, self.g, self.acc = Db(), Gmail(), Accounts("me@x.com", "two@x.com")
        self.db.on("SELECT DISTINCT account_id FROM unified.msg", [("acc1",), ("acc2",)]).on("DISTINCT ON (account_id)", [])
        self.p = Patched(cleanup, db=self.db, gmail=self.g, ACC=self.acc, log=lambda *a, **k: None,
                         gmail_list=lambda a, path, key, limit: self.g(a, path).get(key, []),
                         label_id=lambda a, name, create=True: "Label_Q")
        self.p.__enter__()
        self.addCleanup(self.p.__exit__)

    def kinds(self, acc="acc1"):
        return sorted(f[3] for f in self.db.filters if f[0] == acc)

    def live(self, acc="acc1"):
        return [f["id"] for f in self.g.filters.get(acc, [])]


class Recorded(World):
    def test_every_filter_the_app_creates_is_recorded(self):
        for act, kinds in (("block", ["trash"]), ("autoarchive", ["archive"]), ("quarantine", ["quarantine"]),
                           ("unsub_delete", ["trash"]), ("star", ["star"])):
            self.db.filters, self.g.filters = [], {}
            r = cleanup.sender_action({"addr": S, "action": act})
            self.assertTrue(all(x["ok"] for x in r["results"]), r)
            for acc in ("acc1", "acc2"):
                self.assertEqual(self.kinds(acc), kinds, act)
                self.assertEqual(sorted(f[1] for f in self.db.filters if f[0] == acc), sorted(self.live(acc)), act)

    def test_keep_words_record_the_trash_filter_and_its_star_filter(self):
        cleanup.sender_action({"addr": S, "action": "block", "keep": ["order"]})
        self.assertEqual(self.kinds(), ["keep_star", "trash"])
        crit = {f["id"]: f["criteria"] for f in self.g.filters["acc1"]}
        self.assertIn({"from": S, "negatedQuery": "subject:(order)"}, crit.values())
        self.assertIn({"from": S, "query": "subject:(order)"}, crit.values())

    def test_a_filter_that_cannot_be_recorded_is_taken_back(self):
        def broken(params):
            raise RuntimeError("database is down")
        self.db.on("INSERT INTO unified.filter", broken)
        with self.assertRaises(RuntimeError):
            cleanup._filter("acc1", S, cleanup.TRASH_FILTER, "trash")
        self.assertEqual(self.live(), [])

    def test_an_identical_filter_made_by_hand_is_not_claimed(self):
        self.g.filters["acc1"] = [dict(HAND_MADE)]
        self.assertIsNone(cleanup._filter("acc1", S, cleanup.TRASH_FILTER, "trash"))
        self.assertEqual(self.db.filters, [])


class Removed(World):
    def setUp(self):
        super().setUp()
        for act in ("block", "autoarchive", "quarantine"):   # three generations of automation, recorded
            cleanup._filter("acc1", S, {"block": cleanup.TRASH_FILTER, "autoarchive": cleanup.ARCHIVE_FILTER,
                                        "quarantine": {"addLabelIds": ["Label_Q"], "removeLabelIds": ["INBOX"]}}[act],
                            {"block": "trash", "autoarchive": "archive", "quarantine": "quarantine"}[act], keep="subject:(x)")
        cleanup._filter("acc1", "other@else.com", cleanup.TRASH_FILTER, "trash")
        self.g.filters["acc1"].append(dict(HAND_MADE))   # same sender, made by hand in Gmail
        self.g.calls.clear()

    def deleted(self):
        return [c[1].rsplit("/", 1)[-1] for c in self.g.calls if c[2] == "DELETE"]

    def test_protect_removes_archive_and_quarantine_filters_too_and_nothing_else(self):
        r = cleanup.sender_action({"addr": S, "action": "protect"})
        self.assertEqual(sorted(self.deleted()), ["F1", "F2", "F3"])
        self.assertIn("H1", self.live())                       # the hand-made filter is never deleted
        self.assertIn("F4", self.live())                       # another sender's filter stays
        self.assertEqual(self.kinds(), ["never_spam", "trash"])   # other@else.com's trash + the new never-spam
        self.assertIn("not made by Zero Sum", r["results"][0]["note"])   # ... but the user is told about H1

    def test_protecting_a_domain_covers_its_senders(self):
        cleanup.sender_action({"addr": "@shop.com", "action": "protect"})
        self.assertEqual(sorted(self.deleted()), ["F1", "F2", "F3"])

    def test_keep_and_reset_remove_every_kind(self):
        for act in ("keep", "reset"):
            self.setUp()
            r = cleanup.sender_action({"addr": S, "action": act, "accounts": ["acc1"]})
            self.assertEqual(sorted(self.deleted()), ["F1", "F2", "F3"], act)
            self.assertIn("H1", self.live())
            self.assertIn("3 filter(s) removed", r["results"][0]["note"])
            self.assertIn("not made by Zero Sum", r["results"][0]["note"])

    def test_reset_reaches_an_account_whose_history_was_dropped_by_block(self):
        self.db.on("SELECT DISTINCT account_id FROM unified.msg", [])   # Block deleted the sender's local rows
        cleanup.sender_action({"addr": S, "action": "reset"})
        self.assertEqual(sorted(self.deleted()), ["F1", "F2", "F3"])

    def test_a_new_action_replaces_the_old_filters_instead_of_stacking(self):
        cleanup.sender_action({"addr": S, "action": "block", "keep": ["gift"], "accounts": ["acc1"]})
        mine = [f for f in self.g.filters["acc1"] if f["criteria"]["from"] == S and f["id"] != "H1"]
        self.assertEqual(sorted(str(f["criteria"].get("negatedQuery") or f["criteria"].get("query")) for f in mine),
                         ["subject:(gift)", "subject:(gift)"])
        self.assertEqual(sorted(k for a, _, addr, k, _ in self.db.filters if addr == S), ["keep_star", "trash"])

    def test_a_reapplied_plan_replaces_its_filters(self):
        plan = {"addr": S, "default": "quarantine", "groups": [{"label": "Orders", "words": ["order"], "action": "keep"}]}
        self.db.on("SELECT DISTINCT account_id FROM unified.msg", [("acc1",)])
        cleanup.apply_plan(plan)
        first = [f for f in self.live() if f not in ("H1", "F4")]
        self.assertEqual(len(first), 2)
        plan["groups"][0]["words"], plan["default"] = ["receipt"], "archive"
        cleanup.apply_plan(plan)
        second = [f for f in self.live() if f not in ("H1", "F4")]
        self.assertEqual(len(second), 2)
        self.assertFalse(set(first) & set(second))
        self.assertEqual(sorted(k for a, _, addr, k, _ in self.db.filters if addr == S), ["archive", "keep_star"])
        self.assertIn("H1", self.live())

    def test_unstar_and_unprotect_remove_only_their_own_filter(self):
        cleanup.sender_action({"addr": S, "action": "star", "accounts": ["acc1"]})
        star = self.live()[-1]
        self.g.calls.clear()
        cleanup.sender_action({"addr": S, "action": "unstar", "accounts": ["acc1"]})
        self.assertEqual(self.deleted(), [star])
        cleanup._filter("acc1", "@shop.com", cleanup.NEVER_SPAM_FILTER, "never_spam")
        cleanup._filter("acc1", "vip@shop.com", cleanup.NEVER_SPAM_FILTER, "never_spam")
        dom, vip = self.live()[-2:]
        self.g.calls.clear()
        cleanup.sender_action({"addr": "@shop.com", "action": "unprotect"})
        self.assertEqual(self.deleted(), [dom])               # vip@shop.com is protected on its own: untouched
        self.assertIn(vip, self.live())

    def test_a_recorded_filter_already_deleted_in_gmail_is_forgotten_quietly(self):
        self.g.filters["acc1"] = [f for f in self.g.filters["acc1"] if f["id"] != "F1"]
        self.assertEqual(cleanup.remove_app_filters("acc1", S), 3)
        self.assertEqual([f for f in self.db.filters if f[2] == S], [])

    def test_releasing_a_sender_from_quarantine_removes_only_its_quarantine_filter(self):
        self.g.found["from:" + S] = ["aa11bb"]
        cleanup.quarantine_action({"action": "release", "sender": S})
        self.assertEqual(self.deleted(), ["F3"])


class Adoption(World):
    def setUp(self):
        super().setUp()
        q = {"addLabelIds": ["Label_Q"], "removeLabelIds": ["INBOX"]}
        self.g.filters["acc1"] = [
            dict(HAND_MADE),                                                                       # status: evidence
            {"id": "A2", "criteria": {"from": "news@a.com", "negatedQuery": "subject:(order OR gift)"}, "action": q},
            {"id": "A3", "criteria": {"from": "nostatus@b.com"}, "action": {"removeLabelIds": ["INBOX"]}},
            {"id": "A4", "criteria": {"from": "@bank.com"}, "action": {"removeLabelIds": ["SPAM"]}},
            {"id": "X1", "criteria": {"from": S, "hasAttachment": True}, "action": {"addLabelIds": ["TRASH"]}},
            {"id": "X2", "criteria": {"from": "a@a.com OR b@b.com"}, "action": {"addLabelIds": ["TRASH"]}},
            {"id": "X3", "criteria": {"from": S}, "action": {"addLabelIds": ["TRASH"], "forward": "x@y.com"}},
            {"id": "X4", "criteria": {"from": S, "query": "has:attachment"}, "action": {"addLabelIds": ["STARRED"]}},
            {"id": "X5", "criteria": {"from": "@unprotected.com"}, "action": {"removeLabelIds": ["SPAM"]}}]
        self.db.protected = {"@bank.com"}
        self.db.on('FROM "Newsletter"', [("acc1", S), ("acc1", "news@a.com")])
        self.before = [dict(f) for f in self.g.filters["acc1"]]

    def test_the_default_is_a_report_that_changes_nothing(self):
        rep = cleanup.adopt_filters({})["accounts"][0]
        self.assertEqual(self.db.filters, [])
        self.assertEqual(self.g.calls, [])
        self.assertEqual((rep["filters"], rep["other"], rep["adopted"]), (9, 5, 0))
        self.assertEqual({c["id"]: (c["kind"], c["adopt"]) for c in rep["candidates"]},
                         {"H1": ("trash", True), "A2": ("quarantine", True), "A3": ("archive", False),
                          "A4": ("never_spam", True)})

    def test_apply_records_only_exact_shapes_with_evidence_and_deletes_nothing(self):
        rep = cleanup.adopt_filters({"apply": True})["accounts"][0]
        self.assertEqual(rep["adopted"], 3)
        self.assertEqual(sorted((f[1], f[3], f[4]) for f in self.db.filters),
                         [("A2", "quarantine", "adopted"), ("A4", "never_spam", "adopted"), ("H1", "trash", "adopted")])
        self.assertEqual(self.g.calls, [])                    # no filter created, changed or deleted
        self.assertEqual(self.g.filters["acc1"], self.before)

    def test_running_it_again_adopts_nothing_new(self):
        cleanup.adopt_filters({"apply": True})
        rep = cleanup.adopt_filters({"apply": True})["accounts"][0]
        self.assertEqual((rep["recorded"], rep["adopted"], len(self.db.filters)), (3, 0, 3))

    def test_all_also_takes_exact_shapes_without_evidence_but_never_another_shape(self):
        cleanup.adopt_filters({"apply": True, "all": True})
        self.assertEqual(sorted(f[1] for f in self.db.filters), ["A2", "A3", "A4", "H1"])

    def test_one_account_failing_is_reported_not_fatal(self):
        def gmail(acc, path, method="GET", body=None):
            if acc == "acc2":
                raise RuntimeError("Gmail GET /settings/filters: 500 boom")
            return self.g(acc, path, method, body)
        with Patched(cleanup, gmail=gmail):
            out = cleanup.adopt_filters({})["accounts"]
        self.assertIn("500", out[1]["error"])
        self.assertEqual(len(out[0]["candidates"]), 4)


if __name__ == "__main__":
    unittest.main()
