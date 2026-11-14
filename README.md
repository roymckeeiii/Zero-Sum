# Zero Sum: AI email cleanup and one unified inbox for all your Inbox Zero accounts

**Got five Gmail accounts? Ten? Years of mail you never cleaned up?** Zero Sum is a free, open-source, self-hosted add-on
for [Inbox Zero](https://github.com/elie222/inbox-zero), the AI email assistant. It puts **every connected email account
in one place** (one inbox, one cleanup list, one quarantine, one analytics dashboard) and uses AI to clean up your
**entire mail history**, sender by sender, keeping what matters and clearing what doesn't.

> *Zero Sum adds up all your inboxes so the total can reach zero.*

---

## How a cleanup works

1. **Zero Sum reads your whole history, slowly.** Running on your own server, it reads a little more of every account
   every 15 minutes, all the way back to each account's first email, gently enough to stay inside Gmail's limits.
   A progress bar shows how far it has got.
2. **The AI plans each sender.** As history comes in, a background analyst works through your senders, biggest first,
   and proposes a plan for each: which emails to keep and what to do with the rest.
3. **You review.** Each plan shows its reasoning and a preview of the sender's 20 most recent emails with the action each
   would get. Change anything you like; the preview updates as you edit.
4. **You apply.** Existing mail is sorted across every folder and year, and future mail from that sender is handled the
   same way automatically, in every account.
5. **You clear the quarantine.** Doubtful mail waits on one Quarantine page; skim it, then delete it all in one click.

Nothing in your mailbox changes until you click Apply or confirm an action.

## Who it's for

Inbox Zero is excellent at taming *one* inbox. It handles each linked account on its own: rules, history and bulk
unsubscribe live per account, and you switch accounts to work through each one. That's fine with two accounts. With
many, or with a decade of unread promotions, it becomes the chore you were trying to escape.

Zero Sum is for you if you:

- run **multiple Gmail accounts**: personal, work, a business or two, side projects, an old address that still gets mail;
- manage **several Google Workspace mailboxes** (support@, sales@, hello@) and want them in one view;
- have **years of mail you never cleaned up** and don't want to go through it by hand;
- keep **unsubscribing from the same newsletter** separately in every account;
- get mail from senders that send **both things you need and junk** (orders and promos, articles and ads);
- **self-host Inbox Zero** already, or want to, and like keeping your email data on your own server.

## What you get

### ✨ AI Cleanup: smart plans, not on/off
Most senders mix mail you need with mail you don't. A restaurant sends your **order confirmations** and a flood of
**promotions**; a store sends **shipping updates** and **daily deals**. "Unsubscribe or keep" is the wrong choice for
them, so the AI proposes a plan per sender instead, in one of two ways:

**Word groups** when the subjects tell them apart. For a restaurant's mail, for example:

| Group | Subject words | Action |
|---|---|---|
| Orders | order, receipt, pickup | keep in the inbox and star |
| Points | points, rewards | archive |
| Everything else | | quarantine for review |

These become Gmail filters: instant, free, and they keep working on future mail.

**AI sort** when they don't. A travel site sends great articles *and* booking spam, and "10 hidden beaches in Portugal"
vs "Book now and save on Bali" share no useful words. The plan then says in plain English what to keep, for example
*"keep travel articles, guides and trip ideas; sales, deals and booking promos are junk"*, and the AI reads each email's
subject and preview to decide: all of the existing mail, and new mail from that sender every 15 minutes.

Every plan is editable (groups, words, actions, the rule itself), and every plan card expands to **preview the 20 most
recent emails** with the action each would get, or the AI's verdict on each. You can switch a plan between the two
modes, re-analyze it, skip it, or apply it.

### Slow and complete, not one big grab
Because it runs on your own server, Zero Sum takes its time. It reads your mail **back to each account's very first
email**, a batch per account every 15 minutes, and keeps each email's sender, subject and preview locally. The AI plans
from that stored history (everything a sender ever sent you, not a recent sample) and AI sort judges stored text, so
cleaning up years of mail doesn't hammer Gmail. The AI Cleanup page shows how much of each mailbox has been read and
how many senders are analyzed, with a daily cap on AI calls to keep costs predictable.

### One unified inbox for every account
Every account's newest mail in a single list, newest first, colour-coded by account. Read, archive, star, delete
(with undo), mark read/unread, search with Gmail syntax across all accounts at once, and see which Inbox Zero rule
handled each email. Keyboard shortcuts included: `j`/`k`, `e` archive, `s` star, `#` delete, `/` search.

### Cross-account cleanup, one sender at a time
A single list of **every sender across every account**, with volume, read rate and which mailboxes they hit. Click a
sender to see their 20 most recent emails. A **Clean up…** menu applies to every account at once:

- **Unsubscribe** (RFC 8058 one-click where the sender supports it), **Unsubscribe & archive / quarantine / delete**
- **Quarantine**, **Auto-archive**, **Archive**, **Delete all**, **Block** (straight to Trash), **Report spam**
- **Auto-star** the senders that matter, and **Protect** important senders or whole domains (`@yourbank.com`): now and
  for all future mail they can never be bulk-cleared by accident, and Gmail will never send them to Spam

Every action asks first, showing how many emails and accounts it will touch, and can **keep emails whose subject
contains** certain words (*gift*, *order*, *receipt*): those stay in your inbox and get starred, while the rest is
cleared. Or click **✨ AI suggest** and let the AI read the sender's subjects and fill that in for you. Actions reach
your entire history in every folder, not just the inbox.

### Quarantine: review first, then clear everything at once
Not sure a sender is junk? **Quarantine** them. Their mail, now and in future, skips the inbox in every account and
lands on one Quarantine page, grouped by sender. Skim it whenever you like, then **Archive all** or **Delete all**
across every account in one click, or **Release** a sender you want back.

### Email analytics across all your mailboxes
Mail volume per day, read rate, top senders and top domains, what Inbox Zero's AI rules did, and how many senders
you've unsubscribed from. Merged totals plus a per-account breakdown, so you can see where the noise comes from.

### Share Inbox Zero rules between accounts
Write an Inbox Zero rule once on your main account and share it with the accounts that need it: tick a box per rule and
account. Labels are matched by name and created where missing. Nothing is shared unless you choose it, because every
address gets different mail.

## Zero Sum vs switching accounts in Inbox Zero

| | Inbox Zero alone | Inbox Zero + Zero Sum |
|---|---|---|
| Read mail from all accounts | switch account by account | one unified inbox |
| Clean up years of old mail | by hand, per account | AI plans per sender, applied to every account |
| Senders with good mail *and* junk | keep or unsubscribe | keep the good part, clear the rest |
| Bulk unsubscribe a sender | once per account | once, applied to every account |
| Review junk before deleting | per account | one Quarantine page for all accounts |
| Analytics | per account | merged and per account |
| Inbox Zero rules | written per account | written once, shared where you choose |
| Protect important senders | | addresses or whole domains |

Zero Sum doesn't replace Inbox Zero: Inbox Zero still does the AI sorting, drafting and rules on each account. Zero Sum
gives you one place to see and clean all of them.

## Why self-hosted

- **Your mail stays on your server.** Zero Sum talks to Gmail directly from your machine. The only thing that leaves it
  is what the AI features send to the AI provider you choose (see the FAQ), and you can leave those off.
- **It can take its time**: a full, gentle pass over your whole history instead of a quick sample.
- **Free and open source** (AGPL-3.0), no subscription. AI costs are yours and small (see the FAQ).
- **Drops in next to Inbox Zero** with one extra docker-compose file, and works with the Google accounts you've already
  connected there: no new OAuth app to set up.
- **Kept current with Inbox Zero releases**: it pins the Inbox Zero version it's tested with and ships a checker that
  flags upstream changes before you upgrade.

## Quick start

You need a self-hosted Inbox Zero with your Google accounts connected and an authenticating reverse proxy (Cloudflare
Access, oauth2-proxy, ...). Then, inside the Inbox Zero checkout:

```sh
git clone https://github.com/roymckeeiii/zero-sum.git zero-sum
cp zero-sum/.env.example .env.zero-sum      # set ALLOWED_EMAILS, a database password, ...
                                            # and OPENROUTER_API_KEY to turn on the AI features
docker compose -f docker-compose.yml -f zero-sum/compose.zero-sum.yml run --rm zero-sum python app.py init-db
docker compose -f docker-compose.yml -f zero-sum/compose.zero-sum.yml up -d
```

Open it through your proxy, go to **AI Cleanup** and click **Analyze now**, or just let the background analyst work
through your history. Full details are under [Install](#install-docker-compose) below.

## FAQ

**Can Inbox Zero show all my email accounts in one inbox?** Not on its own: it handles each connected account
separately and you switch between them. Zero Sum adds the combined view on top of your existing Inbox Zero.

**Will it delete anything without asking?** No. Plans change nothing until you click Apply, every action asks first,
deleting means Gmail's Trash (recoverable for 30 days), and protected senders can't be bulk-cleared.

**What does the AI see?** Only what it needs to judge mail: a sender's address and its emails' **subject lines and
preview text** (the first ~200 characters Gmail shows), sent to the OpenAI-compatible API you configure (OpenRouter by
default, using a small Claude model). Never full email bodies, attachments or your other accounts' details. Without an
API key the AI features are simply off and everything else works.

**How much does the AI cost?** Very little: a plan is one small request, and AI sort judges 40 emails per request.
A daily cap (`AI_MAX_CALLS_PER_DAY`, 400 by default) limits the worst case to well under a dollar a day with the
default model.

**How long does the first pass take?** It reads 2,000 emails per account every 15 minutes, and accounts are read side by
side, so a mailbox of 100,000 emails takes about half a day and very old, very full accounts a day or two. You can start
cleaning your biggest senders right away; plans improve as more history arrives.

**Does it work with Google Workspace accounts?** Yes, any Gmail or Google Workspace account connected in Inbox Zero.
Microsoft/Outlook accounts are not supported yet.

**Does it send email?** Never. Replies happen in Gmail; Zero Sum only reads, labels, archives and trashes.

**Is it an official Inbox Zero product?** No. It's an independent add-on that works alongside Inbox Zero.

---

<sub>Keywords: Inbox Zero add-on, Inbox Zero unified inbox, Inbox Zero multiple accounts, AI email cleanup, AI inbox
cleaner, clean up old Gmail, delete old promotional emails, unified inbox for multiple Gmail accounts, combine Gmail
inboxes, manage multiple email accounts in one place, multi-account email client, bulk unsubscribe across all accounts,
mass unsubscribe Gmail, cross-account email cleanup, email quarantine, smart Gmail filters, email analytics dashboard,
self-hosted email dashboard, open-source email management, AI email assistant, Gmail inbox zero, Google Workspace
shared inbox.</sub>

Zero Sum is an independent project, not affiliated with or endorsed by Inbox Zero Inc.

## Features in detail

| Tab | What it does |
|---|---|
| **Inbox** | The newest inbox mail from every account in one list. Read, archive, star, delete (to Trash, with undo), mark read/unread, search (Gmail syntax), filter by account, and see which Inbox Zero rule handled each email. Keys: `j`/`k` move, `e` archive, `s` star, `#` delete, `u` unread, `/` search. Replies happen in Gmail ("Reply in Gmail" opens the thread): Zero Sum never sends mail. |
| **Cleanup** | Every sender across every account, with volume, read rate and which accounts get it. Click a sender for its 20 most recent emails. The **Clean up…** menu applies to every account that receives the sender: **Unsubscribe** (only that: RFC 8058 one-click where offered, otherwise links to open), **Unsubscribe & archive / quarantine / delete**, **Quarantine**, **Auto-archive** (Gmail `from:` filter + Inbox Zero's sender status), **Archive**, **Delete all** (to Trash), **Block** (to Trash now and in future), **Spam** (report, which also trains Gmail). Each asks first and can keep emails whose subject contains chosen words (kept ones are starred, now and in future). Also **Auto-star** / **Un-star**, **Keep**, **Protect** and **✨ AI suggest**. Date range up to all time. |
| **✨ AI Cleanup** | Per-sender plans from the AI, in two modes: **word groups** (subject words → keep & star / archive / quarantine / delete, plus an action for everything else, applied as Gmail filters) or **AI sort** (a plain-English rule the AI applies to each email's subject and preview, for existing mail and new mail every 15 minutes, with progress and Stop). Edit, preview the 20 most recent emails, apply, skip or re-analyze. A background analyst plans senders as history arrives; a progress panel shows mail read and senders analyzed. |
| **Quarantine** | Mail held by Quarantine actions and plans, under a `Quarantine` label (`QUARANTINE_LABEL`), grouped by sender across all accounts: tick messages or senders, then **Archive** or **Delete** (to Trash), or **Archive all** / **Delete all**; **Release sender** puts their mail back in the inbox and removes the filter. |
| **Analytics** | Mail per day, read rate, top senders and domains, what Inbox Zero's rules did and cleanup totals, merged with a per-account breakdown. |
| **Rules** | Write rules in Inbox Zero on a *master* account and share chosen ones with chosen accounts (opt-in: tick a rule under an account). Label actions are matched by label name; preview, sync now, and optional automatic sync. Nothing is shared by default, since each address usually gets different mail. |

### Delete, block and protect

- Deleting moves mail to **Trash**; Gmail empties Trash after 30 days, so mistakes can be undone until then. (Permanent
  deletion would need the full `https://mail.google.com/` scope, which Inbox Zero does not request.)
- Every bulk change (delete, block, archive, quarantine, spam, a plan, an AI sort) is recorded with the exact messages
  it touched before Gmail is called. **Cleanup > Undo a bulk action** lists the last 28 days and puts a change's
  messages back where they were. Undo does not remove filters: use Keep or Reset on the sender for that.
- Every Clean up action and every plan asks for confirmation, with the number of emails and accounts affected.
- **Protected** senders (an address, or a whole domain like `@yourbank.com`) can't be unsubscribed, archived, deleted,
  blocked, quarantined or marked as spam, by hand or by a plan: the buttons are disabled and the server refuses. The
  background analyst skips them. Protecting adds Gmail's "never send to Spam" filter in every account and marks the
  address "kept" in Inbox Zero. Auto-star still works on protected senders.
- Every Gmail filter Zero Sum creates is written down (`unified.filter`: account, filter id, sender, kind). **Protect**,
  **Keep**, **Reset** and applying a new action or plan remove exactly its own filters for that sender (Trash,
  archive, quarantine and keep-star alike) and never a filter you made yourself in Gmail; if one of yours still moves
  the sender's mail, the result says so. Filters created by a version that kept no record are adopted once with
  `python app.py adopt-filters` (a report; add `--apply` to record them, `--all` to include filters of the app's exact
  shape for senders it has no clean-up status for). Adoption only records: it never deletes or changes a filter.

## How it works (for the technically curious)

- It reads the Google tokens Inbox Zero already stores and decrypts them the same way Inbox Zero does (AES-256-GCM with
  a key derived from `EMAIL_ENCRYPT_SECRET` / `EMAIL_ENCRYPT_SALT`). Refreshed access tokens stay in memory: it never
  writes Inbox Zero's account table.
- It calls the Gmail API directly, rate-limited per account (`GMAIL_UNITS_PER_SEC`, well under Google's per-user quota,
  which Inbox Zero shares).
- It uses its own database role (`grants.sql`): read access to a few account columns and to `ExecutedRule`; write
  access only to `Rule`, `Action`, `Newsletter` and its own `unified` schema.
- A collector stores message metadata (sender, subject, preview, date, read/inbox state, unsubscribe header) every 15
  minutes, back-filling the whole history gradually (to each account's first email, `BACKFILL_STEP` per account per run).
  The Cleanup, Analytics and AI features read from this local copy.
- AI plans: one request per sender with its subject lines (counted and de-duplicated, up to 80 distinct). AI sort: 40
  emails (subject + preview) per request; verdicts are reduced to "keep these numbers". Word-group plans become ordered
  Gmail filters (`query` / `negatedQuery`), so the first matching group wins for future mail too; AI-sorted senders are
  re-checked every collector run. Plans and sort state live in Zero Sum's own schema (`ai_plan`, `ai_sort`).
- Every AI call counts against a daily cap (`AI_MAX_CALLS_PER_DAY`); past it, background work waits for the next day.
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
# owner, e.g. postgresql://postgres:<pw>@db:5432/inboxzero), ALLOWED_EMAILS, MASTER_EMAIL, INBOX_ZERO_URL,
# and OPENROUTER_API_KEY (or another OpenAI-compatible API) for the AI features

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
| `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` | unset | behind Cloudflare Access: your team domain (`<team>.cloudflareaccess.com`) and the Access application's AUD tag. With both set, Zero Sum verifies the signed `Cf-Access-Jwt-Assertion` token and takes the user from it; the plain e-mail header is ignored. Recommended: without it, anything that can reach the port can claim to be you. |
| `MASTER_EMAIL` | empty (rule sync off) | account whose rules are copied to the others |
| `INBOX_ZERO_URL` | empty | your Inbox Zero address, for links |
| `ADMIN_DATABASE_URL` | | only for `init-db` |
| `PORT` | `8080` | inside the container |
| `COLLECT_EVERY_MIN`, `RULES_EVERY_MIN` | `15`, `10` | background jobs |
| `BACKFILL_DAYS`, `BACKFILL_STEP` | `0` (all history), `2000` | how far back the collector reads (days; 0 = to the account's first email), and how much per run per account |
| `MAX_BULK` | `200000` | most emails one bulk action touches per account |
| `OPENROUTER_API_KEY`, `AI_MODEL`, `AI_BASE_URL` | empty, `anthropic/claude-haiku-5.5`, OpenRouter | all AI features: AI Cleanup, AI sort, AI suggest, background analyst (off without a key) |
| `AI_MAX_CALLS_PER_DAY`, `AI_PLANS_PER_RUN`, `AI_MIN_EMAILS` | `400`, `4`, `5` | daily AI call cap; senders the background analyst plans per run; minimum emails before a sender is planned |
| `QUARANTINE_LABEL`, `QUARANTINE_SHOW` | `Quarantine`, `600` | Gmail label for held mail; messages per account listed on the Quarantine tab |
| `GMAIL_UNITS_PER_SEC` | `50` | Gmail quota units per second per account |

## Security notes

- Zero Sum can read every linked mailbox, as Inbox Zero can. Run it only behind authentication, for yourself.
- Email bodies are shown in a sandboxed iframe with no scripts; remote images are blocked until you click "Show images".
- POST requests need a custom header, so other sites cannot submit actions with your session.
- Email text sent to the AI is treated as untrusted: prompts say so, and answers are reduced to a fixed set of actions,
  cleaned keywords and message numbers before anything is shown. Nothing the AI returns runs without your confirmation.
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
