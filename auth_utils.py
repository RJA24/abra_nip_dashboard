"""Authentication helpers for the Abra NIP dashboard.

Supports Argon2 for new passwords and transparently accepts/migrates the
legacy unsalted SHA-256 hashes already stored by older dashboard versions.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_ph = PasswordHasher()


@dataclass
class AuthResult:
    ok: bool
    message: str
    user: dict[str, Any] | None = None
    migrated_hash: str | None = None


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password cannot be empty")
    return _ph.hash(password)


def _legacy_sha256(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(password: str, stored_hash: str | None) -> tuple[bool, str | None]:
    """Return (valid, replacement_hash).

    replacement_hash is populated when a valid legacy SHA-256 password should
    be upgraded to Argon2.
    """
    if not password or not stored_hash:
        return False, None

    stored_hash = str(stored_hash).strip()

    if stored_hash.startswith("$argon2"):
        try:
            valid = _ph.verify(stored_hash, password)
            replacement = _ph.hash(password) if valid and _ph.check_needs_rehash(stored_hash) else None
            return bool(valid), replacement
        except (VerifyMismatchError, InvalidHashError, ValueError):
            return False, None

    # Backwards compatibility with the original dashboard.
    candidate = _legacy_sha256(password)
    if hmac.compare_digest(candidate, stored_hash):
        return True, _ph.hash(password)
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
        # Best-effort failed-attempt accounting. Authentication still fails even
        # if this audit update cannot be written.
        try:
            failed = int(user.get("failed_attempts") or 0) + 1
            supabase.table("user_accounts").update({"failed_attempts": failed}).eq("username", username).execute()
        except Exception:
            pass
        return AuthResult(False, "Invalid username or password.")

    try:
        updates: dict[str, Any] = {"failed_attempts": 0}
        if replacement_hash:
            updates["password_hash"] = replacement_hash
        supabase.table("user_accounts").update(updates).eq("username", username).execute()
    except Exception:
        # Successful authentication should not be blocked by an audit/migration
        # write failure.
        pass

    return AuthResult(True, "Authenticated.", user=user, migrated_hash=replacement_hash)
