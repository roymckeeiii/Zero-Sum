"""The Inbox Zero database contract: every upstream table, column and enum Zero Sum depends on, declared once.

Zero Sum reads and writes Inbox Zero's own Postgres tables, which belong to another project and change with its
releases. This list is what the rest of the code may assume. It is used by
  - check(): compares it with the live database (information_schema / pg_enum) at start-up and for /healthz?deep=1;
  - grants.sql, which a test holds to exactly this list (least privilege);
  - tools/check_upstream.py, which diffs only these columns between two Inbox Zero releases.
When SQL elsewhere starts using another upstream column, add it here first (a test scans the sources for upstream
table names). Standard library only: tools/check_upstream.py imports this without the app's dependencies.
"""

# Rule and Action columns copied from the master account to the others (rulesync.py)
RULE_FIELDS = ["name", "instructions", "automate", "runOnThreads", "body", "from", "subject", "to", "enabled",
               "conditionalOperator", "systemType", "promptText"]
ACTION_FIELDS = ["type", "label", "subject", "content", "to", "cc", "bcc", "url", "delayInMinutes", "folderName",
                 "folderId"]
ENUM = {"conditionalOperator": "LogicalOperator", "systemType": "SystemType", "type": "ActionType"}  # column -> enum

# table -> columns used. READ: column-level SELECT only. WRITE: SELECT, INSERT, UPDATE, DELETE on the table.
READ = {
    "Account": ["id", "provider", "access_token", "refresh_token", "expires_at"],   # the Google logins (core.py)
    "EmailAccount": ["id", "email", "name", "accountId"],                           # the linked mailboxes (core.py)
    "ExecutedRule": ["messageId", "ruleId", "status", "emailAccountId", "createdAt"],  # inbox.py, analytics.py
}
WRITE = {
    "Rule": ["id", "createdAt", "updatedAt", "emailAccountId", *RULE_FIELDS],       # rulesync.py
    "Action": ["id", "createdAt", "updatedAt", "ruleId", "labelId", *ACTION_FIELDS],  # rulesync.py
    "Newsletter": ["id", "updatedAt", "email", "status", "emailAccountId"],         # actions.py, ai.py, analytics.py
}
TABLES = {**READ, **WRITE}
# Read only when ACCOUNT_OWNERS is set (core.accounts_query): whose mailbox is whose. grants.sql grants these when
# they exist, and they are checked only when the setting is in use.
OWNER_SCOPE = {"EmailAccount": ["userId"], "User": ["id", "email"]}
# enum -> the values Zero Sum itself writes or compares with (rule sync only copies values, so it needs none by name)
ENUMS = {
    "ActionType": ["LABEL"],
    "LogicalOperator": [],
    "SystemType": [],
    "NewsletterStatus": ["UNSUBSCRIBED", "AUTO_ARCHIVED", "APPROVED"],
    "ExecutedRuleStatus": ["APPLIED"],
}


def problems(c, owner_scope=False):
    """What the contract needs that this connection cannot see: a list of plain sentences, empty when all is there.
    information_schema only shows columns the role may use, so a missing grant is reported like a missing column.
    owner_scope: also require the columns ACCOUNT_OWNERS needs."""
    tables = {t: list(cols) for t, cols in TABLES.items()}
    if owner_scope:
        for t, cols in OWNER_SCOPE.items():
            tables.setdefault(t, []).extend(cols)
    have = {}
    for table, column in c.execute("SELECT table_name, column_name FROM information_schema.columns "
                                   "WHERE table_schema = 'public' AND table_name = ANY(%s)", (sorted(tables),)).fetchall():
        have.setdefault(table, set()).add(column)
    enums = {}
    for name, label in c.execute("SELECT t.typname, e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
                                 "JOIN pg_namespace n ON n.oid = t.typnamespace WHERE n.nspname = 'public' "
                                 "AND t.typname = ANY(%s)", (sorted(ENUMS),)).fetchall():
        enums.setdefault(name, set()).add(label)
    out = []
    for table, columns in tables.items():
        if table not in have:
            out.append(f'table "{table}" is missing or not readable')
            continue
        out += [f'column "{table}"."{col}" is missing or not readable' for col in columns if col not in have[table]]
    for name, values in ENUMS.items():
        if name not in enums:
            out.append(f'enum "{name}" is missing')
            continue
        out += [f'enum "{name}" has no value {v}' for v in values if v not in enums[name]]
    return out


def check():
    """problems() on a fresh connection. Raises when the database cannot be reached."""
    from core import OWNERS, db
    with db() as c:
        return problems(c, owner_scope=bool(OWNERS))
