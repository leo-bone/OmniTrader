"""Authentication: password hashing, user storage, and signed sessions.

Zero third-party dependencies by design (no passlib/bcrypt/jwt packages) —
the whole point of OmniTrader is that it runs anywhere Python runs.

Password storage
    PBKDF2-HMAC-SHA256, 200k iterations, 16-byte per-user random salt.
    Verified with `hmac.compare_digest` (constant time, no early-exit leak).

Sessions
    `base64url(payload).base64url(HMAC-SHA256(payload, server_secret))`
    where payload carries {user, issued, expires, nonce}. Stateless, expires,
    and forgeable only with the server secret — which is generated once and
    stored with 0600 permissions.

Env vars
    OMNITRADER_HOME      where users.json / the server secret live
    OMNITRADER_ADMIN_PASSWORD   bootstrap password for the first `admin`
    OMNITRADER_ALLOW_SIGNUP     set to 1 to open self-registration
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PBKDF2_ITERATIONS = 200_000
SALT_BYTES = 16
KEY_BYTES = 32
SESSION_TTL_SECONDS = 12 * 3600
MIN_PASSWORD_LENGTH = 8
MAX_FAILED_ATTEMPTS = 8
LOCKOUT_SECONDS = 300


def home_dir() -> Path:
    base = os.environ.get("OMNITRADER_HOME")
    if base:
        p = Path(base).expanduser()
    else:
        p = Path.home() / ".omni_trader"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ----------------------------------------------------------------------
@dataclass
class User:
    username: str
    salt: bytes
    hash: bytes
    created_at: float
    role: str = "user"

    def verify(self, password: str) -> bool:
        cand = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                   self.salt, PBKDF2_ITERATIONS, KEY_BYTES)
        return hmac.compare_digest(cand, self.hash)

    def to_json(self) -> Dict[str, Any]:
        return {
            "salt": _b64e(self.salt),
            "hash": _b64e(self.hash),
            "created_at": self.created_at,
            "role": self.role,
        }


class UserStore:
    """A JSON-backed user table. Fine for a single-node self-hosted app.

    It is deliberately NOT a distributed identity provider — if you need SSO,
    put this service behind a reverse proxy that does it.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else home_dir() / "users.json"
        self._lock = threading.RLock()
        self._users: Dict[str, User] = {}
        # set by the first-run bootstrap below; always present so callers can
        # read them without getattr() gymnastics
        self.initial_password: Optional[str] = None
        self.initial_password_generated: bool = False
        self._load()
        self._bootstrap_admin()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        for name, rec in raw.get("users", {}).items():
            try:
                self._users[name] = User(
                    username=name, salt=_b64d(rec["salt"]), hash=_b64d(rec["hash"]),
                    created_at=float(rec.get("created_at", time.time())),
                    role=rec.get("role", "user"),
                )
            except Exception:
                continue

    def _save(self) -> None:
        payload = {"version": 1, "users": {n: u.to_json() for n, u in self._users.items()}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _bootstrap_admin(self) -> None:
        """Create the first admin on an empty store.

        The initial password comes from OMNITRADER_ADMIN_PASSWORD if set,
        otherwise a random one is generated and written to
        `<home>/admin-password.txt` (0600) — printed once at boot so the
        operator can actually log in. Shipping a hardcoded default admin
        password is how self-hosted tools get owned.
        """
        if self._users:
            return
        env_pw = os.environ.get("OMNITRADER_ADMIN_PASSWORD")
        generated = False
        if env_pw:
            password = env_pw
        else:
            password = secrets.token_urlsafe(16)
            generated = True
        self.create_user("admin", password, role="admin")
        marker = home_dir() / "admin-password.txt"
        try:
            marker.write_text(
                f"OmniTrader initial admin password: {password}\n"
                f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                "Delete this file after you change the password.\n")
            os.chmod(marker, 0o600)
        except OSError:
            pass
        self.initial_password = password
        self.initial_password_generated = generated

    # ------------------------------------------------------------------
    def create_user(self, username: str, password: str, role: str = "user") -> User:
        username = username.strip().lower()
        if not username or len(username) > 64 or not username.replace("_", "").isalnum():
            raise ValueError("username must be 1-64 alnum/underscore chars")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must be >= {MIN_PASSWORD_LENGTH} characters")
        with self._lock:
            if username in self._users:
                raise ValueError("username already exists")
            salt = secrets.token_bytes(SALT_BYTES)
            h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                    salt, PBKDF2_ITERATIONS, KEY_BYTES)
            user = User(username=username, salt=salt, hash=h,
                        created_at=time.time(), role=role)
            self._users[username] = user
            self._save()
            return user

    def get(self, username: str) -> Optional[User]:
        return self._users.get(username.strip().lower())

    def authenticate(self, username: str, password: str) -> Optional[User]:
        user = self.get(username)
        if user is None:
            # burn a hash anyway so a missing user is not instantly detectable
            hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                b"x" * SALT_BYTES, PBKDF2_ITERATIONS, KEY_BYTES)
            return None
        return user if user.verify(password) else None

    def set_password(self, username: str, new_password: str) -> None:
        if len(new_password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must be >= {MIN_PASSWORD_LENGTH} characters")
        with self._lock:
            user = self._users.get(username)
            if user is None:
                raise KeyError(username)
            user.salt = secrets.token_bytes(SALT_BYTES)
            user.hash = hashlib.pbkdf2_hmac("sha256", new_password.encode("utf-8"),
                                            user.salt, PBKDF2_ITERATIONS, KEY_BYTES)
            self._save()

    def list_users(self) -> list:
        return [{"username": u.username, "role": u.role,
                 "created_at": u.created_at} for u in self._users.values()]


# ----------------------------------------------------------------------
class SessionManager:
    """Issues and verifies HMAC-signed session tokens."""

    def __init__(self, secret: Optional[bytes] = None, path: Optional[Path] = None):
        self._path = Path(path) if path else home_dir() / "session-secret.key"
        if secret is None:
            secret = self._load_or_create_secret()
        self._secret = secret

    def _load_or_create_secret(self) -> bytes:
        if self._path.exists():
            try:
                raw = _b64d(self._path.read_text().strip())
                if len(raw) >= 32:
                    return raw
            except Exception:
                pass
        raw = secrets.token_bytes(48)
        try:
            self._path.write_text(_b64e(raw))
            os.chmod(self._path, 0o600)
        except OSError:
            pass
        return raw

    # ------------------------------------------------------------------
    def issue(self, username: str, ttl: int = SESSION_TTL_SECONDS) -> Dict[str, Any]:
        now = int(time.time())
        payload = {"u": username, "i": now, "e": now + ttl, "n": secrets.token_hex(8)}
        blob = _b64e(json.dumps(payload, separators=(",", ":")).encode())
        sig = self._sign(blob)
        return {"token": f"{blob}.{sig}", "expires_at": payload["e"],
                "expires_in": ttl, "username": username}

    def verify(self, token: str) -> Optional[Dict[str, Any]]:
        if not token or token.count(".") != 1:
            return None
        blob, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, self._sign(blob)):
            return None
        try:
            payload = json.loads(_b64d(blob))
        except Exception:
            return None
        if int(payload.get("e", 0)) < time.time():
            return None
        return payload

    def _sign(self, blob: str) -> str:
        return _b64e(hmac.new(self._secret, blob.encode("ascii"),
                              hashlib.sha256).digest())


class LoginGuard:
    """Trivial in-memory brute-force throttle (single process)."""

    def __init__(self, max_attempts: int = MAX_FAILED_ATTEMPTS,
                 lockout: int = LOCKOUT_SECONDS):
        self.max_attempts = max_attempts
        self.lockout = lockout
        self._fails: Dict[str, Tuple[int, float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> Tuple[bool, int]:
        with self._lock:
            n, until = self._fails.get(key, (0, 0.0))
            if until and time.time() < until:
                return False, int(until - time.time())
            return True, 0

    def record_failure(self, key: str) -> int:
        with self._lock:
            n, _ = self._fails.get(key, (0, 0.0))
            n += 1
            left = max(0, self.max_attempts - n)
            if n >= self.max_attempts:
                self._fails[key] = (n, time.time() + self.lockout)
            else:
                self._fails[key] = (n, 0.0)
            return left

    def clear(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
