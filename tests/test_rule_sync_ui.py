"""Rules tab (runs app.js under node): "Sync now" shows every delete it would make and asks first; only confirmed
deletes are sent to the server."""
import os, shutil, subprocess, unittest

HERE = os.path.dirname(os.path.abspath(__file__))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class RuleSyncUi(unittest.TestCase):
    def test_sync_now_lists_deletes_and_sends_only_what_was_confirmed(self):
        r = subprocess.run(["node", os.path.join(HERE, "rule_sync_confirm.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RULE-SYNC OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
