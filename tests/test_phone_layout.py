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


class PhoneAiPlans(unittest.TestCase):
    def test_plan_group_rows_fit_a_narrow_card(self):
        # plain 1fr columns keep their inputs' minimum width, so at 390 px the row ran past the card's edge
        m = re.search(r"@media \(max-width: 900px\) \{ \.plan \.g \{([^}]*)\}(.*?)\}\n", read("index.html"))
        self.assertTrue(m, "no narrow layout for plan groups")
        self.assertIn("grid-template-columns:minmax(0,1fr) minmax(0,1fr);", m.group(1))
        self.assertIn(".plan .g > * { min-width:0; }", m.group(2))


class PhoneOtherTabs(unittest.TestCase):
    """At 390 and 320 px: peek and Quarantine rows give the subject its own line, the Quarantine toolbar is two even
    columns, Analytics' By account table becomes named numbers per account, and the reader's buttons are compact."""
    phone_css = PhoneCleanup.phone_css

    def test_rows_put_the_subject_on_its_own_line(self):
        css = self.phone_css()
        self.assertIn(".peek .pm .s { flex:1 1 100%; order:-1; }", css)
        self.assertIn(".qgroup .qm > .s { grid-column:2 / 4; grid-row:1; }", css)
        self.assertIn("-webkit-line-clamp:2", css)

    def test_quarantine_toolbar_is_two_columns(self):
        self.assertIn("#q-bar { display:grid; grid-template-columns:1fr 1fr; }", self.phone_css())
        self.assertIn('<div class="bar-row" id="q-bar">', read("index.html"))

    def test_analytics_accounts_are_named_numbers(self):
        self.assertIn("table.acct-table td[data-k]::before", self.phone_css())
        js = read("app.js")
        self.assertIn('<table class="acct-table">', js)
        for k in ("Received", "Read", "Senders", "In inbox", "Unread threads"):
            self.assertIn(f'data-k="{k}"', js)

    def test_reader_buttons_are_compact_without_key_hints(self):
        self.assertIn("#reader .tools .key { display:none; }", self.phone_css())
        self.assertIn('Archive<span class="key"> (e)</span>', read("app.js"))


if __name__ == "__main__":
    unittest.main()
