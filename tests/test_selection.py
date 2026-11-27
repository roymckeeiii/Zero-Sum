"""Inbox and Cleanup dialog behaviour, run under node: acting on a row other than the open one keeps the open
message, actions that move no existing mail do not ask the server for a count, and a Quarantine group without a
plain sender address offers no sender buttons."""
import os, shutil, subprocess, unittest

HERE = os.path.dirname(os.path.abspath(__file__))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class SelectionFollowsTheMessage(unittest.TestCase):
    def test_removing_another_row_does_not_open_or_mark_read_a_different_message(self):
        r = subprocess.run(["node", os.path.join(HERE, "selection_follows.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SELECTION OK", r.stdout)

    def test_protect_and_keep_do_not_ask_for_a_count(self):
        r = subprocess.run(["node", os.path.join(HERE, "no_count.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("NO-COUNT OK", r.stdout)

    def test_a_quarantine_group_without_an_address_has_no_sender_buttons(self):
        r = subprocess.run(["node", os.path.join(HERE, "quarantine_groups.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("QGROUPS OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
