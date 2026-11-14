"""The upstream schema contract (izdb.py) and everything that is held to it: grants.sql, the SQL in the sources, the
start-up check, /healthz?deep=1 and tools/check_upstream.py."""
import glob, importlib.util, os, re, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import app, izdb, rulesync  # noqa: E402
from fakes import Db, Patched  # noqa: E402


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def live(drop_columns=(), drop_enum_values=(), drop_tables=()):
    """A database that has exactly the contract, minus what is dropped."""
    db = Db()
    db.on("information_schema.columns", [(t, c) for t, cols in izdb.TABLES.items() if t not in drop_tables
                                         for c in cols if (t, c) not in drop_columns])
    db.on("pg_enum", [(e, v) for e, vals in izdb.ENUMS.items() for v in (vals or ["ANY"]) if (e, v) not in drop_enum_values])
    return db


class Grants(unittest.TestCase):
    def test_grants_cover_exactly_the_contract(self):
        sql = re.sub(r"--.*", "", read("grants.sql"))
        column_grants, table_grants = {}, {}
        for privs, tables in re.findall(r'GRANT\s+([^;]*?)\s+ON\s+((?:"\w+"(?:,\s*)?)+)\s+TO iz_unified;', sql, re.S):
            for table in re.findall(r'"(\w+)"', tables):
                m = re.fullmatch(r"SELECT\s*\((.*)\)", privs.strip(), re.S)
                if m:
                    column_grants[table] = sorted(c.strip().strip('"') for c in m.group(1).split(","))
                else:
                    table_grants[table] = sorted(p.strip() for p in privs.split(","))
        self.assertEqual(column_grants, {t: sorted(cols) for t, cols in izdb.READ.items()})
        self.assertEqual(table_grants, {t: ["DELETE", "INSERT", "SELECT", "UPDATE"] for t in izdb.WRITE})
        # and nothing else is granted on a table: no ALL, no other table, nothing to PUBLIC
        self.assertEqual(len(re.findall(r"\bGRANT\b", sql)), len(izdb.READ) + 1 + 2)   # + the write line, CONNECT, USAGE
        self.assertNotRegex(sql, r"(?i)\bALL\b|PUBLIC\b(?! TO)|SUPERUSER|CREATEROLE|CREATEDB")

    def test_it_is_idempotent(self):
        sql = read("grants.sql")
        self.assertIn("IF NOT EXISTS (SELECT FROM pg_roles", sql)
        self.assertIn("CREATE SCHEMA IF NOT EXISTS unified", sql)


class Sources(unittest.TestCase):
    def test_sql_only_names_upstream_tables_that_are_in_the_contract(self):
        used = set()
        for path in glob.glob(os.path.join(ROOT, "*.py")):
            src = open(path, encoding="utf-8").read()
            used |= set(re.findall(r'(?:FROM|JOIN|INTO|UPDATE)\s+\\?"([A-Z]\w*)\\?"', src))
        self.assertEqual(used, set(izdb.TABLES))

    def test_enum_casts_in_the_sources_are_in_the_contract(self):
        used = set()
        for path in glob.glob(os.path.join(ROOT, "*.py")):
            used |= set(re.findall(r'::\\?"(\w+)\\?"', open(path, encoding="utf-8").read()))
        self.assertLessEqual(used - {"{ENUM[f]}"}, set(izdb.ENUMS))
        self.assertLessEqual(set(izdb.ENUM.values()), set(izdb.ENUMS))

    def test_rule_sync_copies_the_declared_columns(self):
        self.assertIs(rulesync.RULE_FIELDS, izdb.RULE_FIELDS)
        self.assertIs(rulesync.ACTION_FIELDS, izdb.ACTION_FIELDS)
        self.assertLessEqual(set(izdb.RULE_FIELDS), set(izdb.TABLES["Rule"]))
        self.assertLessEqual(set(izdb.ACTION_FIELDS), set(izdb.TABLES["Action"]))

    def test_every_schema_statement_of_the_app_is_idempotent_and_in_its_own_schema(self):
        import core
        for stmt in [s.strip() for s in core.SCHEMA.split(";") if s.strip()]:
            self.assertRegex(stmt, r"^(CREATE TABLE IF NOT EXISTS unified\.|CREATE INDEX IF NOT EXISTS \w+ ON unified\.|"
                                   r"ALTER TABLE unified\.\w+ ADD COLUMN IF NOT EXISTS )", stmt[:60])


class Check(unittest.TestCase):
    def test_a_matching_database_has_no_problems(self):
        self.assertEqual(izdb.problems(live()), [])

    def test_it_says_exactly_what_is_missing(self):
        db = live(drop_columns={("Rule", "promptText"), ("Account", "refresh_token")}, drop_tables={"Newsletter"},
                  drop_enum_values={("NewsletterStatus", "AUTO_ARCHIVED"), ("SystemType", "ANY")})
        self.assertEqual(sorted(izdb.problems(db)), sorted([
            'column "Rule"."promptText" is missing or not readable',
            'column "Account"."refresh_token" is missing or not readable',
            'table "Newsletter" is missing or not readable',
            'enum "NewsletterStatus" has no value AUTO_ARCHIVED',
            'enum "SystemType" is missing']))

    def test_start_up_logs_each_problem_and_keeps_going(self):
        logged = []
        with Patched(izdb, check=lambda: ['column "Rule"."promptText" is missing or not readable']), \
                Patched(app, log=lambda kind, msg, account=None: logged.append((kind, msg))):
            self.assertEqual(len(app.check_upstream_schema()), 1)
        self.assertIn(("schema-error", 'Inbox Zero schema mismatch: column "Rule"."promptText" is missing or not readable'), logged)

        def down():
            raise RuntimeError("connection refused")
        with Patched(izdb, check=down), Patched(app, log=lambda kind, msg, account=None: logged.append((kind, msg))):
            self.assertIsNone(app.check_upstream_schema())


class DeepHealth(unittest.TestCase):
    def ask(self, check):
        app._deep.update(at=0.0, result=None)
        with Patched(izdb, check=check):
            return app.deep_health()

    def test_ok_mismatch_and_database_failure(self):
        self.assertEqual(self.ask(lambda: []), (200, {"ok": True}))
        self.assertEqual(self.ask(lambda: ["a", "b"]), (503, {"ok": False, "error": "schema", "problems": 2}))

        def down():
            raise RuntimeError("password authentication failed for user iz_unified")
        status, body = self.ask(down)
        self.assertEqual((status, body), (503, {"ok": False, "error": "database"}))   # no detail to an unsigned caller

    def test_it_is_cached_so_an_unsigned_caller_cannot_hammer_the_database(self):
        calls = []
        app._deep.update(at=0.0, result=None)
        with Patched(izdb, check=lambda: calls.append(1) or []):
            for _ in range(5):
                app.deep_health()
        self.assertEqual(len(calls), 1)

    def test_over_http_plain_stays_up_while_deep_fails(self):
        import http.client, json, threading
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), app.H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        app._deep.update(at=0.0, result=None)
        try:
            with Patched(izdb, check=lambda: ["x"]), Patched(app.H, log_message=lambda self, *a: None):
                out = []
                for path in ("/healthz", "/healthz?deep=1", "/healthz?deep=0"):
                    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
                    c.request("GET", path)
                    r = c.getresponse()
                    out.append((r.status, json.loads(r.read())["ok"]))
                    c.close()
            self.assertEqual(out, [(200, True), (503, False), (200, True)])
        finally:
            srv.shutdown()
            srv.server_close()
            app._deep.update(at=0.0, result=None)


SCHEMA_OLD = '''
model Newsletter {
  id        String   @id @default(cuid())
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
  email     String
  status    NewsletterStatus?
  emailAccountId String
  @@unique([email, emailAccountId])
}

model Account {
  id            String  @id
  provider      String
  access_token  String? @db.Text
  refresh_token String? @db.Text
  expires_at    DateTime?
}

enum NewsletterStatus {
  APPROVED
  UNSUBSCRIBED
  AUTO_ARCHIVED
}
'''


class UpstreamCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("check_upstream", os.path.join(ROOT, "tools", "check_upstream.py"))
        cls.cu = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cu)

    def nl(self, schema):
        return self.cu.model_contract(schema, "Newsletter", izdb.TABLES["Newsletter"])

    def test_a_new_upstream_column_the_app_never_touches_is_not_a_change(self):
        new = SCHEMA_OLD.replace("  email     String\n", "  email     String\n  name      String?   // new upstream\n  categoryId String?\n")
        self.assertEqual(self.nl(SCHEMA_OLD), self.nl(new))

    def test_a_change_to_a_used_column_or_a_constraint_is(self):
        self.assertNotEqual(self.nl(SCHEMA_OLD), self.nl(SCHEMA_OLD.replace("  status    NewsletterStatus?", "  status    String?")))
        self.assertNotEqual(self.nl(SCHEMA_OLD), self.nl(SCHEMA_OLD.replace("  emailAccountId String\n", "  mailboxId String\n")))
        self.assertIn("(column emailAccountId not found)", self.nl(SCHEMA_OLD.replace("  emailAccountId String\n", "")))
        self.assertNotEqual(self.nl(SCHEMA_OLD), self.nl(SCHEMA_OLD.replace("@@unique([email, emailAccountId])", "")))

    def test_a_renamed_field_that_keeps_its_column_name_is_found_by_map(self):
        new = SCHEMA_OLD.replace("  access_token  String? @db.Text", '  accessToken  String? @map("access_token") @db.Text')
        self.assertNotIn("not found", self.cu.model_contract(new, "Account", izdb.TABLES["Account"]))

    def test_enums_are_compared_by_the_values_the_app_uses(self):
        e = lambda s: self.cu.enum_contract(s, "NewsletterStatus", izdb.ENUMS["NewsletterStatus"])
        self.assertEqual(e(SCHEMA_OLD), e(SCHEMA_OLD.replace("  APPROVED\n", "  APPROVED\n  SNOOZED\n")))
        self.assertIn("AUTO_ARCHIVED: MISSING", e(SCHEMA_OLD.replace("  AUTO_ARCHIVED\n", "")))
        self.assertTrue(self.cu.enum_contract(SCHEMA_OLD, "SystemType", []).startswith("("))

    def test_it_watches_exactly_the_contract(self):
        with Patched(self.cu, raw=lambda tag, path: SCHEMA_OLD if path.endswith("schema.prisma") else ""):
            items = self.cu.watched("v1")
        self.assertEqual({k.split(" ")[1] for k in items if k.startswith("model ")}, set(izdb.TABLES))
        self.assertEqual({k.split(" ")[1] for k in items if k.startswith("enum ")}, set(izdb.ENUMS))


if __name__ == "__main__":
    unittest.main()
