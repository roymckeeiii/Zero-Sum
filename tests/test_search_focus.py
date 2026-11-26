"""The inbox refresh keeps the search box (and its focus) in place (runs app.js under node)."""
import os, shutil, subprocess, unittest

HERE = os.path.dirname(os.path.abspath(__file__))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class SearchFocus(unittest.TestCase):
    def test_refreshing_the_accounts_does_not_redraw_the_search_box(self):
        r = subprocess.run(["node", os.path.join(HERE, "search_focus.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SEARCH-FOCUS OK", r.stdout)

    def test_shortcuts_ignore_keys_meant_for_a_text_field(self):
        with open(os.path.join(HERE, "..", "app.js"), encoding="utf-8") as f:
            src = f.read()
        handler = src[src.index("document.onkeydown"):src.index('else if (e.key === "e"')]
        self.assertIn('"TEXTAREA"', handler)
        self.assertIn("isContentEditable", handler)
        self.assertIn("S.blurAt", handler)


if __name__ == "__main__":
    unittest.main()
