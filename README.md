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
2. **The AI plans each sender.** Click **Analyze now**, or turn on the background analyst (off until you do), which then
   works through your senders as history comes in, biggest first. Each plan proposes which emails to keep and what to
   do with the rest.
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
Every account's newest mail in a single list, newest first, colour-coded by account. Read, archive and delete
(each with undo), star, mark read/unread, search with Gmail syntax across all accounts at once (Inbox, All mail, Sent, one label, or Spam & Trash), and see which Inbox Zero rule
handled each email. Keyboard shortcuts included: `j`/`k`, `e` archive, `s` star, `#` delete, `/` search. Without a
mouse, Tab reaches each message (Enter opens it, and its buttons show while it has focus), each account and each
sender in Cleanup; fields and tick boxes are named for screen readers.

### Cross-account cleanup, one sender at a time
A single list of **every sender across every account**, with volume, read rate and which mailboxes they hit. Click a
sender to see their 20 most recent emails. A **Clean up…** menu applies to every account at once:

- **Unsubscribe** (RFC 8058 one-click where the sender supports it), **Unsubscribe & archive / quarantine / delete**
- **Quarantine**, **Auto-archive**, **Archive**, **Delete all**, **Block** (straight to Trash), **Report spam**
- **Auto-star** the senders that matter, and **Protect** important senders or whole domains (`@yourbank.com`): now and
  for all future mail they can never be bulk-cleared by accident, and Gmail will never send them to Spam

Every action asks first, showing how many emails it will touch in each account (counted in Gmail at that moment,
with the same search the action runs, so it covers every folder and year, not just the recent mail in the list), and
can **keep emails whose subject contains** certain words (*gift*, *order*, *receipt*): those stay in your inbox and get starred, while the rest is
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

Open it through your proxy, go to **AI Cleanup** and click **Analyze now**, or tick "Analyze senders in the background"
to let the background analyst work through your history (it is off until you turn it on). Full details are under [Install](#install-docker-compose) below.

## FAQ

**Can Inbox Zero show all my email accounts in one inbox?** Not on its own: it handles each connected account
separately and you switch between them. Zero Sum adds the combined view on top of your existing Inbox Zero.

**Will it delete anything without asking?** No. Plans change nothing until you click Apply, every action asks first,
deleting means Gmail's Trash (recoverable for 30 days), and protected senders can't be bulk-cleared.

**What does the AI see?** Only what it needs to judge mail: a sender's address and its emails' **subject lines and
preview text** (the first ~200 characters Gmail shows), sent to the OpenAI-compatible API you configure (OpenRouter by
default, using a small Claude model). Never full email bodies, attachments or your other accounts' details. Without an
API key the AI features are simply off and everything else works. With a key, nothing is sent until you use an AI
feature: the background analyst, which sends subject lines with no click, stays off until you turn it on.

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
| **Inbox** | The newest inbox mail from every account in one list. Read, archive and delete (to Trash), each with undo, star, mark read/unread, search (Gmail syntax) every account at once in Inbox, All mail, Sent, a label or Spam & Trash (typing a search searches All mail; results show their folder, sent mail its recipients; "Load more" keeps one newest-first order across accounts), filter by account, and see which Inbox Zero rule handled each email. Keys: `j`/`k` move, `e` archive, `s` star, `#` delete, `u` unread, `/` search. Replies happen in Gmail ("Reply in Gmail" opens the thread): Zero Sum never sends mail. On a phone the reader's buttons are compact and drop the key hints. |
| **Cleanup** | Every sender across every account, with volume, read rate and which accounts get it. Click a sender for its 20 most recent emails. The **Clean up…** menu applies to every account that receives the sender: **Unsubscribe** (only that: RFC 8058 one-click where offered, otherwise links to open), **Unsubscribe & archive / quarantine / delete**, **Quarantine**, **Auto-archive** (Gmail `from:` filter + Inbox Zero's sender status), **Archive**, **Delete all** (to Trash), **Block** (to Trash now and in future), **Spam** (report, which also trains Gmail). Each asks first and can keep emails whose subject contains chosen words (kept ones are starred, now and in future). Also **Auto-star** / **Un-star**, **Keep**, **Protect** and **✨ AI suggest**. Date range up to all time. On a phone each sender is a card with its numbers on one line and its actions underneath, so nothing needs sideways scrolling; a sender's recent emails show each subject on its own line (up to two), with the account and date under it. |
| **✨ AI Cleanup** | Per-sender plans from the AI, in two modes: **word groups** (subject words → keep & star / archive / quarantine / delete, plus an action for everything else, applied as Gmail filters) or **AI sort** (a plain-English rule the AI applies to each email's subject and preview, for existing mail and new mail every 15 minutes, with progress and Stop). Edit, preview the 20 most recent emails, apply, skip or re-analyze. A plan that was only partly done (some senders not looked up in one go) stays open as "partly applied" with its **Apply plan** button, so it can simply be applied again (also when an account failed, or another action on that sender was still running, and when an AI sort could not remove the sender's earlier Gmail filters in an account). Its Gmail filters are active meanwhile, and the page says so: **Stop here** (in place of Skip) marks it applied and keeps what was done and its filters (Keep or Reset on the sender removes them), and **Re-analyze** keeps it marked partly applied until the new plan is applied, which replaces those filters. **Review again** on an applied plan opens it marked the same way while its filters are still active, so skipping it then stops it again rather than marking it skipped; once Keep or Reset on the sender has removed them, it opens as a new plan. An optional background analyst (off until you turn it on, and only with AI configured) plans senders as history arrives; a progress panel shows mail read and senders analyzed (against Gmail's mailbox size, which also counts sent mail and drafts, and "unknown" when Gmail could not be asked). |
| **Quarantine** | Mail held by Quarantine actions and plans, under a `Quarantine` label (`QUARANTINE_LABEL`), grouped by sender across all accounts: tick messages or senders, then **Archive** or **Delete** (to Trash), or **Archive all** / **Delete all**; **Release sender** puts their mail back where it was (the inbox, or the archive for mail that was already archived when it was quarantined) and removes the filter. Quarantine and Spam over every folder are recorded as two changes, the inbox part and the archived part, so undoing one never puts long-archived mail in the inbox. A ticked message is only acted on if Gmail still shows it under the label (the result counts any that were not and left them alone). A sender's action touches only mail really from that address: Gmail's `from:` search also matches longer addresses and display names, and that mail stays where it is (counted in the result). On a phone each email's subject gets its own line and the buttons sit in two even columns. |
| **Analytics** | Mail per day (calendar days in your browser's time zone), read rate, top senders and domains, what Inbox Zero's rules did and cleanup totals, merged with a per-account breakdown. On a phone the per-account numbers are listed under each account, and the chart is drawn to the screen's width so its labels stay readable. |
| **Rules** | Write rules in Inbox Zero on a *master* account and share chosen ones with chosen accounts (opt-in: tick a rule under an account). Label actions are matched by label name; preview, sync now, and optional automatic sync. Nothing is shared by default, since each address usually gets different mail. |

### Delete, block and protect

- Deleting moves mail to **Trash**; Gmail empties Trash after 30 days, so mistakes can be undone until then. (Permanent
  deletion would need the full `https://mail.google.com/` scope, which Inbox Zero does not request.)
- Every bulk change (delete, block, archive, quarantine, spam, a plan, an AI sort) is recorded with the exact messages
  it touched before Gmail is called. **Cleanup > Undo a bulk action** lists the last 28 days and puts a change's
  messages back where they were. Undo does not remove filters: use Keep or Reset on the sender for that. The one
  exception: mail from a sender protected since the change (or your own address) is never put back out of view, so
  undoing a delete from Quarantine brings that mail out of Trash into the inbox instead, and the result says how many. The undo answers as soon as
  the mail is restored, and the local history marks it as in the inbox at once. Then, in the background, up to
  `UNDO_REFRESH` (default 500) of the restored messages are read back from Gmail, a few at a time and only while
  nothing else is using the account's Gmail quota, so mail Gmail has in Trash or Spam now (e.g. an archive undone
  after the mail was deleted by hand) is shown as out of the inbox again, and deleted mail shows its sender again.
  Past that cap, restored mail stays marked as in the inbox: the collector's refresh corrects mail dated within the
  last 30 days, but older mail that was trashed by hand since keeps showing as in the inbox in the history.
- Every Clean up action and every word plan asks for confirmation with the real number of emails per account: the
  server counts them in Gmail with the same searches the action then runs (`POST /api/sender/preview`; up to
  `PREVIEW_MAX`, default 20,000, per account and step, shown as "or more" beyond that). Where the action takes mail
  out of view, only mail really from that sender is counted, since only that is changed. The sender comes from the
  local history; for mail not collected yet it is read from Gmail for at most `PREVIEW_LOOKUPS` messages per dialog
  (default 100), and the rest is shown as "up to" a number. The count is taken when the
  dialog opens, so mail that arrives in between is not in it. Bulk actions run inside the request; one action
  handles at most `MAX_BULK` messages per account. So that a request always answers well inside the proxy's timeout
  (Cloudflare gives up after 100 seconds), one action reads at most `BULK_LOOKUPS` senders (default 300, about 30
  seconds) from Gmail for mail not collected yet, shared fairly between the accounts; the messages it could not
  check are left alone and the result says **Partly done: run it again** to continue. The senders it read are
  remembered (`unified.sender_seen`, 30 days; a message with no sender, or deleted since, is tried again after an
  hour), so the next run picks up where this one stopped instead of reading the same messages again. A read Gmail
  could not answer just then (rate limit, server error, a login error, timeout) is not remembered: that message counts as not done
  yet, not as someone else's mail, and the next run reads it again. One Gmail refuses for good (a bad id, a 403 that
  is not a rate limit) is remembered as sender unknown for an hour, like a deleted one, and left alone. A run that changed nothing and learned no sender
  does not say "run it again": it says what stopped it (Gmail not answering the look-ups, or no look-ups left). If the page still gets no answer in time, it says the action may
  still be running and to check **Undo a bulk action** before trying again.
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
  which Inbox Zero shares). A busy Gmail (429, 500, 503) is asked again with back-off. A gateway error (502, 504) is
  asked again only for reads and label changes, which are harmless twice; a new filter or label is not, since Gmail
  may have made it already. A dropped connection (reset, timeout) is asked again only for reads: a change that lost
  its connection fails, and the action says so. If a request repeated after a 500 or 503 finds its filter already
  there, and that filter was not there before the first try, it is recorded as Zero Sum's, so Protect, Keep and Reset
  can still remove it; a matching filter made by hand is left alone. The same goes for a new filter whose connection
  dropped: the filters are listed again, right away and after 2 and 5 seconds (a timed-out request may still land),
  and one that matches exactly and was not there before is recorded, marked "(claimed)". One edge remains, since
  Gmail filters carry no creation time: an identical filter you make by hand in Gmail in those few seconds, while
  Zero Sum's own request was lost, would be taken for Zero Sum's. One sender's filters are changed by one action at a time
  (its old filters removed and the new ones made as one step), so two actions at once on the same sender never claim
  each other's filter or leave both sets behind. That covers every path that replaces filters: sender actions,
  word plans and AI-sort plans, Protect (one or a pasted list), Unprotect and Quarantine release. An action waits
  at most 30 seconds for another one on the same sender; after that it leaves that sender (in that account) alone
  and says another action is still running, instead of holding a request open with no limit. An expired token
  (401) is refreshed once and the request asked again, keeping track of whether an earlier try may have done the
  work.
- It uses its own database role (`grants.sql`): read access to a few columns of `Account`, `EmailAccount` and
  `ExecutedRule`; write access only to `Rule`, `Action`, `Newsletter` and its own `unified` schema.
- The Inbox Zero tables, columns and enums it depends on are declared once, in `izdb.py`. `grants.sql` grants exactly
  that list, the upstream check below compares exactly that list, and at start-up the app checks the live database
  against it and logs each missing table, column or enum by name.
- Health: `GET /healthz` says the process is up. `GET /healthz?deep=1` also connects to the database and checks the
  schema contract, and answers 503 on a database failure or a schema mismatch: point an updater's roll-back check at
  that one.
- A collector stores message metadata (sender, subject, preview, date, read/inbox state, unsubscribe header) every 15
  minutes, back-filling the whole history gradually (to each account's first email, `BACKFILL_STEP` per account per run).
  The Cleanup, Analytics and AI features read from this local copy.
- AI plans: one request per sender with its subject lines (counted and de-duplicated, up to 80 distinct). AI sort: 40
  emails (subject + preview) per request; verdicts are reduced to "keep these numbers". Word-group plans become ordered
  Gmail filters (`query` / `negatedQuery`), so the first matching group wins for future mail too; AI-sorted senders are
  re-checked every collector run. Plans and sort state live in Zero Sum's own schema (`ai_plan`, `ai_sort`).
- Every AI call (AI suggest included) counts against a daily cap (`AI_MAX_CALLS_PER_DAY`); past it, background work
  waits for the next day. A busy AI provider (429 / 5xx / timeout) is retried up to three times per call; an AI sort
  stopped by one, or by a busy Gmail, is picked up again later (`AI_SORT_RETRIES` times at most).
- Email text is untrusted: it is sent to the model as a separate, fenced data message, never as part of the
  instructions, and the answer is only accepted as one of the fixed actions, cleaned keep words, or exactly one
  verdict per numbered email.
- Rule sync records which copy belongs to which master rule (with a content hash), so it only ever updates or deletes
  its own copies. A rule that already existed in a target account under the same name or system type is
  adopted (overwritten with the master's version); what it was before, with its actions, is saved first in
  `unified.rule_backup`. Automatic sync is off until you turn it on in the Rules tab.
- Removing a copy (unticked, or its master rule deleted) never deletes a rule the account had before: an adopted rule is
  put back as it was from its backup, or, with no backup, left as it is and no longer managed. Only copies Zero Sum
  created are deleted, and only after you confirm them: "Sync now" lists every delete first. The automatic sync
  deletes at most `RULES_AUTO_MAX_DELETES` (default 3) copies in total until someone runs "Sync now" (the count is
  kept across runs), and none at all in a run that would go over; the rest wait for "Sync now" and are logged.
  When the master looks like another account than at the last sync (`MASTER_EMAIL` changed, the same address linked
  again in Inbox Zero under a new account id, or every master rule the copies came from gone at once), nothing is
  removed until "Sync now" confirms the change. A copy is saved in "Saved rules" before it is deleted, and before an
  update when someone had changed it in that account. Every saved rule can be put back from "Saved rules" on the
  Rules tab, including an account's own earlier rules after `MASTER_EMAIL` moved to that account. A saved rule you
  are told to restore by hand (its old name now clashes with another rule) is never pruned.
- A rule that forwards, sends or replies by email, or calls a webhook, would do that from every account it is copied
  to, with the master's destinations. Any action type Zero Sum does not know to stay inside the account is treated
  the same way. The Rules tab shows each destination, and such a rule is copied to an account only after you tick
  "allow sending" for that account (and confirm); until then the sync reports it and leaves it.
- The OK is for the destinations shown when you gave it. If the master rule later forwards or posts somewhere else,
  the copy is not updated until you allow sending again (the cell says "sends somewhere new"); the copy already there
  keeps sending only where you OK'd.
- Taking "allow sending" back stops it: at the next sync the copy loses its forward / send / reply / webhook actions,
  and the rest of it stays. The copy is saved in "Saved rules" first. Copies made before this OK existed are treated
  as not allowed, so they stop sending too until you allow it; their cell reads "not sending".

## Requirements

- Self-hosted Inbox Zero with Google accounts, run with its `docker-compose.yml` (its `db` service and
  `inbox-zero-network`). Tested with Inbox Zero **v2.30.0**; Zero Sum writes some Inbox Zero tables directly, so check
  [Upgrading Inbox Zero](#upgrading-inbox-zero) before upgrading.
- By default Zero Sum works on **every** Google mailbox linked in the Inbox Zero database. If your Inbox Zero has more
  than one user, set `ACCOUNT_OWNERS` so it only touches yours.
- An authenticating reverse proxy in front of it. Zero Sum has **no login page**. Behind **Cloudflare Access** set
  `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD`: it then verifies the signed Access token on every request and only lets
  in `ALLOWED_EMAILS`. Without those two it **refuses to start**. `ALLOW_HEADER_TRUST=1` overrides that for local
  development (or a proxy that cannot sign, on a network only that proxy can reach): the app then believes the plain
  `AUTH_EMAIL_HEADER`, which anything that can reach the port can send. Never expose its port directly.

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
run, `ADMIN_DATABASE_URL` can be removed from `.env.zero-sum`. If the app is started before `init-db` (or against the
wrong database), it stops with one line saying to run `init-db`; `init-db` against a database without Inbox Zero's
tables says to point it at Inbox Zero's own database, after Inbox Zero has started once. A refused login is told apart:
`init-db` with a wrong `ADMIN_DATABASE_URL` user or password says so (running it again would not help), and at start a
refused `DATABASE_URL` login says that either `init-db` has not run yet or the password in `DATABASE_URL` is not the
one `init-db` set. A password containing `@ / : + = %` or similar must be percent-encoded in `DATABASE_URL` (`@` as
`%40`, `/` as `%2F`); `init-db` decodes it the same way Postgres does, so the role gets the real password.

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
| `ACCOUNT_OWNERS` | unset | comma-separated Inbox Zero user ids and/or login e-mails: only their linked mailboxes are used. Unset = every Google mailbox in the Inbox Zero database (fine for a single-user instance). Set it when other people also sign in to your Inbox Zero. |
| `ALLOW_HEADER_TRUST` | unset | `1` = start without Access token verification and believe `AUTH_EMAIL_HEADER`. Local development only. |
| `AUTH_EMAIL_HEADER` | `Cf-Access-Authenticated-User-Email` | only with `ALLOW_HEADER_TRUST=1`: header your proxy sets (oauth2-proxy: `X-Forwarded-Email`) |
| `LISTEN_HOST` | `127.0.0.1` (image: `0.0.0.0`) | address the server binds to; in a container the published port decides who can reach it |
| `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` | unset | behind Cloudflare Access: your team domain (`<team>.cloudflareaccess.com`) and the Access application's AUD tag. With both set, Zero Sum verifies the signed `Cf-Access-Jwt-Assertion` token and takes the user from it; the plain e-mail header is ignored. Required unless `ALLOW_HEADER_TRUST=1`. |
| `MASTER_EMAIL` | empty (rule sync off) | account whose rules are copied to the others |
| `INBOX_ZERO_URL` | empty | your Inbox Zero address, for links |
| `ADMIN_DATABASE_URL` | | only for `init-db` |
| `PORT` | `8080` | inside the container |
| `COLLECT_EVERY_MIN`, `RULES_EVERY_MIN` | `15`, `10` | background jobs |
| `RULES_AUTO_MAX_DELETES` | `3` | most rule copies the automatic sync deletes between two "Sync now" (counted across runs); the rest wait for "Sync now" |
| `BACKFILL_DAYS`, `BACKFILL_STEP` | `0` (all history), `2000` | how far back the collector reads (days; 0 = to the account's first email), and how much per run per account |
| `MAX_BULK` | `200000` | most emails one bulk action touches per account |
| `PREVIEW_MAX`, `PREVIEW_LOOKUPS` | `20000`, `100` | the confirm dialog's count: most emails it counts per account and step (shown as "or more" beyond), and most senders it reads from Gmail for mail not collected yet (the rest shown as "up to") |
| `BULK_LOOKUPS` | `300` | most message senders one bulk action reads from Gmail per request (mail not collected yet), split evenly over the accounts; the rest is left for "run it again" |
| `UNDO_REFRESH` | `500` | most restored messages one undo reads back from Gmail (in the background, about 50 seconds of quota at the default `GMAIL_UNITS_PER_SEC`, used only while nothing else needs it); the rest stay marked as in the inbox |
| `SENDER_WAIT` | `30` | seconds an action waits for another action on the same sender (two tabs, Block then Auto-archive) before it skips that sender and says it is busy |
| `OPENROUTER_API_KEY`, `AI_MODEL`, `AI_BASE_URL` | empty, `anthropic/claude-haiku-5.5`, OpenRouter | all AI features: AI Cleanup, AI sort, AI suggest, background analyst (off without a key; the background analyst is also off until turned on in AI Cleanup) |
| `AI_MAX_CALLS_PER_DAY`, `AI_PLANS_PER_RUN`, `AI_MIN_EMAILS` | `400`, `4`, `5` | daily AI call cap; senders the background analyst plans per run; minimum emails before a sender is planned |
| `AI_SORT_RETRIES` | `5` | how often an AI sort pass stopped by a busy AI provider or Gmail is picked up again (15, 30, 45... minutes apart) before it is given up |
| `QUARANTINE_LABEL`, `QUARANTINE_SHOW` | `Quarantine`, `600` | Gmail label for held mail; messages per account listed on the Quarantine tab |
| `GMAIL_UNITS_PER_SEC` | `50` | Gmail quota units per second per account |
| `INBOX_ZERO_REPO` | `elie222/inbox-zero` | tools only: the GitHub repository `tools/check_upstream.py` compares the pinned Inbox Zero version against |

## Security notes

- Zero Sum can read every linked mailbox, as Inbox Zero can. Run it only behind authentication, for yourself.
- Email bodies are shown in a sandboxed iframe with no scripts; remote images are blocked until you click "Show images", which applies to that message only.
- POST requests need a custom header, so other sites cannot submit actions with your session.
- A client has 15 seconds to send its whole request (and may pause at most 30 seconds between bytes); a large POST
  body gets more time by its size (at least 8 KB a second; 79 seconds at most, for the largest body). A slow one is
  dropped, or answered 408 once its headers have arrived; what it is still sending is read and dropped for a second
  after the 408, so the answer is not lost to a reset. The same goes for a POST refused before its body is read
  (403 not signed in, 400 bad request, 413 too large). At most 32 requests are worked on at once, counted only once a request has arrived in full, and at most
  256 connections are open at once (more are answered 503 straight away); up to 64 new connections can wait to be
  accepted during a burst. `/healthz` needs no request slot.
- Failed requests are logged by path only, without query strings (search words, account and message ids). An
  unexpected error reaches the page only as a short reference; its details are in the container log under it.
- One-click unsubscribe POSTs to a URL taken from the sender's own `List-Unsubscribe` header, so it is held to the
  public internet: https on port 443 only, every address the host resolves to must be public (no loopback, private,
  link-local or cloud-metadata addresses, nor IPv6 spellings of them: NAT64, 6to4, Teredo, IPv4-compatible,
  IPv4-translated or site-local), the connection goes to the address that was checked, redirects are not
  followed, and it gives up after 10 seconds. Anything else is left as a link for you to open.
- Sender addresses in requests must be plain addresses and are quoted in every Gmail search. On mail already in the
  account, a sender action, a plan and AI sort (the background pass and new mail) change only messages whose real
  sender is that address: Gmail's `from:` search also matches display names and longer addresses, so each message's
  sender is looked up first, and the rest is left alone. They never change mail from a protected sender or from a
  linked account's own address, in any spelling that reaches it (`+tags`, Gmail's dots, `googlemail.com`). Gmail
  "send as" aliases on other domains are not known to Zero Sum: protect them.
- Future mail is different: Block, Quarantine, Auto-archive, the unsubscribe-and-… actions and plans add Gmail filters
  on `from:`, and Gmail applies those itself with the same loose match, so a filter can also catch mail that only
  shows the address in its sender name. Zero Sum cannot check those messages; the confirmations say so.
- Email text sent to the AI is treated as untrusted: prompts say so, and answers are reduced to a fixed set of actions,
  cleaned keywords and message numbers before anything is shown. Nothing the AI returns runs without your confirmation.
- The container runs read-only as an unprivileged user with all capabilities dropped.

## Deploy definitions

- `compose.zero-sum.yml` is the Docker Compose add-on described under Install. It is kept in step with the app by
  hand and is not exercised by the tests or by the maintainer's own server, so treat it as a starting point and check
  the start-up log after an upgrade.
- `deploy/zero-sum.container.in` is a Podman quadlet template (read-only, no capabilities, port on 127.0.0.1 only,
  health check on `/healthz?deep=1`); this is how the maintainer's server runs it.
- `deploy/env.contract` lists every environment variable the app reads and whether it is required. A test keeps it
  equal to what the code reads, so a setup script can be checked against one file instead of re-reading the source.

## Inbox Zero versions

[`inbox-zero.version`](inbox-zero.version) names the Inbox Zero release this version of Zero Sum is tested with.
Zero Sum reads and writes some Inbox Zero tables directly, so run that release (or check first):

```sh
python3 tools/check_upstream.py              # supported version vs Inbox Zero's latest release
python3 tools/check_upstream.py v2.25.8 v2.30.0
```

It compares only what Zero Sum depends on (the columns and enum values declared in `izdb.py`, not whole Prisma
models, plus `utils/encryption.ts`, the environment variable names and the docker-compose services/endpoints). Exit 0 = nothing relevant changed: bump
`inbox-zero.version`. Exit 1 = adapt Zero Sum first; the diff shows what changed.

Already handled for v2.30: versioned token encryption (`v1:` prefix), `Action.emailAccountId` (written on rule copies
when the column exists, checked on every sync, declared as a versioned column in `izdb.py`) and messaging-channel
actions (Slack/Teams/Telegram; not copied to other accounts, since they point at the master's own channel). The pin
stays at the tested release until `check_upstream.py` has been run against the new one.

## Development

One Python process, standard library HTTP server, no framework. `app.py` (routes, sign-in gate, background jobs),
`core.py` (database, tokens, Gmail calls), `izdb.py` (the Inbox Zero schema contract), `guard.py` (input validation,
protected senders), `collector.py` (mail metadata), `gmailops.py` (filters, bulk label changes, undo journal),
`actions.py` (sender actions, quarantine, plans, previews), `aisort.py` and `ai.py` (AI), `inbox.py`, `analytics.py`,
`rulesync.py`, `access.py`. `cleanup.py` is only a facade over the five modules it was split into.

```sh
pip install cryptography==43.0.3 "psycopg[binary]==3.2.3"
python3 -m unittest discover -s tests
```

## License

[AGPL-3.0](LICENSE), the same license as Inbox Zero. Inbox Zero's own license adds terms restricting commercial
monetization of Inbox Zero; Zero Sum is meant to be used alongside it on your own server.

Copyright (C) 2026 Roy McKee
