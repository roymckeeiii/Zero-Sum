"""Compare two Inbox Zero releases in the parts Zero Sum depends on.

  python3 tools/check_upstream.py                 # supported version (inbox-zero.version) vs the latest release
  python3 tools/check_upstream.py v2.25.8 v2.26.0

Prints, per watched item, "same" or a unified diff, and exits 0 when nothing Zero Sum depends on changed (bump
inbox-zero.version and you are done), 1 when something did (adapt Zero Sum first), 2 on errors. Watched:
- the Prisma models Zero Sum reads or writes (Account, EmailAccount, Rule, Action, Newsletter, ExecutedRule) and the
  enums it casts to (ActionType, LogicalOperator, SystemType, NewsletterStatus, ExecutedRuleStatus);
- apps/web/utils/encryption.ts (how Google tokens are encrypted);
- the environment variable names apps/web/env.ts accepts (deployments set some of them);
- the services, network and scheduled endpoints in docker-compose.yml (the compose add-on joins them).
Only public data is used: git tags (git ls-remote) and raw files from raw.githubusercontent.com.
"""
import difflib, os, re, sys, urllib.request

REPO = os.environ.get("INBOX_ZERO_REPO", "elie222/inbox-zero")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = ["Account", "EmailAccount", "Rule", "Action", "Newsletter", "ExecutedRule"]
ENUMS = ["ActionType", "LogicalOperator", "SystemType", "NewsletterStatus", "ExecutedRuleStatus"]


def get(url):
    headers = {"User-Agent": "zero-sum-upstream-check"}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def raw(tag, path):
    return get(f"https://raw.githubusercontent.com/{REPO}/{tag}/{path}")


def latest_release():
    """Highest vX.Y.Z tag, read with `git ls-remote` (no API rate limit; works through restrictive proxies)."""
    import subprocess
    out = subprocess.run(["git", "ls-remote", "--tags", "--refs", f"https://github.com/{REPO}.git"],
                         capture_output=True, text=True, timeout=60, check=True).stdout
    tags = re.findall(r"refs/tags/(v\d+\.\d+\.\d+)$", out, re.M)
    return max(tags, key=lambda t: tuple(map(int, t[1:].split("."))))


def block(schema, kind, name):
    m = re.search(rf"^{kind} {name} \{{.*?^\}}", schema, re.S | re.M)
    return m.group(0) if m else f"({kind} {name} not found)"


def env_names(src):
    return "\n".join(sorted(set(re.findall(r"^\s{2,}([A-Z][A-Z0-9_]+):\s*z\.", src, re.M))))


def compose_shape(src):
    keep = []
    for line in src.splitlines():
        s = line.rstrip()
        if re.match(r"^  [a-z][a-z0-9-]*:$", s) or re.match(r"^networks:|^  inbox-zero-network:", s):
            keep.append(s)
        keep += [f"endpoint {u}" for u in re.findall(r"http://web:3000(/api/[A-Za-z0-9/_-]+)", s)]
    return "\n".join(sorted(set(keep)))


def watched(tag):
    schema = raw(tag, "apps/web/prisma/schema.prisma")
    items = {f"model {m}": block(schema, "model", m) for m in MODELS}
    items.update({f"enum {e}": block(schema, "enum", e) for e in ENUMS})
    items["utils/encryption.ts"] = raw(tag, "apps/web/utils/encryption.ts")
    items["env.ts variable names"] = env_names(raw(tag, "apps/web/env.ts"))
    items["docker-compose services/endpoints"] = compose_shape(raw(tag, "docker-compose.yml"))
    return items


def main(argv):
    try:
        old = argv[1] if len(argv) > 1 else open(os.path.join(HERE, "inbox-zero.version")).read().strip()
        new = argv[2] if len(argv) > 2 else latest_release()
        if old == new:
            print(f"up to date: {old}")
            return 0
        a, b = watched(old), watched(new)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 2
    changed = 0
    print(f"Inbox Zero {old} -> {new}")
    for k in a:
        if a[k] == b[k]:
            print(f"  same     {k}")
            continue
        changed += 1
        print(f"  CHANGED  {k}")
        for line in difflib.unified_diff(a[k].splitlines(), b[k].splitlines(), old, new, lineterm="", n=1):
            print(f"    {line}")
    print(f"{changed} watched item(s) changed" if changed else "nothing Zero Sum depends on changed: bump inbox-zero.version")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
