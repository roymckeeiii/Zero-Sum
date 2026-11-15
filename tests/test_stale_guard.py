"""RFC-001885: the inbox list ignores an answer to an older search or Load more (runs app.js's loadList under node)."""
import os, shutil, subprocess, unittest

HERE = os.path.dirname(os.path.abspath(__file__))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class StaleSearchGuard(unittest.TestCase):
    def test_older_answer_cannot_overwrite_a_newer_search(self):
        r = subprocess.run(["node", os.path.join(HERE, "stale_guard.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("STALE-GUARD OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
