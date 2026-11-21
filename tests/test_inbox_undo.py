"""Archive in the Inbox can be undone like Delete (runs app.js's act() under node): the toast's Undo puts the thread
back in the inbox of the same account, and is not offered for a search result that was not in the inbox."""
import os, shutil, subprocess, unittest

HERE = os.path.dirname(os.path.abspath(__file__))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class InboxUndo(unittest.TestCase):
    def test_archive_offers_undo_back_to_the_inbox(self):
        r = subprocess.run(["node", os.path.join(HERE, "inbox_undo.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("INBOX-UNDO OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
