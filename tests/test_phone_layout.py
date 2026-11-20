"""On a phone the Cleanup table is a list of cards: the actions sit under each sender instead of in a column that only
sideways scrolling reaches."""
import os, re, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


class PhoneCleanup(unittest.TestCase):
    def phone_css(self):
        m = re.search(r"@media \(max-width: 600px\) \{(.*?)\n\}", read("index.html"), re.S)
        self.assertTrue(m, "no phone layout")
        return m.group(1)

    def test_the_table_becomes_cards_with_the_actions_under_the_sender(self):
        css = self.phone_css()
        self.assertRegex(css, r"#c-table thead \{ display:none; \}")
        self.assertRegex(css, r"#c-table tbody tr \{ display:flex; flex-wrap:wrap;")
        self.assertRegex(css, r"#c-table td\.acts-cell \{[^}]*flex:1 1 100%")
        self.assertIn("#c-table tr.peek-row td { flex:1 1 100%; }", css)

    def test_the_numbers_keep_their_names_without_the_header(self):
        self.assertIn("#c-table td[data-k]::before { content:attr(data-k)", self.phone_css())
        js = read("app.js")
        for k in ("Emails", "Read", "In inbox"):
            self.assertIn(f'data-k="{k}"', js)


if __name__ == "__main__":
    unittest.main()
