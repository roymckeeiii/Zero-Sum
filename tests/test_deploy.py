"""The deploy definition lives with the app: deploy/env.contract lists exactly the environment variables the code
reads, and deploy/zero-sum.container.in is a hardened quadlet with a health check on the deep endpoint."""
import glob, os, re, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def contract():
    rows = [ln.split(None, 2) for ln in read("deploy", "env.contract").splitlines() if ln.strip() and not ln.startswith("#")]
    return {r[0]: r[1] for r in rows}


class EnvContract(unittest.TestCase):
    def test_it_lists_exactly_the_variables_the_code_reads(self):
        used = set()
        for path in glob.glob(os.path.join(ROOT, "*.py")) + glob.glob(os.path.join(ROOT, "tools", "*.py")):
            used |= set(re.findall(r'os\.environ(?:\.get\(|\[)\s*"([A-Z][A-Z0-9_]*)"', read(path)))
        self.assertEqual(used, set(contract()))

    def test_kinds_are_known_and_sign_in_and_secrets_are_required(self):
        c = contract()
        self.assertLessEqual(set(c.values()), {"required", "optional", "tool"})
        for name in ("DATABASE_URL", "EMAIL_ENCRYPT_SECRET", "EMAIL_ENCRYPT_SALT", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                     "ALLOWED_EMAILS", "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD"):
            self.assertEqual(c[name], "required", name)
        self.assertEqual(c["ALLOW_HEADER_TRUST"], "optional")

    def test_every_variable_is_in_the_readme_configuration_table(self):
        rows = "\n".join(ln for ln in read("README.md").splitlines() if ln.startswith("| `"))
        for name in contract():
            self.assertIn(f"`{name}`", rows, name)

    def test_every_setting_a_user_can_change_is_in_the_example_file(self):
        example = read(".env.example")
        for name, kind in contract().items():
            if kind != "tool" or name == "ADMIN_DATABASE_URL":
                if name in ("AI_BASE_URL", "QUARANTINE_SHOW"):   # documented in README only
                    continue
                self.assertRegex(example, rf"(?m)^#? ?{name}=", name)


class Quadlet(unittest.TestCase):
    def test_it_is_hardened_local_only_and_health_checked_deeply(self):
        unit = read("deploy", "zero-sum.container.in")
        for line in ("PublishPort=127.0.0.1:@PORT@:8080", "ReadOnly=true", "DropCapability=ALL", "NoNewPrivileges=true",
                     "UserNS=auto", "EnvironmentFile=@ENV_FILE@", "Restart=always"):
            self.assertIn("\n" + line + "\n", unit, line)
        self.assertRegex(unit, r"\nHealthCmd=.*healthz\?deep=1")
        self.assertNotRegex(unit, r"(?m)^(Environment=|.*PASSWORD|.*SECRET=)")   # no values in the unit, only the env file

    def test_the_image_runs_unprivileged_and_ships_the_split_modules(self):
        cf = read("Containerfile")
        self.assertIn("USER 65534:65534", cf)
        self.assertIn("COPY *.py *.html *.js grants.sql inbox-zero.version /app/", cf)   # izdb.supported_release()


if __name__ == "__main__":
    unittest.main()
