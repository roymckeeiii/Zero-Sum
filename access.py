"""Cloudflare Access token check (stdlib + cryptography; the image has no PyJWT).

The reverse proxy sets Cf-Access-Authenticated-User-Email, but a header is only as trustworthy as the path the
request took: anything that can reach this port directly (another container on the pod network, a process on the
host) can set it too. Access also sends a signed token, Cf-Access-Jwt-Assertion. With CF_ACCESS_TEAM_DOMAIN and
CF_ACCESS_AUD set, app.py takes the user's address from that token and ignores the plain header.
"""
import base64, json, os, threading, time, urllib.request

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

TEAM = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip().lower().removeprefix("https://").rstrip("/")
AUD = os.environ.get("CF_ACCESS_AUD", "").strip()
ENABLED = bool(TEAM and AUD)
CERT_TTL, LEEWAY = 3600, 60
REFETCH_MIN, RETRY_AFTER = 60, 60   # seconds between refetches for an unknown kid; before retrying a failed fetch
_keys, _fetched, _retry_at, _lock = {}, 0.0, 0.0, threading.Lock()


def _b64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _fetch_keys():
    with urllib.request.urlopen(f"https://{TEAM}/cdn-cgi/access/certs", timeout=10) as r:
        jwks = json.load(r)
    out = {}
    for k in jwks.get("keys", []):
        if k.get("kty") == "RSA" and k.get("kid"):
            out[k["kid"]] = rsa.RSAPublicNumbers(int.from_bytes(_b64(k["e"]), "big"),
                                                 int.from_bytes(_b64(k["n"]), "big")).public_key()
    return out


def keys(force=False):
    """Access's signing keys by kid, refetched hourly. A failed fetch keeps the keys already held (a Cloudflare blip
    must not lock the owner out); with none held yet, nobody gets in.

    Every request is checked here, signed in or not, under one lock. So a token naming an unknown kid (`force`, for
    a rotated key) refetches at most once a minute, and a failed fetch is retried after a minute, not on every
    request: otherwise anyone who can reach the port could make each request wait on a fetch to Cloudflare, and
    hold up everyone else's sign-in behind it."""
    global _keys, _fetched, _retry_at
    with _lock:
        now = time.time()
        due = not _keys or now - _fetched > CERT_TTL or (force and now - _fetched > REFETCH_MIN)
        if due and now >= _retry_at:
            try:
                _keys, _fetched = _fetch_keys(), now
            except Exception as e:  # noqa: BLE001
                print(f"[access] could not fetch certs: {e}", flush=True)
                _retry_at = now + RETRY_AFTER
        return _keys


def email_from(token, team=None, aud=None, now=None, key_lookup=None):
    """The verified email of an Access token, lower-cased, or None. Checks: RS256 signature by one of the team's
    keys, issuer = the team, audience = this application, not expired, not used before its time."""
    team, aud, now = team or TEAM, aud or AUD, now or time.time()
    try:
        h64, p64, s64 = str(token or "").split(".")
        header, payload, sig = json.loads(_b64(h64)), json.loads(_b64(p64)), _b64(s64)
        if header.get("alg") != "RS256":     # never "none", never a symmetric algorithm
            return None
        lookup = key_lookup or (lambda kid: keys().get(kid) or keys(force=True).get(kid))   # a rotated key: refetch once
        key = lookup(header.get("kid"))
        if key is None:
            return None
        key.verify(sig, f"{h64}.{p64}".encode(), padding.PKCS1v15(), hashes.SHA256())
        auds = payload.get("aud")
        auds = auds if isinstance(auds, list) else [auds]
        if aud not in auds or payload.get("iss") != f"https://{team}":
            return None
        if not isinstance(payload.get("exp"), (int, float)) or payload["exp"] < now - LEEWAY:
            return None
        if isinstance(payload.get("nbf"), (int, float)) and payload["nbf"] > now + LEEWAY:
            return None
        email = payload.get("email")
        return email.strip().lower() if isinstance(email, str) and email.strip() else None
    except Exception:  # noqa: BLE001 - malformed token, bad signature: not signed in
        return None
