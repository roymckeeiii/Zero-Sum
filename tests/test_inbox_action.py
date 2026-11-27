"""inbox.action, the Inbox tab's one-thread triage: only the fixed actions, only a thread id of Gmail's shape, only a
linked account, and each action but Delete is exactly one Gmail call on that account's thread (Delete moves one
message through the guard and the Undo journal: test_inbox_delete). No network or DB."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import inbox  # noqa: E402
from fakes import Patched  # noqa: E402


class FakeAcc:
    def row(self, account_id):
        if account_id not in ("A", "B"):
            raise KeyError("unknown account")
        return {"id": account_id}


class Action(unittest.TestCase):
    def setUp(self):
        self.calls = []
        for p in (Patched(inbox, ACC=FakeAcc(), gmail=lambda acc, path, method="GET", body=None: self.calls.append(
                (acc, path, method, body)) or {}), Patched(inbox, _meta={})):
            p.__enter__()
            self.addCleanup(p.__exit__)

    def test_each_action_is_one_call_on_that_accounts_thread(self):
        cases = {"archive": ("/threads/abc123/modify", {"removeLabelIds": ["INBOX"]}),
                 "inbox": ("/threads/abc123/modify", {"addLabelIds": ["INBOX"]}),
                 "read": ("/threads/abc123/modify", {"removeLabelIds": ["UNREAD"]}),
                 "unread": ("/threads/abc123/modify", {"addLabelIds": ["UNREAD"]}),
                 "undelete": ("/threads/abc123/untrash", None),
                 "star": ("/messages/def456/modify", {"addLabelIds": ["STARRED"]}),   # Gmail stars one message
                 "unstar": ("/messages/def456/modify", {"removeLabelIds": ["STARRED"]})}
        for act, (path, body) in cases.items():
            self.calls.clear()
            self.assertEqual(inbox.action({"account": "B", "threadId": "abc123", "id": "def456", "action": act}), {"ok": True})
            self.assertEqual(self.calls, [("B", path, "POST", body)], act)

    def test_anything_else_is_refused_before_gmail_is_called(self):
        bad = [{"account": "A", "threadId": "abc123", "action": "purge"},          # not one of the actions
               {"account": "A", "threadId": "abc123"},
               {"account": "A", "threadId": "../../settings/filters", "action": "delete"},   # into another API path
               {"account": "A", "threadId": "abc123/trash?x=", "action": "archive"},
               {"account": "A", "threadId": "", "action": "delete"},
               {"account": "A", "threadId": 123456, "action": "delete"},       # not a string: 400, not a crash
               {"account": "A", "threadId": ["abc123"], "action": "delete"},
               {"account": "A", "threadId": None, "action": "archive"}]
        for d in bad:
            with self.assertRaises(ValueError, msg=d):
                inbox.action(d)
        with self.assertRaises(KeyError):                                   # not a linked account
            inbox.action({"account": "Z", "threadId": "abc123", "action": "delete"})
        with self.assertRaises(ValueError):
            inbox.action({"account": "A", "threadId": "abc123", "action": ["star"]})   # unhashable: 400, not a crash
        self.assertEqual(self.calls, [])

    def test_a_star_with_a_bad_message_id_never_reaches_a_message_path(self):
        for mid in ("../threads/x", 123, None, ["def456"]):
            self.calls.clear()
            inbox.action({"account": "A", "threadId": "abc123", "id": mid, "action": "star"})
            self.assertEqual([c[1] for c in self.calls], ["/threads/abc123/modify"], mid)

    def test_only_that_accounts_cached_rows_for_the_thread_are_dropped(self):
        inbox._meta.update({("A", "m1"): (0, {"threadId": "abc123"}), ("B", "m2"): (0, {"threadId": "abc123"}),
                            ("A", "m3"): (0, {"threadId": "fff999"})})
        inbox.action({"account": "A", "threadId": "abc123", "action": "archive"})
        self.assertEqual(sorted(inbox._meta), [("A", "m3"), ("B", "m2")])


if __name__ == "__main__":
    unittest.main()
