"""Authentication helpers for the Abra NIP dashboard.

Preferred password hashing is Argon2 (via argon2-cffi). If that optional
package has not been installed yet, the app falls back to salted PBKDF2-HMAC-
SHA256 from Python's standard library instead of crashing at import time.

The verifier also accepts the dashboard's legacy unsalted SHA-256 hashes and
upgrades them to the strongest hashing method available after a successful
login.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerifyMismatchError

    _ph = PasswordHasher()
    ARGON2_AVAILABLE = True
except ImportError:
    PasswordHasher = None  # type: ignore[assignment]
    InvalidHashError = ValueError  # type: ignore[assignment,misc]
    VerifyMismatchError = ValueError  # type: ignore[assignment,misc]
    _ph = None
    ARGON2_AVAILABLE = False


PBKDF2_ITERATIONS = 600_000
PBKDF2_PREFIX = "$pbkdf2-sha256$"


@dataclass
class AuthResult:
    ok: bool
    message: str
    user: dict[str, Any] | None = None
    migrated_hash: str | None = None


def _pbkdf2_hash(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    digest_b64 = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{PBKDF2_PREFIX}{iterations}${salt_b64}${digest_b64}"


def _b64decode_nopad(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _verify_pbkdf2(password: str, stored_hash: str) -> bool:
    try:
        _, algorithm, iterations_text, salt_b64, digest_b64 = stored_hash.split("$", 4)
        if algorithm != "pbkdf2-sha256":
            return False
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 5_000_000:
            return False
        salt = _b64decode_nopad(salt_b64)
        expected = _b64decode_nopad(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password cannot be empty")

    if ARGON2_AVAILABLE and _ph is not None:
        return _ph.hash(password)

    # Safe dependency-free fallback. Adding argon2-cffi to requirements.txt
    # will automatically make future password hashes use Argon2 instead.
    return _pbkdf2_hash(password)


def _legacy_sha256(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(password: str, stored_hash: str | None) -> tuple[bool, str | None]:
    """Return ``(valid, replacement_hash)``.

    ``replacement_hash`` is returned when a valid legacy/PBKDF2 password can
    be transparently upgraded to Argon2, or when a legacy SHA-256 hash should
    be upgraded to the strongest currently available method.
    """
    if not password or not stored_hash:
        return False, None

    stored_hash = str(stored_hash).strip()

    if stored_hash.startswith("$argon2"):
        # Existing Argon2 hashes require argon2-cffi to verify. This case is
        # intentionally non-fatal so a missing dependency never crashes the
        # entire Streamlit app.
        if not ARGON2_AVAILABLE or _ph is None:
            return False, None
        try:
            valid = _ph.verify(stored_hash, password)
            replacement = (
                _ph.hash(password)
                if valid and _ph.check_needs_rehash(stored_hash)
                else None
            )
            return bool(valid), replacement
        except (VerifyMismatchError, InvalidHashError, ValueError):
            return False, None

    if stored_hash.startswith(PBKDF2_PREFIX):
        valid = _verify_pbkdf2(password, stored_hash)
        if not valid:
            return False, None
        # Transparently migrate PBKDF2 to Argon2 after the dependency is
        # installed; otherwise keep the existing PBKDF2 hash.
        replacement = hash_password(password) if ARGON2_AVAILABLE else None
        return True, replacement

    # Backwards compatibility with the original dashboard's SHA-256 hashes.
    candidate = _legacy_sha256(password)
    if hmac.compare_digest(candidate, stored_hash):
        return True, hash_password(password)

    return False, None


def authenticate_user(supabase, username: str, password: str) -> AuthResult:
    username = (username or "").strip()
    if not username or not password:
        return AuthResult(False, "Enter both username and password.")

    try:
        response = (
            supabase.table("user_accounts")
            .select("*")
            .eq("username", username)
            .limit(1)
            .execute()
        )
    except Exception:
        return AuthResult(False, "Unable to verify the account right now.")

    if not response.data:
        return AuthResult(False, "Invalid username or password.")

    user = dict(response.data[0])
    status = str(user.get("account_status", "Approved")).strip().lower()
    if status not in {"approved", "active"}:
        return AuthResult(False, "This account is not approved for access.")

    valid, replacement_hash = verify_password(password, user.get("password_hash"))
    if not valid:
        try:
            failed = int(user.get("failed_attempts") or 0) + 1
            supabase.table("user_accounts").update(
                {"failed_attempts": failed}
            ).eq("username", username).execute()
        except Exception:
            pass
        return AuthResult(False, "Invalid username or password.")

    try:
        updates: dict[str, Any] = {"failed_attempts": 0}
        if replacement_hash:
            updates["password_hash"] = replacement_hash
        supabase.table("user_accounts").update(updates).eq(
            "username", username
        ).execute()
    except Exception:
        # A successful login should not be blocked by an audit/migration write.
        pass

    return AuthResult(
        True,
        "Authenticated.",
        user=user,
        migrated_hash=replacement_hash,
    )
