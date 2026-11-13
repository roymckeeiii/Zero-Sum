"""Shared plumbing for Zero Sum: Inbox Zero DB access, token decryption, Google token refresh, Gmail API calls.

Tokens: Inbox Zero stores Account.access_token / refresh_token encrypted by apps/web/utils/encryption.ts (v2.25.8):
AES-256-GCM, key = scrypt(EMAIL_ENCRYPT_SECRET, EMAIL_ENCRYPT_SALT, 32) with Node's defaults (N=16384, r=8, p=1),
stored as hex IV(16) + tag(16) + ciphertext. Refreshed access tokens are kept in memory only: this app never writes
the Account table, so it cannot break Inbox Zero's own logins.
"""
import hashlib, json, os, re, secrets, string, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
MASTER = os.environ.get("MASTER_EMAIL", "").strip().lower()  # whose rules are copied to the others; empty = no rule sync
POOL = ThreadPoolExecutor(max_workers=24)
_key = None


def db():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10, autocommit=False)


def new_id():
    """cuid-like id (Inbox Zero uses cuid(); any unique text works for its text primary keys)."""
    return "c" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(24))


def key():
    global _key
    if _key is None:
        _key = hashlib.scrypt(os.environ["EMAIL_ENCRYPT_SECRET"].encode(), salt=os.environ["EMAIL_ENCRYPT_SALT"].encode(),
                              n=16384, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    return _key


def decrypt(value):
    """Inbox Zero token: "v1:<hex>" (v2.30+, versioned keys; only v1 exists), legacy "<hex>", or plaintext."""
    if not value:
        return None
    m = re.fullmatch(r"v(\d+):([0-9a-fA-F]+)", value)
    if m:
        if m.group(1) != "1":
            raise ValueError(f"unknown Inbox Zero encryption version v{m.group(1)}: update Zero Sum")
        value = m.group(2)
    try:
        b = bytes.fromhex(value)
    except ValueError:
        return value if re.match(r"^(ya29\.|1//)", value) else None  # an unencrypted token
    try:
        return AESGCM(key()).decrypt(b[:16], b[32:] + b[16:32], None).decode()
    except Exception:  # noqa: BLE001 - wrong key / corrupted value
        return None


class Accounts:
    """The linked Gmail accounts (EmailAccount + its Google Account row), cached for a minute."""

    def __init__(self):
        self.lock, self.at, self.rows, self.tokens = threading.Lock(), 0.0, [], {}

    def _load(self):
        with self.lock:
            if time.time() - self.at > 60:
                with db() as c:
                    self.rows = [dict(zip(("id", "email", "name", "access", "refresh", "expires"), r)) for r in c.execute(
                        'SELECT ea.id, ea.email, ea.name, a.access_token, a.refresh_token, a.expires_at '
                        'FROM "EmailAccount" ea JOIN "Account" a ON a.id = ea."accountId" '
                        "WHERE a.provider = 'google' ORDER BY ea.email").fetchall()]
                self.at = time.time()

    def list(self):
        self._load()
        return [{"id": r["id"], "email": r["email"], "name": r["name"], "master": r["email"].lower() == MASTER}
                for r in self.rows]

    def master(self):
        return next((a for a in self.list() if a["master"]), None)

    def row(self, account_id):
        self._load()
        for r in self.rows:
            if r["id"] == account_id:
                return r
        raise KeyError("unknown account")

    def email(self, account_id):
        return self.row(account_id)["email"]

    def token(self, account_id, force=False):
        r = self.row(account_id)
        tok, exp = self.tokens.get(account_id, (None, 0))
        if tok and exp > time.time() + 60 and not force:
            return tok
        if not force and r["expires"] and r["expires"].timestamp() > time.time() + 120:
            tok = decrypt(r["access"])
            if tok:
                self.tokens[account_id] = (tok, r["expires"].timestamp())
                return tok
        refresh = decrypt(r["refresh"])
        if not refresh:
            raise PermissionError(f"no usable Google login for {r['email']}: re-link it in Inbox Zero")
        body = urllib.parse.urlencode({"client_id": os.environ["GOOGLE_CLIENT_ID"],
                                       "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                                       "refresh_token": refresh, "grant_type": "refresh_token"}).encode()
        try:
            with urllib.request.urlopen("https://oauth2.googleapis.com/token", body, timeout=20) as resp:
                d = json.load(resp)
        except urllib.error.HTTPError as e:
            raise PermissionError(f"Google refused the login for {r['email']} ({e.code}): re-link it in Inbox Zero")
        self.tokens[account_id] = (d["access_token"], time.time() + int(d.get("expires_in", 3600)))
        return d["access_token"]


ACC = Accounts()


class Bucket:
    """Per-account token bucket in Gmail quota units. Gmail allows 15,000 units per user per minute (250/s), and
    Inbox Zero spends from the same per-user quota, so this keeps well below: GMAIL_UNITS_PER_SEC (default 50)."""
    RATE = float(os.environ.get("GMAIL_UNITS_PER_SEC", "50"))

    def __init__(self):
        self.lock, self.tokens, self.at = threading.Lock(), self.RATE * 5, time.monotonic()

    def take(self, cost):
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.RATE * 5, self.tokens + (now - self.at) * self.RATE)
                self.at = now
                if self.tokens >= cost:
                    self.tokens -= cost
                    return
                wait = (cost - self.tokens) / self.RATE
            time.sleep(wait)


_buckets, _bucket_lock = {}, threading.Lock()


def _cost(method, path):
    """Approximate Gmail API quota units (developers.google.com/gmail/api/reference/quota)."""
    p = path.split("?")[0]
    if "batchModify" in p:
        return 50
    if p.startswith("/labels"):
        return 1 if method == "GET" else 5
    if method == "POST":
        return 10
    return 5


def gmail(account_id, path, method="GET", body=None, retry=True):
    with _bucket_lock:
        bucket = _buckets.setdefault(account_id, Bucket())
    req = urllib.request.Request(GMAIL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {ACC.token(account_id, force=not retry)}",
                                          "Content-Type": "application/json"})
    for attempt in range(6):
        bucket.take(_cost(method, path))
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 401 and retry:
                return gmail(account_id, path, method, body, retry=False)
            detail = e.read()[:300].decode("utf-8", "replace")
            low = detail.lower()
            limited = e.code == 429 or (e.code == 403 and ("ratelimit" in low.replace(" ", "") or "quota exceeded" in low))
            if (limited or e.code in (500, 503)) and attempt < 5:  # quota is per minute: back off up to a minute
                time.sleep(min(60, 4 * 2 ** attempt) if limited else 2 ** attempt)
                continue
            raise RuntimeError(f"Gmail {method} {path.split('?')[0]}: {e.code} {detail}") from None


def gmail_list(account_id, path, key_, limit):
    """Page through a Gmail list endpoint (messages, threads) up to `limit` items."""
    out, token = [], None
    sep = "&" if "?" in path else "?"
    while len(out) < limit:
        p = f"{path}{sep}maxResults={min(500, limit - len(out))}" + (f"&pageToken={token}" if token else "")
        d = gmail(account_id, p)
        out += d.get(key_, [])
        token = d.get("nextPageToken")
        if not token:
            break
    return out


_labels = {}


def labels(account_id, fresh=False):
    """{label id: name} for the account's user labels, cached 10 min."""
    t, m = _labels.get(account_id, (0, None))
    if fresh or m is None or time.time() - t > 600:
        m = {l["id"]: l["name"] for l in gmail(account_id, "/labels").get("labels", []) if l.get("type") == "user"}
        _labels[account_id] = (time.time(), m)
    return m


def label_id(account_id, name, create=True):
    """Gmail label id for `name` in this account; creates the label when missing (nested names like A/B work)."""
    for lid, n in labels(account_id).items():
        if n.lower() == name.lower():
            return lid
    for lid, n in labels(account_id, fresh=True).items():
        if n.lower() == name.lower():
            return lid
    if not create:
        return None
    d = gmail(account_id, "/labels", "POST", {"name": name, "labelListVisibility": "labelShow",
                                               "messageListVisibility": "show"})
    labels(account_id, fresh=True)
    return d["id"]


def header(headers, name):
    for h in headers:
        if h["name"].lower() == name:
            return h["value"]
    return ""


# The "unified" schema itself is created (owned by this app's role) by the setup script: the role has no CREATE right on
# the database, and CREATE SCHEMA IF NOT EXISTS checks that right even when the schema exists.
SCHEMA = """
CREATE TABLE IF NOT EXISTS unified.rule_map (
  master_rule_id text NOT NULL, account_id text NOT NULL, target_rule_id text NOT NULL,
  hash text NOT NULL, synced_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (master_rule_id, account_id));
CREATE TABLE IF NOT EXISTS unified.rule_include (
  master_rule_id text NOT NULL, account_id text NOT NULL, PRIMARY KEY (master_rule_id, account_id));
CREATE TABLE IF NOT EXISTS unified.log (
  id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(), kind text NOT NULL, account text, msg text NOT NULL);
CREATE TABLE IF NOT EXISTS unified.settings (k text PRIMARY KEY, v text NOT NULL);
CREATE TABLE IF NOT EXISTS unified.msg (
  account_id text NOT NULL, id text NOT NULL, thread_id text NOT NULL, ts timestamptz NOT NULL,
  from_addr text NOT NULL, from_name text, domain text NOT NULL, unread boolean NOT NULL, inbox boolean NOT NULL,
  unsub text, unsub_post boolean NOT NULL DEFAULT false, PRIMARY KEY (account_id, id));
CREATE INDEX IF NOT EXISTS msg_from ON unified.msg (from_addr);
ALTER TABLE unified.msg ADD COLUMN IF NOT EXISTS subject text;
ALTER TABLE unified.msg ADD COLUMN IF NOT EXISTS snippet text;
CREATE INDEX IF NOT EXISTS msg_ts ON unified.msg (ts);
CREATE TABLE IF NOT EXISTS unified.ai_plan (
  addr text PRIMARY KEY, plan jsonb NOT NULL, status text NOT NULL DEFAULT 'new', created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS unified.ai_sort (
  addr text PRIMARY KEY, rule text NOT NULL, action text NOT NULL, last_ts timestamptz, status text NOT NULL DEFAULT 'sorting',
  done int NOT NULL DEFAULT 0, total int NOT NULL DEFAULT 0, kept int NOT NULL DEFAULT 0, updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS unified.protected (
  key text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS unified.action_log (
  id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(), account_id text NOT NULL, note text NOT NULL DEFAULT '',
  add_labels text[] NOT NULL, remove_labels text[] NOT NULL, ids text[] NOT NULL, undone_at timestamptz);
CREATE TABLE IF NOT EXISTS unified.collect_state (
  account_id text PRIMARY KEY, newest timestamptz, oldest timestamptz, backfill_done boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now(), error text);
"""


def init_schema():
    with db() as c:
        c.execute(SCHEMA)


def log(kind, msg, account=None):
    print(f"[{kind}] {account or '-'} {msg}", flush=True)
    try:
        with db() as c:
            c.execute("INSERT INTO unified.log (kind, account, msg) VALUES (%s, %s, %s)", (kind, account, msg[:2000]))
            c.execute("DELETE FROM unified.log WHERE at < now() - interval '30 days'")
    except Exception:  # noqa: BLE001 - logging must not break the caller
        pass


def setting(k, default=None):
    with db() as c:
        r = c.execute("SELECT v FROM unified.settings WHERE k = %s", (k,)).fetchone()
    return r[0] if r else default


def set_setting(k, v):
    with db() as c:
        c.execute("INSERT INTO unified.settings (k, v) VALUES (%s, %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
                  (k, str(v)))
