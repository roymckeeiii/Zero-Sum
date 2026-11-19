"""Reader (runs app.js under node): "Show images" is for the one message it was clicked on, not every later one."""
import os, shutil, subprocess, unittest

HERE = os.path.dirname(os.path.abspath(__file__))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ImagesPerMessage(unittest.TestCase):
    def test_show_images_does_not_carry_over_to_the_next_message(self):
        r = subprocess.run(["node", os.path.join(HERE, "images_per_message.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("IMAGES OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
