# Zero Sum

**A unified inbox add-on for self-hosted [Inbox Zero](https://github.com/elie222/inbox-zero).** One inbox, one
cleanup, one set of rules across all your Gmail accounts.

Inbox Zero handles each linked email account on its own. Zero Sum runs next to it and works across all of them:

| Tab | What it does |
|---|---|
| **Inbox** | The newest inbox mail from every account in one list. Read, archive, star, delete (to Trash, with undo), mark read/unread, search (Gmail syntax), filter by account, and see which Inbox Zero rule handled each email. Keys: `j`/`k` move, `e` archive, `s` star, `#` delete, `u` unread, `/` search. Replies happen in Gmail ("Reply in Gmail" opens the thread): Zero Sum never sends mail. |
| **Cleanup** | Every sender across every account, with volume, read rate and which accounts get it, and one-click actions applied to every account that receives that sender: **Unsubscribe** (RFC 8058 one-click where offered, otherwise links to open), **Auto-archive** (Gmail `from:` filter + Inbox Zero's sender status), **Archive**, **Delete all** (to Trash), **Block** (to Trash now and in future), **Spam** (report, which also trains Gmail), **Auto-star** / **Un-star** (star now and in future), **Keep**, and **Protect**. |
| **Analytics** | Mail per day, read rate, top senders and domains, what Inbox Zero's rules did and cleanup totals, merged with a per-account breakdown. |
| **Rules** | Write rules in Inbox Zero on a *master* account and share chosen ones with chosen accounts (opt-in: tick a rule under an account). Label actions are matched by label name; preview, sync now, and optional automatic sync. Nothing is shared by default, since each address usually gets different mail. |

Zero Sum is an independent project, not affiliated with or endorsed by Inbox Zero Inc.

### Delete, block and protect

- Deleting moves mail to **Trash**; Gmail empties Trash after 30 days, so mistakes can be undone until then. (Permanent
  deletion would need the full `https://mail.google.com/` scope, which Inbox Zero does not request.)
- Delete all, Block and Spam ask for confirmation, with the number of emails and accounts affected.
- **Protected** senders (an address, or a whole domain like `@yourbank.com`) can't be unsubscribed, archived, deleted,
  blocked or marked as spam from the Cleanup tab: the buttons are disabled and the server refuses. Protecting an
  address also marks it "kept" in Inbox Zero. Auto-star still works on protected senders.

## How it works

- It reads the Google tokens Inbox Zero already stores and decrypts them the same way Inbox Zero does (AES-256-GCM with
  a key derived from `EMAIL_ENCRYPT_SECRET` / `EMAIL_ENCRYPT_SALT`). Refreshed access tokens stay in memory: it never
  writes Inbox Zero's account table.
- It calls the Gmail API directly, rate-limited per account (`GMAIL_UNITS_PER_SEC`, well under Google's per-user quota,
  which Inbox Zero shares).
- It uses its own database role (`grants.sql`): read access to a few account columns and to `ExecutedRule`; write
  access only to `Rule`, `Action`, `Newsletter` and its own `unified` schema.
- A collector stores message metadata (sender, date, read/inbox state, unsubscribe header) every 15 minutes for the
  Cleanup and Analytics tabs, back-filling the last 180 days gradually.
- Rule sync records which copy belongs to which master rule (with a content hash), so it only ever updates or deletes
  its own copies. Automatic sync is off until you turn it on in the Rules tab.

## Requirements

- Self-hosted Inbox Zero with Google accounts, run with its `docker-compose.yml` (its `db` service and
  `inbox-zero-network`). Tested with Inbox Zero **v2.25.8**; Zero Sum writes some Inbox Zero tables directly, so check
  [Upgrading Inbox Zero](#upgrading-inbox-zero) before upgrading.
- An authenticating reverse proxy in front of it (Cloudflare Access, oauth2-proxy, ...). Zero Sum has **no login page**:
  it trusts the e-mail address in `AUTH_EMAIL_HEADER` and only lets in `ALLOWED_EMAILS`. Never expose its port directly.

## Install (Docker Compose)

Inside your Inbox Zero checkout:

```sh
git clone https://github.com/roymckeeiii/zero-sum.git zero-sum
cp zero-sum/.env.example .env.zero-sum
# edit .env.zero-sum: DATABASE_URL (pick a password for the iz_unified role), ADMIN_DATABASE_URL (Inbox Zero's database
# owner, e.g. postgresql://postgres:<pw>@db:5432/inboxzero), ALLOWED_EMAILS, MASTER_EMAIL, INBOX_ZERO_URL

docker compose -f docker-compose.yml -f zero-sum/compose.zero-sum.yml run --rm zero-sum python app.py init-db
docker compose -f docker-compose.yml -f zero-sum/compose.zero-sum.yml up -d
```

It listens on `127.0.0.1:3101` (`ZERO_SUM_BIND_HOST` / `ZERO_SUM_PORT`). Point your auth proxy at it. Once `init-db` has
run, `ADMIN_DATABASE_URL` can be removed from `.env.zero-sum`.

The secrets it shares with Inbox Zero (`EMAIL_ENCRYPT_SECRET`, `EMAIL_ENCRYPT_SALT`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`) come from Inbox Zero's own env file (`INBOX_ZERO_ENV_FILE`, default `apps/web/.env`).

## Configuration

All settings are environment variables; see [`.env.example`](.env.example).

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | required | connection as the `iz_unified` role |
| `EMAIL_ENCRYPT_SECRET`, `EMAIL_ENCRYPT_SALT` | required | Inbox Zero's values |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | required | Inbox Zero's Google OAuth client |
| `ALLOWED_EMAILS` | required | comma-separated addresses allowed in |
| `AUTH_EMAIL_HEADER` | `Cf-Access-Authenticated-User-Email` | header your proxy sets (oauth2-proxy: `X-Forwarded-Email`) |
| `MASTER_EMAIL` | empty (rule sync off) | account whose rules are copied to the others |
| `INBOX_ZERO_URL` | empty | your Inbox Zero address, for links |
| `ADMIN_DATABASE_URL` | | only for `init-db` |
| `PORT` | `8080` | inside the container |
| `COLLECT_EVERY_MIN`, `RULES_EVERY_MIN` | `15`, `10` | background jobs |
| `BACKFILL_DAYS`, `BACKFILL_STEP` | `180`, `400` | history the collector gathers, per run per account |
| `GMAIL_UNITS_PER_SEC` | `50` | Gmail quota units per second per account |

## Security notes

- Zero Sum can read every linked mailbox, as Inbox Zero can. Run it only behind authentication, for yourself.
- Email bodies are shown in a sandboxed iframe with no scripts; remote images are blocked until you click "Show images".
- POST requests need a custom header, so other sites cannot submit actions with your session.
- The container runs read-only as an unprivileged user with all capabilities dropped.

## Inbox Zero versions

[`inbox-zero.version`](inbox-zero.version) names the Inbox Zero release this version of Zero Sum is tested with.
Zero Sum reads and writes some Inbox Zero tables directly, so run that release (or check first):

```sh
python3 tools/check_upstream.py              # supported version vs Inbox Zero's latest release
python3 tools/check_upstream.py v2.25.8 v2.30.0
```

It compares only what Zero Sum depends on (the Prisma models and enums it uses, `utils/encryption.ts`, the
environment variable names and the docker-compose services/endpoints). Exit 0 = nothing relevant changed: bump
`inbox-zero.version`. Exit 1 = adapt Zero Sum first; the diff shows what changed.

## Development

```sh
pip install cryptography==43.0.3 "psycopg[binary]==3.2.3"
python3 -m unittest discover -s tests
```

## License

[AGPL-3.0](LICENSE), the same license as Inbox Zero. Inbox Zero's own license adds terms restricting commercial
monetization of Inbox Zero; Zero Sum is meant to be used alongside it on your own server.

Copyright (C) 2026 Roy McKee
