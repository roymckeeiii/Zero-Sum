"""Analytics: the daily counts are grouped by calendar days in the viewer's time zone (sent by the browser, checked
against the zones Postgres knows, else UTC), and the chart draws the same days (runs daily_chart.js under node)."""
import os, shutil, subprocess, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import analytics  # noqa: E402
from fakes import Accounts, Db, Patched  # noqa: E402


class Zone(unittest.TestCase):
    def run_overview(self, tz):
        db = Db().on("pg_timezone_names", lambda p: [(1,)] if p[0] in ("America/New_York", "UTC") else [])
        db.on("FROM unified.collect_state", [(0, 0, None)]).on("SELECT count(DISTINCT from_addr) FROM unified.msg", [(0,)])
        with Patched(analytics, db=db, ACC=Accounts("me@x.com"), gmail=lambda *a, **k: {}):
            out = analytics.overview({"days": ["7"], **({"tz": [tz]} if tz is not None else {})})
        sql, params = db.said("date_trunc('day'")[0]
        return out, sql, params, db

    def test_days_are_grouped_in_the_viewers_zone(self):
        out, sql, params, _ = self.run_overview("America/New_York")
        self.assertIn("date_trunc('day', ts AT TIME ZONE %s)", sql)
        self.assertEqual(params, ("America/New_York", 7, ["acc1"]))
        self.assertEqual(out["tz"], "America/New_York")

    def test_an_unknown_missing_or_odd_zone_is_utc_and_never_reaches_the_query_unchecked(self):
        for tz in (None, "", "Mars/Olympus", "UTC'; DROP TABLE x; --", "x" * 65):
            out, _, params, db = self.run_overview(tz)
            self.assertEqual((out["tz"], params[0]), ("UTC", "UTC"), tz)
            checked = [p for _, p in db.said("pg_timezone_names")]
            self.assertTrue(all(p[0] in ("Mars/Olympus",) for p in checked), (tz, checked))   # odd shapes not even asked


class Scoped(unittest.TestCase):
    """With ACCOUNT_OWNERS (or after an account was unlinked), Analytics shows only the linked accounts: no other
    user's rule names, counts or account ids."""

    def test_every_query_is_held_to_the_linked_accounts(self):
        db = Db().on("pg_timezone_names", [(1,)])
        db.on("FROM unified.collect_state", [(0, 0, None)]).on("SELECT count(DISTINCT from_addr) FROM unified.msg", [(0,)])
        db.on('FROM "ExecutedRule"', lambda p: [r for r in [("Mine", "acc1", 3), ("Theirs", "other", 9)] if r[1] in p[-1]])
        db.on('FROM "Newsletter"', lambda p: [r for r in [("UNSUBSCRIBED", "acc1", 2), ("UNSUBSCRIBED", "other", 5)]
                                              if r[1] in p[-1]])
        db.on("date_trunc('day'", [("2026-11-01", "acc1", 4), ("2026-11-01", "other", 7)])
        with Patched(analytics, db=db, ACC=Accounts("me@x.com"), gmail=lambda *a, **k: {}):
            out = analytics.overview({"days": ["7"]})
        for text in ("FROM unified.msg", '"ExecutedRule"', '"Newsletter"', "unified.collect_state"):
            for sql, params in db.said(text):
                self.assertIn("ANY(%s)", sql, text)
                self.assertIn(["acc1"], list(params), text)
        self.assertEqual([r["name"] for r in out["rules"]], ["Mine"])
        self.assertEqual(out["cleanup"]["UNSUBSCRIBED"], {"total": 2, "accounts": {"me@x.com": 2}})
        self.assertEqual([d["email"] for d in out["daily"]], ["me@x.com"])
        self.assertNotIn("other", repr(out))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Chart(unittest.TestCase):
    def test_the_chart_draws_the_viewers_calendar_days(self):
        r = subprocess.run(["node", os.path.join(HERE, "daily_chart.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("DAILY-CHART OK", r.stdout)

    def test_server_times_in_the_undo_list_and_rule_log_are_shown_on_the_viewers_clock(self):
        r = subprocess.run(["node", os.path.join(HERE, "local_times.js")], capture_output=True, text=True, timeout=60,
                           env={**os.environ, "TZ": "America/New_York"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("LOCAL-TIMES OK", r.stdout)
        js = open(os.path.join(os.path.dirname(HERE), "app.js"), encoding="utf-8").read()
        self.assertNotIn('.at.slice(0, 16)', js)     # the server's text, in the database's zone, is not shown as is


if __name__ == "__main__":
    unittest.main()
