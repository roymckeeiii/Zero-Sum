"""The Inbox tab's Delete: only the chosen message goes to Trash (never the rest of its thread), a protected sender or
one of your own addresses is refused before Gmail is asked to change anything, and the move is written to the Undo
journal (unified.action_log) first, so Undo can bring exactly that message back. Fakes only."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import cleanup, inbox  # noqa: E402
from fakes import Accounts, Db, Gmail, Patched  # noqa: E402

BANK, VIP, ME, JUNK = "alerts@mail.bank.com", "vip@x.org", "me@x.com", "deals@shop.com"


class InboxDelete(unittest.TestCase):
    def setUp(self):
        self.db, self.g, self.acc = Db(), Gmail(), Accounts(ME, "two@x.com")
        self.db.protected = {"@bank.com", VIP}
        self.journal = {}   # unified.action_log, id -> row

        def insert(p):
            i = len(self.journal) + 1
            self.journal[i] = {"account": p[0], "note": p[1], "add": p[2], "remove": p[3], "ids": p[4], "undone": None}
            return [(i,)]

        def claim(p):
            r = self.journal.get(p[0])
            return [(r["account"], r["add"], r["remove"], r["ids"], "T0")] if r and not r["undone"] else []

        def done(p):
            for r in self.journal.values():
                r["undone"] = "now"
            return []
        self.db.on("INSERT INTO unified.action_log", insert).on("SET undo_started_at = now()", claim)
        self.db.on("SET undo_started_at = clock_timestamp()", [("T1",)]).on("SET undone_at", done)
        # one thread t1: the inbox message m1 (from JUNK), your own reply m2 and a protected bank alert m3
        self.g.meta = {"aa0001": f"Deals <{JUNK}>", "aa0002": f"Me <{ME}>", "aa0003": f"Bank <{BANK}>",
                       "aa0004": f"VIP <{VIP}>"}
        for p in (Patched(cleanup, db=self.db, gmail=self.g, ACC=self.acc, log=lambda *a, **k: None,
                          _store=lambda *a, **k: 0, _background=lambda fn, *a: None),
                  Patched(inbox, ACC=self.acc, gmail=self.g, _meta={})):
            p.__enter__()
            self.addCleanup(p.__exit__)

    def delete(self, mid, tid="abc123"):
        return inbox.action({"account": "acc1", "threadId": tid, "id": mid, "action": "delete"})

    def test_a_protected_sender_or_own_address_is_refused_and_nothing_changes(self):
        for mid in ("aa0003", "aa0004", "aa0002"):   # protected by domain, by address, your own reply
            with self.assertRaises(PermissionError, msg=mid):
                self.delete(mid)
        self.assertEqual(self.g.calls, [])                                    # no trash, no batchModify, nothing
        self.assertEqual(self.journal, {})

    def test_a_sender_that_cannot_be_read_is_refused(self):
        with self.assertRaises(PermissionError):
            self.delete("aa0009")   # Gmail has no such message: unknown sender, not moved
        self.assertEqual(self.g.calls, [])

    def test_only_the_chosen_message_is_trashed_never_its_thread(self):
        r = self.delete("aa0001")
        self.assertEqual(r["ok"], True)
        self.assertEqual(r["count"], 1)
        self.assertEqual([c for c in self.g.calls if "/threads/" in c[1]], [])   # no thread-wide trash
        self.assertEqual([(c[1], c[3]) for c in self.g.calls],
                         [("/messages/batchModify", {"ids": ["aa0001"], "addLabelIds": ["TRASH"], "removeLabelIds": []})])

    def test_the_delete_is_in_the_undo_journal_before_gmail_and_undo_brings_it_back(self):
        order = []
        real = self.db.execute
        self.db.execute = lambda sql, params=None: (order.append("journal") if "INSERT INTO unified.action_log" in sql
                                                    else None) or real(sql, params)
        g = self.g
        inbox.gmail = cleanup.gmail = lambda *a, **k: (order.append("gmail") if a[2:3] == ("POST",) else None) or g(*a, **k)
        r = self.delete("aa0001")
        self.assertEqual(order[:2], ["journal", "gmail"])                     # recorded before it happens
        self.assertEqual(list(self.journal), [r["undo"]])                     # the page's Undo names this entry
        row = self.journal[r["undo"]]
        self.assertEqual((row["account"], row["add"], row["remove"], row["ids"]), ("acc1", ["TRASH"], [], ["aa0001"]))
        self.assertIn(JUNK, row["note"])
        self.assertIn("Inbox", row["note"])
        self.g.calls.clear()
        self.assertEqual(cleanup.undo_action({"id": r["undo"]})["restored"], 1)
        self.assertEqual([(c[1], c[3]) for c in self.g.calls],
                         [("/messages/batchModify", {"ids": ["aa0001"], "addLabelIds": [], "removeLabelIds": ["TRASH"]})])

    def test_delete_needs_a_message_id(self):
        for mid in (None, "../x", 5):
            with self.assertRaises(ValueError, msg=mid):
                self.delete(mid)
        self.assertEqual(self.g.calls, [])


if __name__ == "__main__":
    unittest.main()
