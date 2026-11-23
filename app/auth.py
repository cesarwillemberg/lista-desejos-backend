"""Password hashing and JWT sessions."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from fastapi import Header, HTTPException

from app import db

# OWASP minimum. The library default (64 MiB, parallelism 4) takes about a
# second and a half on Render's small instance.
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
_ALGORITHM = "HS256"
_TOKEN_DAYS = 30


def jwt_secret() -> str:
    secret = (os.environ.get("JWT_SECRET") or "").strip()
    if len(secret) < 16:
        raise RuntimeError("Defina JWT_SECRET com pelo menos 16 caracteres.")
    return secret


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def issue_token(user_id: str, email: str, token_version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "tv": int(token_version),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=_TOKEN_DAYS)).timestamp()),
    }
    return jwt.encode(payload, jwt_secret(), algorithm=_ALGORITHM)


def current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Faça login para continuar.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, jwt_secret(), algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Sessão inválida. Entre de novo.") from exc
    user_id = payload.get("sub")
    if not isinstance(user_id, str):
        raise HTTPException(401, "Sessão inválida. Entre de novo.")
    user = db.get_user(user_id)
    if user is None:
        raise HTTPException(401, "Sessão inválida. Entre de novo.")
    claimed = payload.get("tv", 0)
    try:
        claimed = int(claimed)
    except (TypeError, ValueError):
        claimed = 0
    if claimed != int(user.get("token_version") or 0):
        raise HTTPException(401, "Sessão inválida. Entre de novo.")
    return user
