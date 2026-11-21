"""The 2-minute inbox refresh keeps pages added with Load more (runs app.js under node); on page one it still
refreshes the list, and it always refreshes the account counts."""
import os, shutil, subprocess, unittest

HERE = os.path.dirname(os.path.abspath(__file__))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class KeepPages(unittest.TestCase):
    def test_the_timer_does_not_drop_loaded_pages(self):
        r = subprocess.run(["node", os.path.join(HERE, "keep_pages.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("KEEP-PAGES OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
