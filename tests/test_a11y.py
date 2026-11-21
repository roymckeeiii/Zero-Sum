"""Keyboard and screen-reader basics of the page: rows and accounts can be reached and opened from the keyboard,
their buttons show on focus as on hover, form fields and tick boxes have names, and text colours keep a 4.5:1
contrast in both themes (runs a11y.js under node for what app.js draws)."""
import os, re, shutil, subprocess, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HTML = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
JS = open(os.path.join(ROOT, "app.js"), encoding="utf-8").read()


def ratio(a, b):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class Page(unittest.TestCase):
    def themes(self):
        light = HTML.split(":root {", 1)[1].split("}", 1)[0]
        dark = HTML.split("@media (prefers-color-scheme: dark) { :root {", 1)[1].split("}", 1)[0]
        return [dict(re.findall(r"--([\w-]+):(#[0-9a-fA-F]{6})", t)) for t in (light, dark)]

    def test_body_and_muted_text_keep_4_5_to_1_in_both_themes(self):
        for v in self.themes():
            for fg in ("text", "muted", "err", "link"):
                for bg in ("bg", "side", "hover"):
                    self.assertGreaterEqual(ratio(v[fg], v[bg]), 4.5, (fg, bg, v[fg], v[bg]))

    def test_button_text_keeps_4_5_to_1(self):
        self.assertIn("button.danger { background:var(--danger); color:#fff;", HTML)
        for v in self.themes():
            self.assertGreaterEqual(ratio("#ffffff", v["danger"]), 4.5, v["danger"])
            self.assertGreaterEqual(ratio(v["accent-t"], v["accent"]), 4.5)

    def test_the_faint_colour_is_never_used_for_text(self):
        self.assertNotRegex(HTML, r"(?<![-\w])color:var\(--faint\)")
        self.assertNotIn("var(--faint)", JS)

    def test_row_buttons_show_on_keyboard_focus_and_focus_is_visible(self):
        self.assertIn(".row:focus-within .acts", HTML)
        self.assertRegex(HTML, r"\.row:focus-visible[^{]*\{ outline:2px")

    def test_every_field_has_a_name(self):
        for tag in re.findall(r'<(?:input|select|textarea)\b(?:[^>"]|"[^"]*")*>', HTML + JS):
            if 'type="checkbox"' in tag and "aria-label" not in tag:
                continue   # wrapped in a <label> with text: checked by test_a11y.js where drawn
            if tag == "<select ${attrs}>":
                calls = [ln for ln in JS.splitlines() if "gactSel(" in ln and "const gactSel" not in ln]
                self.assertTrue(calls and all("aria-label" in ln for ln in calls), calls)
                continue   # the plan action menu: each call passes its own name
            if 'type="hidden"' in tag or "dlg-keep" in tag or "dlg-protect-text" in tag or 'data-f="rule"' in tag:
                continue   # inside a <label>, or under the dialog's heading
            self.assertTrue("aria-label" in tag or "title=" in tag, tag)

    def test_on_a_phone_search_stays_and_the_tab_bar_scrolls(self):
        narrow = HTML.split("@media (max-width: 900px) {\n", 1)[1].split("\n}\n", 1)[0]
        tools = re.findall(r"#accounts \.tools \{([^}]*)\}", narrow)
        self.assertTrue(tools)
        self.assertFalse(any("display:none" in t for t in tools))     # search, scope, Unread only, Refresh
        self.assertIn("header .tabs { max-width:100%; overflow-x:auto; }", narrow)

    def test_accounts_are_links_and_enter_opens_a_focused_row(self):
        self.assertEqual(len(re.findall(r'<a href="#" data-id=', JS)), 2)
        self.assertIn('e.target?.matches?.(".row[data-i], td.who[data-peek]")', JS)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Drawn(unittest.TestCase):
    def test_what_app_js_draws(self):
        r = subprocess.run(["node", os.path.join(HERE, "a11y.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("A11Y OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
