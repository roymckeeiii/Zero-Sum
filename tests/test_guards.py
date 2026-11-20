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


class OwnSpellings(World):
    """The user's own mailbox in any spelling that delivers to it: +tags, Gmail's ignored dots, googlemail.com."""

    def setUp(self):
        super().setUp()
        self.acc = Accounts(ME, "jane.doe@gmail.com")
        self.p2 = Patched(cleanup, ACC=self.acc)
        self.p2.__enter__()
        self.addCleanup(self.p2.__exit__)

    def test_variants_of_an_own_address_are_own(self):
        for addr in ("me+news@x.com", "ME@x.com", "jane.doe@gmail.com", "janedoe@gmail.com", "j.a.n.e.d.o.e+x@gmail.com",
                     "jane.doe@googlemail.com", "janedoe+shop@googlemail.com"):
            self.assertTrue(cleanup.own_address(addr), addr)
        for addr in ("m.e@x.com", "me@x.co", "jane.doe@gmail.co", "janedoe2@gmail.com", "x+me@x.com"):
            self.assertFalse(cleanup.own_address(addr), addr)   # dots only count at Gmail; the tag is after the +

    def test_every_destructive_path_refuses_a_variant(self):
        for who in ("me+tag@x.com", "jane.doe+promo@googlemail.com", "janedoe@gmail.com"):
            with self.assertRaises(PermissionError, msg=who):
                cleanup.sender_action({"addr": who, "action": "block"})
            with self.assertRaises(PermissionError, msg=who):
                cleanup._batch("acc1", ["m1"], add=["TRASH"], sender=who)
        self.db.on("SELECT id, from_addr FROM unified.msg", [("m1", JUNK), ("m2", "me+x@x.com"), ("m3", "jane.doe@googlemail.com")])
        self.assertEqual(cleanup._batch("acc1", ["m1", "m2", "m3"], add=["TRASH"]), 1)
        self.untouched_but_m1()

    def untouched_but_m1(self):
        self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["m1"]])


class RealSender(World):
    """Gmail's from:"addr" also finds mail where addr is only in the display name, or part of a longer address: a
    destructive sender action changes only mail really from addr, and says how much it left alone."""

    def setUp(self):
        super().setUp()
        self.g.found["from:"] = ["aa11bb", "aa22cc", "aa33dd", "aa44ee", "aa55ff"]
        self.g.meta = {"aa11bb": f"Deals <{JUNK}>", "aa22cc": f'"{JUNK}" <{BANK}>',     # a protected sender, spoofed name
                       "aa33dd": f"Me <{ME}>", "aa44ee": "Deals AU <deals@shop.com.au>"}  # aa55ff: sender unknown

    def test_block_trashes_only_mail_really_from_that_sender(self):
        r = cleanup.sender_action({"addr": JUNK, "action": "block"})
        self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["aa11bb"]])
        self.assertEqual(self.db.said("INSERT INTO unified.action_log")[0][1][4], ["aa11bb"])
        note = r["results"][0]["note"]
        self.assertIn("1 moved to Trash", note)
        self.assertIn("2 from a protected sender or your own address left alone", note)
        self.assertIn("2 sent by someone else (or sender unknown) left alone", note)

    def test_every_destructive_path_checks_the_real_sender(self):
        for act in ("delete", "spam", "archive", "quarantine", "unsub_delete"):
            self.g.calls.clear()
            cleanup.sender_action({"addr": JUNK, "action": act})
            self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["aa11bb"]], act)
        self.g.calls.clear()
        cleanup._plan_action("acc1", JUNK, "delete", {}, "", "Label_Q")
        self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["aa11bb"]])

    def test_starring_is_not_filtered(self):
        cleanup.sender_action({"addr": JUNK, "action": "star"})
        self.assertEqual(len(self.g.changes("batchModify")[0][3]["ids"]), 5)


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
            self.g.meta["m1"] = ME
            with self.assertRaises(PermissionError):
                cleanup._ai_sort_ids(ME, "keep news", "delete", [(self.acc.list()[0], "m1")])
        self.assertEqual(self.g.changes("batchModify"), [])

    def test_ai_sort_acts_only_on_mail_really_from_the_sender(self):
        # Gmail's from:"deals@shop.com" also finds mail from the protected bank that uses it as a display name
        import ai
        self.db.on("FROM unified.ai_sort WHERE addr", [("keep receipts", "delete")])
        self.db.on("SELECT account_id, id, subject", [("acc1", "aa11bb", "Sale", ""), ("acc1", "aa22cc", "Statement", ""),
                                                      ("acc1", "aa33dd", "Hi", "")])
        self.g.meta = {"aa11bb": f"Deals <{JUNK}>", "aa22cc": f'"{JUNK}" <{BANK}>', "aa33dd": f'"{JUNK}" <x@y.org>'}
        judged = []
        with Patched(ai, classify=lambda rule, items: judged.extend(items) or [False] * len(items)):
            a1 = self.acc.list()[0]
            cleanup._ai_sort_ids(JUNK, "keep receipts", "delete", [(a1, "aa11bb"), (a1, "aa22cc"), (a1, "aa33dd")])
        self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["aa11bb"]])
        self.assertEqual(judged, [("Sale", "")])                       # the others are not even sent to the AI

    def test_undoing_a_quarantine_delete_brings_a_since_protected_senders_mail_out_of_trash_too(self):
        # Quarantine "Delete all" took the bank's mail too; the bank was protected afterwards, then Undo pressed
        self.db.on("UPDATE unified.action_log SET undone_at = now()",
                   [("acc1", ["TRASH"], ["Label_Q"], ["aa11bb", "aa22cc"])])
        self.db.on("SELECT id, from_addr FROM unified.msg", [("aa11bb", JUNK), ("aa22cc", BANK)])
        self.db.on("SELECT id FROM unified.msg", [("aa22cc",)])   # the held one is still in the local history
        self.g.meta = {"aa11bb": JUNK}
        r = cleanup.undo_action({"id": 7})   # the real _store, against the fake database
        # the held one went back to the inbox: its local row says so (it is not fetched again, it is already there)
        self.assertEqual(self.db.said("UPDATE unified.msg SET inbox")[0][1], (True, "acc1", ["aa22cc"]))
        self.assertEqual([p[1][1] for p in self.db.said("INSERT INTO unified.msg")], ["aa11bb"])   # the other is read back
        bodies = [c[3] for c in self.g.changes("batchModify")]
        self.assertEqual(bodies, [{"ids": ["aa11bb"], "addLabelIds": ["Label_Q"], "removeLabelIds": ["TRASH"]},
                                  {"ids": ["aa22cc"], "addLabelIds": ["INBOX"], "removeLabelIds": ["TRASH"]}])
        self.assertEqual((r["restored"], r["held"]), (2, 1))
        self.assertIn("protected", r["note"])

    def test_batch_with_a_sender_leaves_out_mail_the_history_says_is_someone_elses(self):
        self.db.on("SELECT id, from_addr FROM unified.msg", [("m1", JUNK), ("m2", BANK), ("m3", "x@y.org")])
        self.assertEqual(cleanup._batch("acc1", ["m1", "m2", "m3", "m4"], add=["TRASH"], sender=JUNK), 2)
        self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["m1", "m4"]])

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


class LookupBudget(World):
    """A bulk action reads at most BULK_LOOKUPS senders from Gmail per request, so it answers inside the proxy's timeout;
    the messages it could not check are left alone and the answer says to run it again."""

    def setUp(self):
        super().setUp()
        import gmailops
        self.g.found["from:"] = ["aa0001", "aa0002", "aa0003", "aa0004", "aa0005"]
        self.g.meta = {i: JUNK for i in self.g.found["from:"]}            # none in the local history yet
        p = Patched(gmailops, BULK_LOOKUPS=2)
        p.__enter__()
        self.addCleanup(p.__exit__)

    def test_a_sender_action_stops_looking_up_at_the_budget_and_says_run_it_again(self):
        read = []
        real = self.g

        def gmail(acc, path, method="GET", body=None, retry=True):
            if path.startswith("/messages/") and method == "GET":
                read.append(path)
            return real(acc, path, method, body)
        with Patched(cleanup, gmail=gmail):
            r = cleanup.sender_action({"addr": JUNK, "action": "delete"})
        self.assertEqual(len(read), 2)
        self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["aa0001", "aa0002"]])
        self.assertTrue(r["partial"])
        self.assertIn("3 not done yet", r["results"][0]["note"])
        self.assertIn("run it again", r["results"][0]["note"])
        r = cleanup.sender_action({"addr": JUNK, "action": "star"})   # no look-ups needed: not partial
        self.assertFalse(r["partial"])

    def test_quarantine_delete_keeps_to_the_budget_too(self):
        self.g.found = {"labelIds=Label_Q": ["aa0001", "aa0002", "aa0003"]}
        self.acc.accs = self.acc.accs[:1]
        r = cleanup.quarantine_action({"action": "delete", "all": True})
        self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["aa0001", "aa0002"]])
        self.assertTrue(r["partial"])
        self.assertIn("1 not done yet", r["results"][0]["note"])

    def test_senders_read_once_are_remembered_so_a_rerun_gets_further(self):
        # Gmail's from: search also finds deals@shop.com.au. When those come first, a run spends its whole budget on
        # them; without remembering them, every run read the same ones again and never reached the real mail
        others, mine = ["ab0001", "ab0002", "ab0003"], ["cd0001", "cd0002"]
        self.acc.accs = self.acc.accs[:1]
        self.g.found = {"from:": others + mine}
        self.g.meta = {**{i: "AU <deals@shop.com.au>" for i in others}, **{i: JUNK for i in mine}}
        real = self.g

        def gmail(acc, path, method="GET", body=None, retry=True):
            r = real(acc, path, method, body)
            if path.endswith("batchModify") and "TRASH" in body["addLabelIds"]:
                real.found["from:"] = [i for i in real.found["from:"] if i not in body["ids"]]
            return r
        runs = []
        with Patched(cleanup, gmail=gmail):
            for _ in range(4):
                runs.append(cleanup.sender_action({"addr": JUNK, "action": "delete"}))
                if not runs[-1]["partial"]:
                    break
        self.assertEqual(self.g.found["from:"], others)                 # all of the real sender's mail is in Trash
        self.assertEqual(len(runs), 3)
        self.assertEqual(self.db.seen[("acc1", "ab0001")], "deals@shop.com.au")

    def test_a_run_that_gets_nowhere_says_why_instead_of_run_it_again(self):
        self.acc.accs = self.acc.accs[:1]
        self.g.meta = {}                                                  # Gmail answers no metadata read
        r = cleanup.sender_action({"addr": JUNK, "action": "delete"})
        self.assertFalse(r["partial"])
        note = r["results"][0]["note"]
        self.assertNotIn("run it again", note)
        self.assertIn("3 not done: Gmail did not return their senders", note)
        self.g.found = {"labelIds=Label_Q": ["ee0001", "ee0002", "ee0003"]}
        r = cleanup.quarantine_action({"action": "delete", "all": True})
        self.assertFalse(r["partial"])
        self.assertIn("1 not done: Gmail did not return their senders", r["results"][0]["note"])

    def test_each_account_gets_its_share_of_the_look_ups(self):
        import gmailops
        self.db.on("SELECT DISTINCT account_id FROM unified.msg", [("acc1",), ("acc2",)])
        read = []
        real = self.g

        def gmail(acc, path, method="GET", body=None, retry=True):
            if path.startswith("/messages/") and method == "GET":
                read.append(acc)
            return real(acc, path, method, body)
        with Patched(gmailops, BULK_LOOKUPS=4), Patched(cleanup, gmail=gmail):
            r = cleanup.sender_action({"addr": JUNK, "action": "delete"})
        self.assertEqual((read.count("acc1"), read.count("acc2")), (2, 2))   # not 4 and 0
        self.assertTrue(r["partial"])

    def test_background_work_has_no_budget(self):
        import gmailops
        self.assertIsNone(gmailops.lookup_budget())


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

    def test_one_senders_action_leaves_mail_gmails_search_matched_from_other_senders(self):
        # from:"deals@shop.com" also finds deals@shop.com.au and display names holding the address
        self.g.found = {"labelIds=Label_Q": ["aaaa01", "ffff06", "abcd07", "eeee05"]}
        self.db.on("SELECT id, from_addr FROM unified.msg", [("aaaa01", JUNK), ("ffff06", JUNK + ".au")])
        self.g.meta = {"abcd07": f'"{JUNK}" <other@else.org>'}   # e5 cannot be read: unknown
        for act in ("delete", "archive", "release"):
            self.g.calls.clear()
            r = cleanup.quarantine_action({"action": act, "sender": JUNK})
            self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["aaaa01"]], act)
            self.assertIn("3 from another sender (or sender unknown) left in Quarantine", r["results"][0]["note"], act)

    def test_the_sender_is_an_address_never_a_search(self):
        with self.assertRaises(ValueError):
            cleanup.quarantine_action({"action": "delete", "sender": "x@y.com OR from:alerts@bank.com"})

    def test_malformed_chosen_items_are_refused_not_crashed_on(self):
        for items in (["aaaa01"], [5, None], [{"account": ["acc1"], "id": "aaaa01"}], [{"account": "acc1", "id": 123456}],
                      [{"account": "acc1", "id": "aaaa01/trash"}], "aaaa01", {"account": "acc1", "id": "aaaa01"}):
            with self.assertRaises(ValueError, msg=items):
                cleanup.quarantine_action({"action": "delete", "items": items})
        self.assertEqual(self.g.calls, [])

    def test_chosen_messages_must_really_carry_the_quarantine_label(self):
        self.g.found["labelIds=Label_Q"] = ["aaaa01", "bbbb02"]          # what Gmail says is in Quarantine right now
        self.db.on("SELECT id, from_addr FROM unified.msg", [("aaaa01", JUNK), ("ffff06", JUNK), ("abcd07", JUNK)])
        items = [{"account": "acc1", "id": i} for i in ("aaaa01", "ffff06", "abcd07", "aaaa01")]   # an inbox message too
        r = cleanup.quarantine_action({"action": "delete", "items": items})
        self.assertEqual(self.trashed(), [["aaaa01"]])
        self.assertEqual(self.db.said("INSERT INTO unified.action_log")[0][1][4], ["aaaa01"])
        note = r["results"][0]["note"]
        self.assertIn("1 moved to Trash", note)
        self.assertIn("2 not in Quarantine, left alone", note)
        for act in ("archive", "release"):     # a release would otherwise put any message back in the inbox
            self.g.calls.clear()
            r = cleanup.quarantine_action({"action": act, "items": [{"account": "acc1", "id": "ffff06"}]})
            self.assertEqual(self.g.changes("batchModify"), [], act)
            self.assertEqual([(x["ok"], "1 not in Quarantine" in x["note"]) for x in r["results"]], [(False, True)])

    def test_chosen_messages_of_an_unlinked_account_or_without_a_label_are_refused_with_a_count(self):
        r = cleanup.quarantine_action({"action": "delete", "items": [{"account": "acc9", "id": "aaaa01"},
                                                                     {"account": "acc9", "id": "bbbb02"}]})
        self.assertEqual(self.g.calls, [])
        self.assertEqual([x["note"] for x in r["results"]], ["2 not acted on: not a linked account"])
        with Patched(cleanup, label_id=lambda a, name, create=True: None):   # no Quarantine label in this account
            r = cleanup.quarantine_action({"action": "release", "items": [{"account": "acc1", "id": "aaaa01"}]})
        self.assertEqual(self.g.calls, [])
        self.assertIn("1 not in Quarantine", r["results"][0]["note"])

    def test_archive_and_release_still_work_for_everything(self):
        cleanup.quarantine_action({"action": "release", "all": True})
        self.assertEqual([len(c[3]["ids"]) for c in self.g.changes("batchModify")], [5])


if __name__ == "__main__":
    unittest.main()
