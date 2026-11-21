"""The keep-word matcher exists twice: guard.subject_has (server previews) and subjectHas in app.js (browser
previews). Both must agree with each other, and with what the applied Gmail search `subject:(word)` matches, on one
shared list of cases: tests/keep_words.json."""
import json, os, shutil, subprocess, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import guard  # noqa: E402

with open(os.path.join(HERE, "keep_words.json"), encoding="utf-8") as f:
    VECTORS = json.load(f)


class KeepWordMatcher(unittest.TestCase):
    def test_there_are_cases_of_both_kinds(self):
        self.assertGreater(sum(v["expect"] for v in VECTORS), 10)
        self.assertGreater(sum(not v["expect"] for v in VECTORS), 10)

    def test_python_matches_the_shared_cases(self):
        for v in VECTORS:
            self.assertEqual(guard.subject_has(v["words"], v["subject"]), v["expect"], v)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_browser_matcher_matches_the_same_cases(self):
        out = subprocess.run(["node", os.path.join(HERE, "keep_words.js")], capture_output=True, text=True, check=True).stdout
        got = json.loads(out)
        self.assertEqual(len(got), len(VECTORS))
        for v, g in zip(VECTORS, got):
            self.assertEqual(g, v["expect"], v)


if __name__ == "__main__":
    unittest.main()
