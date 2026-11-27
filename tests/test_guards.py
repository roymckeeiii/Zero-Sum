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
        self.db.on("SET undo_started_at = clock_timestamp()", [("T1",)])   # an undo renewing its claim
        self.g.found["from:"] = ["aa11bb", "aa22cc"]
        self.p = Patched(cleanup, db=self.db, gmail=self.g, ACC=self.acc, log=lambda *a, **k: None,
                         gmail_list=lambda a, path, key, limit: self.g(a, path).get(key, []),
                         label_id=lambda a, name, create=True: "Label_Q", _one_click=lambda url: True,
                         _background=lambda fn, *a: fn(*a),   # background work runs at once here,
                         gmail_spare=lambda acc, **k: True)   # without waiting for spare quota
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
        self.db.on("UPDATE unified.action_log SET undo_started_at = now()",
                   [("acc1", ["TRASH"], ["Label_Q"], ["aa11bb", "aa22cc"], "T0")])
        self.db.on("SELECT id, from_addr FROM unified.msg", [("aa11bb", JUNK), ("aa22cc", BANK)])
        self.db.on("SELECT id FROM unified.msg", [("aa22cc",)])   # the held one is still in the local history
        self.g.meta = {"aa11bb": JUNK, "aa22cc": BANK}
        self.g.msg_labels = {"aa22cc": ["INBOX"]}
        r = cleanup.undo_action({"id": 7})   # the real _store, against the fake database
        # the held one went back to the inbox: it is read back and its local row says so; the other is read back too
        rows = {p[1]: p[8] for _, p in self.db.said("INSERT INTO unified.msg")}
        self.assertEqual(rows, {"aa22cc": True, "aa11bb": False})
        bodies = [c[3] for c in self.g.changes("batchModify")]
        self.assertEqual(bodies, [{"ids": ["aa11bb"], "addLabelIds": ["Label_Q"], "removeLabelIds": ["TRASH"]},
                                  {"ids": ["aa22cc"], "addLabelIds": ["INBOX"], "removeLabelIds": ["TRASH"]}])
        self.assertEqual((r["restored"], r["held"]), (2, 1))
        self.assertIn("protected", r["note"])

    def test_undoing_an_archive_does_not_call_mail_trashed_since_in_the_inbox(self):
        self.db.on("UPDATE unified.action_log SET undo_started_at = now()", [("acc1", [], ["INBOX"], ["aa11bb", "aa22cc"], "T0")])
        self.g.meta = {"aa11bb": JUNK, "aa22cc": JUNK}
        # aa22cc was deleted by hand after it was archived: Gmail now has INBOX on it, but in Trash
        self.g.msg_labels = {"aa11bb": ["INBOX"], "aa22cc": ["INBOX", "TRASH"]}
        cleanup.undo_action({"id": 7})
        self.assertEqual([c[3] for c in self.g.changes("batchModify")],
                         [{"ids": ["aa11bb", "aa22cc"], "addLabelIds": ["INBOX"], "removeLabelIds": []}])
        rows = {p[1]: p[8] for _, p in self.db.said("INSERT INTO unified.msg")}   # id -> inbox
        self.assertEqual(rows, {"aa11bb": True, "aa22cc": False})

    def test_undo_reads_back_only_the_restored_mail_not_the_whole_trash(self):
        # after a big clean-up, undoing a 2-message archive must not page through every message in Trash and Spam
        self.db.on("UPDATE unified.action_log SET undo_started_at = now()", [("acc1", [], ["INBOX"], ["aa11bb", "aa22cc"], "T0")])
        self.g.meta = {"aa11bb": JUNK, "aa22cc": JUNK}
        self.g.found = {"in:trash": [f"t{i}" for i in range(500)]}
        cleanup.undo_action({"id": 7})
        self.assertEqual([p for p in self.g.reads if p.startswith("/messages?")], [])
        self.assertEqual(sorted(p.split("?")[0] for p in self.g.reads), ["/messages/aa11bb", "/messages/aa22cc"])

    def test_undo_reads_back_at_most_undo_refresh_messages(self):
        many = [f"m{i}" for i in range(12)]
        self.db.on("UPDATE unified.action_log SET undo_started_at = now()", [("acc1", [], ["INBOX"], many, "T0")])
        self.g.meta = {i: JUNK for i in many}
        with Patched(cleanup, UNDO_REFRESH=5):
            cleanup.undo_action({"id": 7})
        self.assertEqual(len([p for p in self.g.reads if p.startswith("/messages/")]), 5)
        # all are marked in the inbox at once, as the undo put INBOX back on them; only the first 5 are read back
        self.assertEqual(self.db.said("UPDATE unified.msg SET inbox")[0][1], (True, "acc1", many))

    def test_undo_answers_before_it_reads_anything_back(self):
        many = [f"m{i}" for i in range(50)]
        self.db.on("UPDATE unified.action_log SET undo_started_at = now()", [("acc1", [], ["INBOX"], many, "T0")])
        self.g.meta = {i: JUNK for i in many}
        jobs, spare = [], []
        with Patched(cleanup, _background=lambda fn, *a: jobs.append((fn, a)),
                     gmail_spare=lambda acc, **k: spare.append(acc) or True):
            r = cleanup.undo_action({"id": 7})
            self.assertEqual(r["restored"], 50)
            self.assertEqual([p for p in self.g.reads if p.startswith("/messages/")], [])   # nothing read yet
            self.assertEqual(len(jobs), 1)
            fn, a = jobs[0]
            fn(*a)
        self.assertEqual(len([p for p in self.g.reads if p.startswith("/messages/")]), 50)
        self.assertEqual(len(spare), 3)   # asks for spare quota before each step of UNDO_STEP (20) reads

    def test_undo_read_back_stops_while_the_accounts_quota_is_busy(self):
        many = [f"m{i}" for i in range(50)]
        self.db.on("UPDATE unified.action_log SET undo_started_at = now()", [("acc1", [], ["INBOX"], many, "T0")])
        self.g.meta = {i: JUNK for i in many}
        answers = iter([True, False])
        with Patched(cleanup, gmail_spare=lambda acc, **k: next(answers)):
            cleanup.undo_action({"id": 7})
        self.assertEqual(len([p for p in self.g.reads if p.startswith("/messages/")]), 20)

    def test_undo_read_back_is_capped_at_500_by_default(self):
        import gmailops
        self.assertEqual(gmailops.UNDO_REFRESH, int(os.environ.get("UNDO_REFRESH", "500")))
        if "UNDO_REFRESH" not in os.environ:
            self.assertEqual(gmailops.UNDO_REFRESH, 500)

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


class InboxOrigin(World):
    """Quarantine, Spam and AI sort act on a sender's mail in every folder: the inbox part and the already-archived
    part are separate changes, so Undo and Release put back in the inbox only what was in it."""

    def setUp(self):
        super().setUp()
        self.g.found["from:"] = ["aa11bb", "aa22cc"]
        self.g.meta = {"aa11bb": JUNK, "aa22cc": JUNK}
        self.g.inbox = {"aa11bb"}            # aa22cc was archived years ago

    def bodies(self):
        return [c[3] for c in self.g.changes("batchModify")]

    def test_quarantine_and_spam_take_out_of_the_inbox_only_what_was_in_it(self):
        for act, label in (("quarantine", "Label_Q"), ("unsub_quarantine", "Label_Q"), ("spam", "SPAM")):
            self.g.calls.clear()
            self.db.sql.clear()
            cleanup.sender_action({"addr": JUNK, "action": act})
            self.assertEqual(self.bodies(), [{"ids": ["aa11bb"], "addLabelIds": [label], "removeLabelIds": ["INBOX"]},
                                             {"ids": ["aa22cc"], "addLabelIds": [label], "removeLabelIds": []}], act)
            logged = [(p[2], p[3], p[4]) for _, p in self.db.said("INSERT INTO unified.action_log")]
            self.assertEqual(logged, [([label], ["INBOX"], ["aa11bb"]), ([label], [], ["aa22cc"])], act)
            notes = [p[1] for _, p in self.db.said("INSERT INTO unified.action_log")]   # the Undo list tells them apart
            self.assertEqual(notes, [f"{act} {JUNK} (in the inbox)", f"{act} {JUNK} (already archived)"], act)
        self.assertEqual([p[1] for _, p in self.db.said("INSERT INTO unified.quarantine_archived")], [])  # spam: none

    def test_quarantine_remembers_which_mail_was_archived(self):
        cleanup.sender_action({"addr": JUNK, "action": "quarantine"})
        self.assertEqual([p for _, p in self.db.said("INSERT INTO unified.quarantine_archived")], [("acc1", ["aa22cc"])])
        self.assertEqual([p for _, p in self.db.said("DELETE FROM unified.quarantine_archived")], [("acc1", ["aa11bb"])])

    def test_a_plan_quarantine_is_split_too(self):
        cleanup._plan_action("acc1", JUNK, "quarantine", {}, "", "Label_Q")
        self.assertEqual(self.bodies(), [{"ids": ["aa11bb"], "addLabelIds": ["Label_Q"], "removeLabelIds": ["INBOX"]},
                                         {"ids": ["aa22cc"], "addLabelIds": ["Label_Q"], "removeLabelIds": []}])

    def test_undoing_the_archived_part_does_not_put_it_in_the_inbox(self):
        self.db.on("UPDATE unified.action_log SET undo_started_at = now()", [("acc1", ["Label_Q"], [], ["aa22cc"], "T0")])
        cleanup.undo_action({"id": 8})
        self.assertEqual(self.bodies(), [{"ids": ["aa22cc"], "addLabelIds": [], "removeLabelIds": ["Label_Q"]}])

    def test_release_puts_only_formerly_inbox_mail_back_in_the_inbox(self):
        # DEFAULT - Roy may change: mail quarantined from the archive is released to the archive
        self.acc.accs = self.acc.accs[:1]
        self.g.found = {"labelIds=Label_Q": ["aa11bb", "aa22cc"]}
        self.db.on("SELECT id, from_addr FROM unified.msg", [("aa11bb", JUNK), ("aa22cc", JUNK)])
        self.db.on("SELECT id FROM unified.quarantine_archived", [("aa22cc",)])
        for req in ({"action": "release", "sender": JUNK}, {"action": "release", "all": True}):
            self.g.calls.clear()
            r = cleanup.quarantine_action(req)
            self.assertEqual(self.bodies(), [{"ids": ["aa11bb"], "addLabelIds": ["INBOX"], "removeLabelIds": ["Label_Q"]},
                                             {"ids": ["aa22cc"], "addLabelIds": [], "removeLabelIds": ["Label_Q"]}], req)
            self.assertIn("1 back in the inbox, 1 archived", r["results"][0]["note"])
            notes = [p[1] for _, p in self.db.said("INSERT INTO unified.action_log")][-2:]
            self.assertTrue(notes[0].endswith("(back to the inbox)") and notes[1].endswith("(kept archived)"), notes)

    def test_entries_are_pruned_after_the_mail_left_quarantine_and_kept_through_an_undo(self):
        self.acc.accs = self.acc.accs[:1]
        self.g.found = {"labelIds=Label_Q": ["aa11bb", "aa22cc"]}
        self.db.on("SELECT id, from_addr FROM unified.msg", [("aa11bb", JUNK), ("aa22cc", JUNK)])
        for act in ("release", "archive", "delete"):
            self.db.sql.clear()
            cleanup.quarantine_action({"action": act, "sender": JUNK})
            left = self.db.said("SET left_at = now()")
            self.assertEqual([p for _, p in left], [("acc1", ["aa11bb", "aa22cc"])], act)
            prune = self.db.said("DELETE FROM unified.quarantine_archived WHERE left_at <")
            self.assertEqual([p for _, p in prune], [(cleanup.UNDO_DAYS,)], act)
        # undoing the release puts them back in Quarantine: the entries count again
        self.db.sql.clear()
        self.db.on("UPDATE unified.action_log SET undo_started_at = now()", [("acc1", [], ["Label_Q"], ["aa22cc"], "T0")])
        cleanup.undo_action({"id": 9})
        self.assertEqual([p for _, p in self.db.said("SET left_at = NULL")], [("acc1", ["aa22cc"])])

    def test_only_mail_that_really_left_quarantine_is_stamped(self):
        # Gmail says aa22cc is from deals@, but the local history says the protected bank sent it: _batch holds it
        # back, so it stays in Quarantine and keeps its origin entry
        self.acc.accs = self.acc.accs[:1]
        self.g.found = {"labelIds=Label_Q": ["aa11bb", "aa22cc"]}
        self.g.meta = {"aa11bb": JUNK, "aa22cc": JUNK}
        self.db.on("SELECT id, from_addr FROM unified.msg", lambda p: [("aa22cc", BANK)] if "aa22cc" in p[1] else [])
        with Patched(cleanup, senders_of=lambda acc, ids, budget=None: {i: JUNK for i in ids}):
            cleanup.quarantine_action({"action": "delete", "sender": JUNK})
        self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["aa11bb"]])
        self.assertEqual([p for _, p in self.db.said("SET left_at = now()")], [("acc1", ["aa11bb"])])

    def test_the_archived_part_holds_back_token_matched_mail_of_protected_and_other_senders(self):
        # from:"deals@shop.com" also finds a protected bank and another sender that use the address in their name
        self.g.found["from:"] = ["aa11bb", "aa22cc", "aa33dd", "aa44ee"]
        self.g.inbox = {"aa11bb"}                                   # everything else is archived
        self.g.meta = {"aa11bb": JUNK, "aa22cc": JUNK, "aa33dd": f'"{JUNK}" <{BANK}>', "aa44ee": f'"{JUNK}" <x@y.org>'}
        r = cleanup.sender_action({"addr": JUNK, "action": "quarantine"})
        self.assertEqual(self.bodies(), [{"ids": ["aa11bb"], "addLabelIds": ["Label_Q"], "removeLabelIds": ["INBOX"]},
                                         {"ids": ["aa22cc"], "addLabelIds": ["Label_Q"], "removeLabelIds": []}])
        note = r["results"][0]["note"]
        self.assertIn("1 from a protected sender or your own address left alone", note)
        self.assertIn("1 sent by someone else (or sender unknown) left alone", note)

    def test_ai_sort_of_existing_mail_leaves_archived_mail_archived(self):
        import ai
        self.db.on("FROM unified.ai_sort WHERE addr", [("keep receipts", "quarantine")])
        self.db.on("SELECT account_id, id, subject", [("acc1", "aa11bb", "Sale", ""), ("acc1", "aa22cc", "Sale", "")])
        self.acc.accs = self.acc.accs[:1]
        with Patched(ai, classify=lambda rule, items: [False] * len(items)):
            cleanup._ai_sort_existing(JUNK, "keep receipts", "quarantine")
        self.assertEqual([p[1] for _, p in self.db.said("INSERT INTO unified.action_log")],
                         [f"AI sort {JUNK} (in the inbox)", f"AI sort {JUNK} (already archived)"])
        self.assertEqual(self.bodies(), [{"ids": ["aa11bb"], "addLabelIds": ["Label_Q"], "removeLabelIds": ["INBOX"]},
                                         {"ids": ["aa22cc"], "addLabelIds": ["Label_Q"], "removeLabelIds": []}])


class UnlinkedAccounts(World):
    """unified.msg keeps rows of accounts that were unlinked or that ACCOUNT_OWNERS leaves out: a sender action,
    preview, plan or protect acts on the linked ones and never fails because of the others."""

    def setUp(self):
        super().setUp()
        self.db.on("SELECT DISTINCT account_id FROM unified.msg", [("acc-gone",), ("acc1",)])
        self.g.meta = {"aa11bb": JUNK, "aa22cc": JUNK}

    def test_delete_preview_and_plan_skip_an_unlinked_account(self):
        r = cleanup.sender_action({"addr": JUNK, "action": "delete"})
        self.assertEqual([(x["email"], x["ok"]) for x in r["results"]], [(ME, True)])
        p = cleanup.preview({"addr": JUNK, "action": "delete"})
        self.assertEqual([a["id"] for a in p["steps"][0]["accounts"]], ["acc1"])
        r = cleanup.apply_plan({"addr": JUNK, "groups": [], "default": "archive"})
        self.assertEqual([x["email"] for x in r["results"]], [ME])

    def test_protect_marks_no_newsletter_row_for_an_unlinked_account(self):
        cleanup.sender_action({"addr": JUNK, "action": "protect"})
        marked = [p[3] for _, p in self.db.said('INSERT INTO "Newsletter"')]
        self.assertIn("acc1", marked)
        self.assertNotIn("acc-gone", marked)   # acc2 may be marked too: protect put a never-spam filter there

    def test_the_cleanup_list_reads_only_linked_accounts(self):
        cleanup.senders({})
        sql, params = self.db.said("FROM unified.msg WHERE ts >")[0]
        self.assertIn("account_id = ANY(%s)", sql)
        self.assertIn(["acc1", "acc2"], list(params))


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

    def test_a_partly_done_delete_keeps_the_local_history_so_run_it_again_finds_the_account(self):
        for act in ("delete", "block", "spam", "unsub_delete"):
            self.db.sql.clear()
            self.db.seen.clear()   # each starts with no sender read yet
            r = cleanup.sender_action({"addr": JUNK, "action": act})
            self.assertTrue(r["partial"], act)
            self.assertEqual(self.db.said("DELETE FROM unified.msg"), [], act)
        with Patched(__import__("gmailops"), BULK_LOOKUPS=100):   # everything looked up: the history goes
            self.db.sql.clear()
            r = cleanup.sender_action({"addr": JUNK, "action": "delete"})
            self.assertFalse(r["partial"])
            self.assertEqual(len(self.db.said("DELETE FROM unified.msg")), 1)

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

    def refusing_reads(self):
        real = self.g

        def gmail(acc, path, method="GET", body=None, retry=True):
            if path.startswith("/messages/") and method == "GET":
                raise RuntimeError(f"Gmail GET {path}: 429 Too Many Requests")
            return real(acc, path, method, body)
        return Patched(cleanup, gmail=gmail)

    def test_a_run_that_gets_nowhere_says_why_instead_of_run_it_again(self):
        self.acc.accs = self.acc.accs[:1]
        with self.refusing_reads():                                       # Gmail answers no metadata read
            r = cleanup.sender_action({"addr": JUNK, "action": "delete"})
        self.assertFalse(r["partial"])
        note = r["results"][0]["note"]
        self.assertNotIn("run it again", note)
        self.assertIn("5 not done: Gmail did not return their senders", note)   # 2 refused, 3 over the budget
        self.g.found = {"labelIds=Label_Q": ["ee0001", "ee0002", "ee0003"]}
        with self.refusing_reads():
            r = cleanup.quarantine_action({"action": "delete", "all": True})
        self.assertFalse(r["partial"])
        self.assertIn("3 not done: Gmail did not return their senders", r["results"][0]["note"])

    def test_a_failed_sender_read_is_not_remembered_as_someone_elses_mail(self):
        # Gmail rate-limits the reads during a Delete: those messages are not done yet, not "someone else's", and a
        # run once Gmail answers again finishes the job instead of serving the failure from the cache
        self.acc.accs = self.acc.accs[:1]
        self.g.found = {"from:": ["ab0001", "ab0002"]}
        self.g.meta = {i: JUNK for i in ("ab0001", "ab0002")}
        with self.refusing_reads():
            r = cleanup.sender_action({"addr": JUNK, "action": "delete"})
        note = r["results"][0]["note"]
        self.assertNotIn("someone else", note)
        self.assertIn("2 not done", note)
        self.assertEqual(self.db.seen, {})
        r = cleanup.sender_action({"addr": JUNK, "action": "delete"})
        self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["ab0001", "ab0002"]])
        self.assertNotIn("not done", r["results"][0]["note"])

    def test_a_read_gmail_refuses_for_good_is_remembered_as_unknown_a_passing_one_is_not(self):
        errors = {"ab0001": RuntimeError("Gmail GET /messages/ab0001: 400 Invalid id value"),
                  "ab0002": RuntimeError("Gmail GET /messages/ab0002: 403 Forbidden: insufficient permission"),
                  "ab0003": RuntimeError("Gmail GET /messages/ab0003: 404 Not Found"),
                  "ab0004": RuntimeError("Gmail GET /messages/ab0004: 429 Too Many Requests"),
                  "ab0005": RuntimeError('Gmail GET /messages/ab0005: 403 {"reason": "userRateLimitExceeded"}'),
                  "ab0006": RuntimeError("Gmail GET /messages/ab0006: 503 Backend Error"),
                  "ab0007": TimeoutError("timed out"),
                  "ab0008": OSError("connection reset"),
                  "ab0009": RuntimeError("Gmail GET /messages/ab0009: 401 Invalid Credentials")}   # after a refresh

        def gmail(acc, path, method="GET", body=None, retry=True):
            raise errors[path.split("?")[0].rsplit("/", 1)[-1]]
        with Patched(cleanup, gmail=gmail):
            who = cleanup.senders_of("acc1", sorted(errors))
        self.assertEqual(who, {"ab0001": None, "ab0002": None, "ab0003": None})
        self.assertEqual(self.db.seen, {("acc1", "ab0001"): None, ("acc1", "ab0002"): None, ("acc1", "ab0003"): None})

    def test_a_message_deleted_since_it_was_listed_is_left_alone(self):
        self.acc.accs = self.acc.accs[:1]
        self.g.found = {"from:": ["ab0001", "ab0002"]}
        self.g.meta = {"ab0001": JUNK}                                    # ab0002: Gmail says 404
        r = cleanup.sender_action({"addr": JUNK, "action": "delete"})
        self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["ab0001"]])
        self.assertIn("1 sent by someone else (or sender unknown) left alone", r["results"][0]["note"])
        self.assertFalse(r["partial"])

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

    def test_a_group_without_a_plain_address_offers_no_sender_buttons(self):
        import actions
        metas = {"aaaa01": JUNK, "bbbb02": "", "cccc03": '"odd local"@x.com'}
        self.g.found = {"labelIds=Label_Q": list(metas)}
        with Patched(actions, meta=lambda a, i: {"fromAddr": metas[i], "from": "x", "account": a["id"], "email": a["email"],
                                                 "id": i, "ts": 1}):
            d = cleanup.quarantine_list()
        self.assertEqual({g["addr"]: g["actionable"] for g in d["senders"]},
                         {JUNK: True, "": False, '"odd local"@x.com': False})

    def test_archive_and_release_still_work_for_everything(self):
        cleanup.quarantine_action({"action": "release", "all": True})
        self.assertEqual([len(c[3]["ids"]) for c in self.g.changes("batchModify")], [5])


class TrashFallback(World):
    """Some accounts refuse TRASH in batchModify: only that refusal switches to trashing one by one, and the other
    label changes still happen; a rate limit is raised, not turned into a thousand single calls."""

    def refuse(self, error):
        real = self.g

        def gmail(acc, path, method="GET", body=None, retry=True):
            if path == "/messages/batchModify" and "TRASH" in body["addLabelIds"]:
                real.calls.append((acc, path, method, body))
                raise RuntimeError(error)
            return real(acc, path, method, body)
        return Patched(cleanup, gmail=gmail)

    def test_a_rate_limit_is_raised_not_fallen_back_on(self):
        with self.refuse("Gmail POST /messages/batchModify: 429 Too many requests"):
            with self.assertRaises(RuntimeError):
                cleanup._batch("acc1", ["aa11bb", "aa22cc"], add=["TRASH"], remove=["Label_Q"])
        self.assertEqual(self.g.changes("/trash"), [])

    def test_the_trash_refusal_trashes_one_by_one_and_still_removes_the_quarantine_label(self):
        with self.refuse("Gmail POST /messages/batchModify: 400 Invalid label: TRASH"):
            cleanup._batch("acc1", ["aa11bb", "aa22cc"], add=["TRASH"], remove=["Label_Q"])
        self.assertEqual([c[1] for c in self.g.changes("/trash")], ["/messages/aa11bb/trash", "/messages/aa22cc/trash"])
        self.assertEqual(self.g.changes("batchModify")[-1][3],
                         {"ids": ["aa11bb", "aa22cc"], "addLabelIds": [], "removeLabelIds": ["Label_Q"]})


class JudgedOnce(World):
    """New mail is looked at again in the next run's one-hour overlap: a message judged under a rule is not judged
    again, so a second, different verdict cannot trash mail the first one kept."""

    def test_a_kept_message_is_not_judged_again_by_the_next_run(self):
        import ai
        judged = set()
        self.db.on("SELECT account_id, id FROM unified.ai_sorted",
                   lambda p: [(a, i) for (a, i, rk) in judged if rk == p[1] and i in p[2]])
        self.db.on("INSERT INTO unified.ai_sorted", lambda p: judged.add((p[2], p[3], p[1])) or [])
        self.db.on("FROM unified.ai_sort WHERE addr", [("keep receipts", "delete")])
        self.db.on("SELECT account_id, id, subject", [("acc1", "aa11bb", "Receipt", "")])
        self.g.meta = {"aa11bb": JUNK}
        a1 = self.acc.list()[0]
        verdicts = iter([[True], [False]])                 # kept at 10:00, "drop" if asked again at 10:15
        asked = []
        with Patched(ai, classify=lambda rule, items: asked.append(items) or next(verdicts)[:len(items)]):
            cleanup._ai_sort_ids(JUNK, "keep receipts", "delete", [(a1, "aa11bb")])
            cleanup._ai_sort_ids(JUNK, "keep receipts", "delete", [(a1, "aa11bb")])
        self.assertEqual(self.g.changes("batchModify"), [])
        self.assertEqual([len(x) for x in asked if x], [1])   # asked once
        with Patched(ai, classify=lambda rule, items: [False] * len(items)):   # a new rule judges afresh
            self.db.on("FROM unified.ai_sort WHERE addr", [("keep invoices", "delete")])
            cleanup._ai_sort_ids(JUNK, "keep invoices", "delete", [(a1, "aa11bb")])
        self.assertEqual([c[3]["ids"] for c in self.g.changes("batchModify")], [["aa11bb"]])


class QuarantineBadge(World):
    def test_the_badge_reads_one_label_per_account_and_lists_nothing(self):
        real = self.g

        def gmail(acc, path, method="GET", body=None, retry=True):
            if path == "/labels/Label_Q":
                real.reads.append(path)
                return {"messagesTotal": {"acc1": 3, "acc2": 4}[acc]}
            return real(acc, path, method, body)
        with Patched(cleanup, gmail=gmail):
            self.assertEqual(cleanup.quarantine_count(), {"total": 7})
        self.assertEqual(self.g.reads, ["/labels/Label_Q", "/labels/Label_Q"])

    def test_page_start_asks_only_for_the_count(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('api("/api/quarantine/count")', js)
        self.assertEqual(js.count('api("/api/quarantine")'), 1)   # the full listing: only in the tab's own load
        tab = js[js.index("async function loadQuarantine("):]
        self.assertLess(tab.index('api("/api/quarantine")'), tab.index("\n}\n"))


class Background(unittest.TestCase):
    def test_background_work_does_not_use_the_request_pool(self):
        import threading, gmailops
        used, done = [], threading.Event()

        class Spy:
            def submit(self, *a):
                used.append(a)

            def map(self, *a):
                used.append(a)
        with Patched(gmailops, POOL=Spy()):
            gmailops._background(done.set).result(5)
        self.assertTrue(done.is_set())
        self.assertEqual(used, [])
        self.assertEqual(gmailops.BACKGROUND._max_workers, 2)

    def test_whatever_a_background_job_raises_is_logged(self):
        import gmailops
        logged = []

        def job():
            raise LookupError("account gone")
        with Patched(gmailops, log=lambda *a, **k: logged.append(a)):
            gmailops._background(job).result(5)
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0][0], "cleanup-error")
        self.assertIn("account gone", logged[0][1])


if __name__ == "__main__":
    unittest.main()
