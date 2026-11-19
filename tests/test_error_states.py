"""Error states in the UI (runs app.js under node): a failed account load is shown as an error, not as "no linked
accounts", and a request that never reaches the app says to reload (an expired sign-in)."""
import os, shutil, subprocess, unittest

HERE = os.path.dirname(os.path.abspath(__file__))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ErrorStates(unittest.TestCase):
    def test_failures_are_not_shown_as_empty(self):
        r = subprocess.run(["node", os.path.join(HERE, "error_states.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ERROR-STATES OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
