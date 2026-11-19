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
        self.assertEqual(params, ("America/New_York", 7))
        self.assertEqual(out["tz"], "America/New_York")

    def test_an_unknown_missing_or_odd_zone_is_utc_and_never_reaches_the_query_unchecked(self):
        for tz in (None, "", "Mars/Olympus", "UTC'; DROP TABLE x; --", "x" * 65):
            out, _, params, db = self.run_overview(tz)
            self.assertEqual((out["tz"], params[0]), ("UTC", "UTC"), tz)
            checked = [p for _, p in db.said("pg_timezone_names")]
            self.assertTrue(all(p[0] in ("Mars/Olympus",) for p in checked), (tz, checked))   # odd shapes not even asked


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Chart(unittest.TestCase):
    def test_the_chart_draws_the_viewers_calendar_days(self):
        r = subprocess.run(["node", os.path.join(HERE, "daily_chart.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("DAILY-CHART OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
