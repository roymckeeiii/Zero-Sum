"""The reader shows only the message opened last (runs app.js's open() under node): a slower answer for an earlier
message must not replace it, since the reader's buttons and keys act on the current selection."""
import os, shutil, subprocess, unittest

HERE = os.path.dirname(os.path.abspath(__file__))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ReaderGuard(unittest.TestCase):
    def test_an_older_message_answering_late_cannot_replace_the_selected_one(self):
        r = subprocess.run(["node", os.path.join(HERE, "reader_guard.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("READER-GUARD OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
