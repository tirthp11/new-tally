"""Password hashing, JWT creation/verification, and token hashing helpers."""
import datetime as dt
import hashlib
import secrets

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_token(subject: str, token_type: str, expires_delta: dt.timedelta) -> str:
    settings = get_settings()
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str) -> str:
    settings = get_settings()
    return _create_token(user_id, "access", dt.timedelta(minutes=settings.access_token_expire_minutes))


def create_refresh_token(user_id: str) -> str:
    settings = get_settings()
    return _create_token(user_id, "refresh", dt.timedelta(days=settings.refresh_token_expire_days))


def decode_token(token: str, expected_type: str) -> str | None:
    """Return the user id if the token is valid and of the expected type, else None."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != expected_type:
        return None
    return payload.get("sub")


# --- Connector pairing -------------------------------------------------------
# The pairing code is short and typed by a human; the long-lived token replaces
# it after the first exchange. Only hashes are stored in the database.

def generate_pairing_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no easily-confused characters
    part = lambda: "".join(secrets.choice(alphabet) for _ in range(4))
    return f"{part()}-{part()}"


def generate_connector_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
