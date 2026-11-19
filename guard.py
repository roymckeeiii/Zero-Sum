"""What may be acted on, and how what comes in with a request is cleaned: sender addresses (they end up in Gmail
searches and filter criteria), keep words, and the two refusals every destructive path ends in: protected senders
(an address or a whole domain, unified.protected) and the linked accounts' own addresses. Depends only on core."""
import re

from core import ACC, db

# A sender address as this app accepts it in an action: plain mailbox characters only. It ends up in Gmail searches
# and filter criteria, so nothing that reads as search syntax (spaces, quotes, brackets, braces, |, :, commas) gets in.
_LOCAL, _DOMAIN = r"[a-z0-9._%+=&'~!#$*/?^-]{1,64}", r"[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63})+"
SENDER = re.compile(rf"{_LOCAL}@{_DOMAIN}")
SENDER_KEY = re.compile(rf"(?:{_LOCAL})?@{_DOMAIN}")  # an address, or a whole domain ("@example.com")


def sender_addr(value):
    """The address of a sender action, lower-cased; ValueError unless it is one plain address."""
    addr = str(value or "").strip().lower()
    if len(addr) > 254 or not SENDER.fullmatch(addr):
        raise ValueError("bad sender address")
    return addr


def frm(addr):
    """Gmail search term for mail from this address: validated again here, and quoted, so it is one literal term."""
    return f'from:"{sender_addr(addr)}"'


def protected_set():
    with db() as c:
        return {r[0] for r in c.execute("SELECT key FROM unified.protected").fetchall()}


def is_protected(addr, prot=None):
    """A sender is protected by its exact address or its domain ("@example.com")."""
    prot = protected_set() if prot is None else prot
    if addr in prot:
        return True
    labels = addr.rsplit("@", 1)[-1].split(".")  # "@chase.com" also covers alerts.chase.com, never a bare TLD
    return any("@" + ".".join(labels[i:]) in prot for i in range(max(1, len(labels) - 1)))


GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def mailbox(addr):
    """The mailbox an address delivers to, for comparing addresses: lower-cased, "+tag" dropped (me+news@ is me@),
    and for Gmail also the dots in the name and googlemail.com (m.e@googlemail.com is me@gmail.com)."""
    local, _, domain = str(addr or "").strip().lower().rpartition("@")
    if not local:
        return str(addr or "").strip().lower()
    local = local.split("+", 1)[0] or local
    if domain in GMAIL_DOMAINS:
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def own_set():
    """The linked accounts' own mailboxes (see mailbox()). Gmail "send as" aliases are not included: this app does
    not read /settings/sendAs, so mail from an alias on another domain is not recognised as the user's own."""
    return {mailbox(a["email"]) for a in ACC.list()}


def own_address(addr, own=None):
    """True when addr is one of the linked accounts, in any spelling that reaches the same mailbox (+tag, Gmail dots,
    googlemail.com): `from:<me>` matches everything that account ever sent."""
    return mailbox(addr) in (own_set() if own is None else own)


def guard_sender(addr, prot=None):
    """The one refusal every destructive path ends in: a protected sender's mail, and mail from one of the linked
    accounts' own addresses, is never trashed, archived, quarantined or reported, whoever asks."""
    if is_protected(addr, prot):
        raise PermissionError(f"{addr} is protected: unprotect it first")
    if own_address(addr):
        raise PermissionError(f"{addr} is one of your own accounts: its mail is never cleaned up")


def removes_mail(add, remove):
    """Does this label change take mail out of view (Trash, Spam, a holding label, out of the inbox)? Starring and
    putting mail back in the inbox do not."""
    return bool(set(add) - {"STARRED", "INBOX"}) or "INBOX" in remove


PROTECT_KEY = re.compile(r"[^@\s,;]*@[^@\s,;]+\.[^@\s,;]+")


def parse_protect_keys(text):
    """A pasted list of senders -> (keys, rejected). One or more per line, separated by spaces or semicolons; on a
    CSV line only the first column counts (so a keep-list export with notes and dates pastes as it is). An address
    stays an address, "@example.com" a domain, and a bare "example.com" becomes "@example.com"."""
    keys, rejected = [], []
    for line in str(text or "").splitlines():
        first = line.split(",", 1)[0]
        for tok in re.split(r"[\s;]+", first.strip()):
            tok = tok.strip().strip('"\'<>').lower()
            if not tok:
                continue
            key = tok if "@" in tok else "@" + tok
            if PROTECT_KEY.fullmatch(key) and SENDER_KEY.fullmatch(key) and len(key) <= 254:
                if key not in keys:
                    keys.append(key)
            elif tok not in rejected:
                rejected.append(tok[:60])
    return keys, rejected


def keep_query(words):
    """Gmail search for 'subject contains any of these words/phrases', or None. Input is cleaned, not trusted."""
    out = []
    words = [w for w in (words or []) if str(w).strip()]
    if len(words) > 10:  # never drop one silently: a missing keep word means that mail gets the destructive action
        raise ValueError("at most 10 keep words per group")
    for w in words:
        w = re.sub(r"[^\w\s'&$%!-]", " ", str(w), flags=re.U).strip().lstrip("-").strip()[:40]  # "-word" would negate
        if w:
            out.append(f'"{w}"' if " " in w else w)
    return f"subject:({' OR '.join(out)})" if out else None


def subject_has(words, subject):
    """Does the subject contain one of the words or phrases as whole words? This is what the applied Gmail search
    `subject:(word)` matches, so the previews use it too. A plain substring test showed "Reorder now" as kept by
    "order" (green in the preview), and Gmail then gave it the destructive action."""
    sub = str(subject or "").lower()
    for w in words or []:
        w = re.sub(r"\s+", " ", str(w).strip().lower())
        if w and re.search(r"(?<![^\W_])" + re.escape(w).replace(r"\ ", r"\s+") + r"(?![^\W_])", sub, flags=re.U):
            return True
    return False


def sender_matches(frm, key):
    """Does a filter's `from` value fall under `key`: an address, or a domain ("@example.com", subdomains included)?"""
    frm, key = (frm or "").strip().lower(), (key or "").strip().lower()
    if not frm or not key:
        return False
    if key.startswith("@"):
        dom = frm.rsplit("@", 1)[-1]
        return dom == key[1:] or dom.endswith("." + key[1:])
    return frm == key
