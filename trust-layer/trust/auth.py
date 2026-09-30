"""Authenticatie, sessies, rollen en rate limiting (alleen standaardbibliotheek).

- Wachtwoorden: PBKDF2-SHA256 met salt, vergelijking in constante tijd.
- Sessies: willekeurig token in een HttpOnly-cookie; de server bewaart wie erbij hoort.
- Rol en company komen altijd uit de sessie, nooit uit het request.
- Accounts staan in data/state/users.json (niet in git). Bij de eerste start worden
  demo-accounts aangemaakt met het wachtwoord uit TRUST_LAYER_DEMO_PASSWORD; staat die
  niet gezet, dan wordt een willekeurig wachtwoord gegenereerd en één keer in de console getoond.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

ITERATIONS = 200_000
SESSION_TTL = 8 * 3600
COOKIE = "tl_session"

# wat elke rol mag; controle gebeurt server-side per endpoint
PERMISSIONS = {
    "employee": {"search", "vote", "read_docs", "ingest"},
    "hr": {"search", "vote", "read_docs", "ingest", "health"},
    "admin": {"search", "vote", "read_docs", "ingest", "health", "eval"},
}
VOTE_WEIGHT_ROLE = {"employee": "employee", "hr": "hr", "admin": "hr"}

DEMO_USERS = [
    {"id": "u-emp-brouwer", "email": "medewerker@brouwer.demo", "name": "Kevin Claes", "role": "employee", "companies": ["brouwer"]},
    {"id": "u-hr-brouwer", "email": "hr@brouwer.demo", "name": "Sofie Maes", "role": "hr", "companies": ["brouwer"]},
    {"id": "u-emp-verhoeven", "email": "medewerker@verhoeven.demo", "name": "Jonas Peeters", "role": "employee", "companies": ["verhoeven"]},
    {"id": "u-admin", "email": "admin@trustlayer.demo", "name": "Admin", "role": "admin", "companies": ["brouwer", "verhoeven"]},
]


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return {"salt": salt.hex(), "hash": digest.hex(), "iterations": ITERATIONS}


def verify_password(password, stored):
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(stored["salt"]), stored["iterations"])
    return hmac.compare_digest(digest.hex(), stored["hash"])


class Auth:
    def __init__(self, state_dir):
        self.path = Path(state_dir) / "users.json"
        self._lock = threading.Lock()
        self.sessions = {}
        self.users = self._load_or_create()
        # dummy-hash om timing gelijk te houden bij onbekende e-mailadressen
        self._dummy = hash_password(secrets.token_urlsafe(16))

    def _load_or_create(self):
        if self.path.exists():
            return {u["id"]: u for u in json.loads(self.path.read_text(encoding="utf-8"))}
        password = os.environ.get("TRUST_LAYER_DEMO_PASSWORD")
        generated = not password
        if generated:
            password = secrets.token_urlsafe(12)
        users = [{**u, "password": hash_password(password)} for u in DEMO_USERS]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(users, indent=1), encoding="utf-8")
        if generated:
            print("  Demo-accounts aangemaakt (" + ", ".join(u["email"] for u in DEMO_USERS) + ")", flush=True)
            print(f"  Eenmalig getoond demo-wachtwoord: {password}", flush=True)
            print("  Zet TRUST_LAYER_DEMO_PASSWORD om zelf een wachtwoord te kiezen (verwijder dan data/state/users.json).", flush=True)
        return {u["id"]: u for u in users}

    def login(self, email, password):
        user = next((u for u in self.users.values() if u["email"] == email.strip().lower()), None)
        if user is None:
            verify_password(password, self._dummy)
            return None
        if not verify_password(password, user["password"]):
            return None
        token = secrets.token_urlsafe(32)
        with self._lock:
            self.sessions[token] = {"user_id": user["id"], "expires": time.time() + SESSION_TTL}
        return token, user

    def logout(self, token):
        with self._lock:
            self.sessions.pop(token, None)

    def user_for(self, token):
        if not token:
            return None
        with self._lock:
            s = self.sessions.get(token)
            if not s or s["expires"] < time.time():
                self.sessions.pop(token, None)
                return None
        return self.users.get(s["user_id"])


PRIVILEGED = {"hr", "admin"}


def sees_restricted(user):
    return user["role"] in PRIVILEGED


def can(user, permission):
    return permission in PERMISSIONS.get(user["role"], set())


def public_user(user):
    """Alleen wat de frontend nodig heeft: nooit de wachtwoordhash."""
    return {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"],
            "companies": list(user["companies"])}


class RateLimiter:
    """Eenvoudig sliding window per sleutel (bv. IP + route of gebruiker + route)."""

    def __init__(self):
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key, limit, window=60):
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < window]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True
