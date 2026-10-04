from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuthToken, User

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthToken(token_hash=_token_hash(token), user_id=user.id))
    db.commit()
    return token


def user_for_token(db: Session, token: str) -> User | None:
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == _token_hash(token)))
    return row.user if row else None


def revoke_token(db: Session, token: str) -> None:
    row = db.get(AuthToken, _token_hash(token))
    if row:
        db.delete(row)
        db.commit()
