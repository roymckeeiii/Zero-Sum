"""Protected senders and the user's own addresses are refused where every destructive change ends (_batch for existing
mail, _filter for future mail), as well as at each entry point."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import cleanup  # noqa: E402
from fakes import Accounts, Db, Gmail, Patched  # noqa: E402

BANK, VIP, ME, JUNK = "alerts@mail.bank.com", "vip@x.org", "me@x.com", "deals@shop.com"
REFUSED = (BANK, VIP, ME)   # protected by domain, protected by address, one of the linked accounts


class World(unittest.TestCase):
    def setUp(self):
        self.db, self.g, self.acc = Db(), Gmail(), Accounts(ME, "two@x.com")
        self.db.protected = {"@bank.com", VIP}
        self.db.on("SELECT DISTINCT account_id FROM unified.msg", [("acc1",)]).on("DISTINCT ON (account_id)", [])
        self.g.found["from:"] = ["aa11bb", "aa22cc"]
        self.p = Patched(cleanup, db=self.db, gmail=self.g, ACC=self.acc, log=lambda *a, **k: None,
                         gmail_list=lambda a, path, key, limit: self.g(a, path).get(key, []),
                         label_id=lambda a, name, create=True: "Label_Q", _one_click=lambda url: True)
        self.p.__enter__()
        self.addCleanup(self.p.__exit__)

    def untouched(self):
        self.assertEqual(self.g.calls, [])                              # no Gmail change of any kind
        self.assertEqual(self.db.said("INSERT INTO unified.action_log"), [])
        self.assertEqual(self.db.said('"Newsletter"'), [])


class ChokePoint(World):
    def test_batch_refuses_every_change_that_takes_mail_out_of_view(self):
        for who in REFUSED:
            for add, remove in ((["TRASH"], []), (["SPAM"], ["INBOX"]), ([], ["INBOX"]), (["Label_Q"], ["INBOX"])):
                with self.assertRaises(PermissionError, msg=(who, add, remove)):
                    cleanup._batch("acc1", ["m1"], add=add, remove=remove, sender=who)
            with self.assertRaises(PermissionError):
                cleanup._batch("acc1", ["m1"], add=["TRASH"], sender={JUNK, who})   # one bad sender refuses the lot
        self.untouched()

    def test_batch_still_stars_and_restores_their_mail(self):
        self.assertEqual(cleanup._batch("acc1", ["m1"], add=["STARRED"], sender=BANK), 1)
        self.assertEqual(cleanup._batch("acc1", ["m1"], add=["INBOX"], remove=["TRASH"], sender=BANK), 1)
        self.assertEqual(cleanup._batch("acc1", ["m1"], add=["TRASH"], sender=JUNK), 1)

    def test_batch_without_a_sender_leaves_out_protected_and_own_mail(self):
        self.db.on("SELECT id, from_addr FROM unified.msg", [("m1", JUNK), ("m2", BANK), ("m3", ME), ("m4", VIP)])
        self.assertEqual(cleanup._batch("acc1", ["m1", "m2", "m3", "m4"], add=["TRASH"]), 1)
        self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["m1"]])
        self.assertEqual(self.db.said("INSERT INTO unified.action_log")[0][1][4], ["m1"])   # and only m1 is recorded

    def test_filter_refuses_trash_archive_and_quarantine_filters(self):
        q = {"addLabelIds": ["Label_Q"], "removeLabelIds": ["INBOX"]}
        for who in REFUSED:
            for action, kind in ((cleanup.TRASH_FILTER, "trash"), (cleanup.ARCHIVE_FILTER, "archive"), (q, "quarantine"),
                                 (cleanup.TRASH_FILTER, "star")):          # a wrong kind does not get round it
                with self.assertRaises(PermissionError, msg=(who, kind)):
                    cleanup._filter("acc1", who, action, kind)
        self.untouched()
        self.assertEqual(self.db.filters, [])
        self.assertTrue(cleanup._filter("acc1", BANK, cleanup.STAR_FILTER, "star"))        # Auto-star is still allowed
        self.assertTrue(cleanup._filter("acc1", "@bank.com", cleanup.NEVER_SPAM_FILTER, "never_spam"))

    def test_modify_existing_cannot_be_called_without_saying_whose_mail_it_is(self):
        with self.assertRaises(TypeError):
            cleanup._modify_existing("acc1", "from:x@y.com", add=["TRASH"])
        for who in REFUSED:
            with self.assertRaises(PermissionError):
                cleanup._modify_existing("acc1", f"from:{who}", add=["TRASH"], sender=who)
        self.untouched()

    def test_plan_groups_and_archive_helper_end_in_the_same_refusal(self):
        for who in REFUSED:
            for action in ("archive", "quarantine", "delete"):
                with self.assertRaises(PermissionError):
                    cleanup._plan_action("acc1", who, action, {}, "", "Label_Q")
            with self.assertRaises(PermissionError):
                cleanup._archive_existing("acc1", who)
        self.untouched()


class EntryPoints(World):
    def test_every_destructive_sender_action_is_refused(self):
        for who in REFUSED:
            for act in sorted(cleanup.DESTRUCTIVE):
                with self.assertRaises(PermissionError, msg=(who, act)):
                    cleanup.sender_action({"addr": who, "action": act, "keep": ["order"]})
        self.untouched()

    def test_if_an_entry_check_were_missing_the_choke_point_still_refuses(self):
        with Patched(cleanup, DESTRUCTIVE=set()):   # simulate a new action someone forgot to list as destructive
            for who in REFUSED:
                for act in ("block", "delete", "quarantine", "autoarchive", "archive", "spam", "unsub_delete"):
                    r = cleanup.sender_action({"addr": who, "action": act})
                    self.assertEqual([x["ok"] for x in r["results"]], [False], (who, act))
        self.assertEqual(self.g.changes("batchModify") + self.g.changes("/settings/filters") + self.g.changes("/trash"), [])
        self.assertEqual(self.db.said("INSERT INTO unified.action_log"), [])

    def test_plans_are_refused_in_both_modes(self):
        for who in REFUSED:
            for plan in ({"default": "delete"}, {"default": "keep", "groups": [{"words": ["sale"], "action": "archive"}]},
                         {"default": "quarantine", "mode": "ai", "rule": "keep receipts"}):
                with self.assertRaises(PermissionError, msg=(who, plan)):
                    cleanup.apply_plan({"addr": who, **plan})
        self.untouched()
        self.assertEqual(self.db.said("unified.ai_sort"), [])

    def test_ai_sort_refuses_before_and_at_the_batch(self):
        self.db.on("FROM unified.ai_sort WHERE addr", [("keep news", "delete")])
        for who in REFUSED[:2]:
            with self.assertRaises(PermissionError):
                cleanup._ai_sort_ids(who, "keep news", "delete", [(self.acc.list()[0], "m1")])
        import ai
        with Patched(cleanup, is_protected=lambda addr, prot=None: False), \
                Patched(ai, classify=lambda rule, items: [False] * len(items)):   # the pass's own check knocked out
            self.db.on("SELECT account_id, id, subject", [("acc1", "m1", "Sale", "")])
            with self.assertRaises(PermissionError):
                cleanup._ai_sort_ids(ME, "keep news", "delete", [(self.acc.list()[0], "m1")])
        self.assertEqual(self.g.changes("batchModify"), [])

    def test_new_mail_of_a_sender_protected_after_its_sort_was_set_up_is_left_alone(self):
        self.db.on("FROM unified.ai_sort", [(BANK, "keep news", "delete", 0), (ME, "keep news", "archive", 0)])
        cleanup.ai_sort_new()
        self.assertEqual(self.g.calls, [])

    def test_interrupted_sorts_of_protected_senders_are_not_resumed(self):
        started = []
        self.db.on("FROM unified.ai_sort WHERE", [(BANK, "r", "delete"), (ME, "r", "archive"), (JUNK, "r", "delete")])
        with Patched(cleanup, start_ai_sort=lambda *a: started.append(a[0])):
            cleanup.resume_ai_sorts()
        self.assertEqual(started, [JUNK])


class Quarantine(World):
    def setUp(self):
        super().setUp()
        self.acc.accs = self.acc.accs[:1]
        self.g.found = {"labelIds=Label_Q": ["aaaa01", "bbbb02", "cccc03", "dddd04", "eeee05"]}
        self.db.on("SELECT id, from_addr FROM unified.msg", [("aaaa01", JUNK), ("bbbb02", BANK), ("cccc03", ME)])
        self.g.meta = {"dddd04": "VIP <vip@x.org>"}     # not in the local history: read from Gmail. e5 cannot be read.

    def trashed(self):
        return [c[3]["ids"] for c in self.g.changes("batchModify") if "TRASH" in c[3]["addLabelIds"]]

    def test_delete_all_leaves_protected_own_and_unknown_senders_in_quarantine(self):
        r = cleanup.quarantine_action({"action": "delete", "all": True})
        self.assertEqual(self.trashed(), [["aaaa01"]])
        note = next(x["note"] for x in r["results"] if x["email"] == ME)
        self.assertIn("1 moved to Trash", note)
        self.assertIn("4 left in Quarantine", note)

    def test_delete_chosen_messages_is_held_to_the_same_rule(self):
        items = [{"account": "acc1", "id": i} for i in ("aaaa01", "bbbb02", "cccc03", "dddd04", "eeee05")]
        cleanup.quarantine_action({"action": "delete", "items": items})
        self.assertEqual(self.trashed(), [["aaaa01"]])

    def test_delete_by_sender_is_refused_for_protected_and_own_and_checks_each_message(self):
        for who in REFUSED:
            with self.assertRaises(PermissionError):
                cleanup.quarantine_action({"action": "delete", "sender": who})
        self.assertEqual(self.g.calls, [])
        cleanup.quarantine_action({"action": "delete", "sender": JUNK})   # Gmail's from: search returned b2 too
        self.assertEqual(self.trashed(), [["aaaa01"]])

    def test_the_sender_is_an_address_never_a_search(self):
        with self.assertRaises(ValueError):
            cleanup.quarantine_action({"action": "delete", "sender": "x@y.com OR from:alerts@bank.com"})

    def test_archive_and_release_still_work_for_everything(self):
        cleanup.quarantine_action({"action": "release", "all": True})
        self.assertEqual([len(c[3]["ids"]) for c in self.g.changes("batchModify")], [5])


if __name__ == "__main__":
    unittest.main()
