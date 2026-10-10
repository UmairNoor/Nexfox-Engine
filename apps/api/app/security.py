"""Passwords, JWT + refresh sessions, TOTP 2FA, field encryption, MT5 credential vault, license keys, worker tokens,
and EA response signing."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

settings = get_settings()
_ph = PasswordHasher()
_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.app_secret.encode()).digest()))
# MT5 passwords use their own key. Dev fallback is derived from APP_SECRET; production refuses to start without one.
_cred_fernet = Fernet(settings.mt5_credentials_key.encode() if settings.mt5_credentials_key else
                      base64.urlsafe_b64encode(hashlib.sha256(b"mt5-cred:" + settings.app_secret.encode()).digest()))


# ---- passwords ----
def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


# ---- JWT ----
def create_token(sub: int, kind: str, minutes: float, **extra) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(sub), "kind": kind, "iat": now, "exp": now + timedelta(minutes=minutes), **extra}
    return jwt.encode(payload, settings.app_secret, algorithm="HS256")


def decode_token(token: str, kind: str) -> dict:
    data = jwt.decode(token, settings.app_secret, algorithms=["HS256"])
    if data.get("kind") != kind:
        raise jwt.InvalidTokenError("wrong token kind")
    return data


def issue_session(db, user, request=None) -> dict:
    """Access token (short) + refresh token bound to a stored, revocable session row."""
    from .models import AuthSession
    jti = secrets.token_hex(16)
    expires = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days)
    db.add(AuthSession(user_id=user.id, jti=jti, expires_at=expires,
                       ip=request.client.host if request is not None and request.client else None,
                       user_agent=(request.headers.get("user-agent", "")[:200] if request is not None else None)))
    db.flush()
    return {
        "access_token": create_token(user.id, "access", settings.access_token_minutes, tv=user.token_version),
        "refresh_token": create_token(user.id, "refresh", settings.refresh_token_days * 24 * 60, jti=jti, tv=user.token_version),
        "token_type": "bearer",
    }


# ---- encryption for secrets at rest (TOTP seeds, license keys) ----
def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet.decrypt(value.encode()).decode()


# ---- MT5 credential vault ----
def encrypt_credential(value: str) -> str:
    return _cred_fernet.encrypt(value.encode()).decode()


def decrypt_credential(value: str) -> str | None:
    try:
        return _cred_fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        return None


def new_fernet_key() -> str:
    return Fernet.generate_key().decode()


# ---- TOTP ----
def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="NexFox Engine")


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)


# ---- short codes (email verification, password reset) ----
def new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def hash_code(code: str) -> str:
    return hmac.new(settings.app_secret.encode(), code.encode(), hashlib.sha256).hexdigest()


# ---- license keys ----
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_license_key() -> str:
    groups = ["".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(4)]
    return "AUSC-" + "-".join(groups)


def license_hash(key: str) -> str:
    return hmac.new(settings.app_secret.encode(), key.strip().upper().encode(), hashlib.sha256).hexdigest()


# ---- worker tokens ----
def new_worker_token() -> str:
    return "nfw_" + secrets.token_urlsafe(32)


def worker_token_hash(token: str) -> str:
    return hmac.new(settings.app_secret.encode(), b"worker:" + token.strip().encode(), hashlib.sha256).hexdigest()


# ---- EA response signing (HMAC-SHA256, verified inside the EA) ----
def sign_for_ea(payload: dict) -> dict:
    """Returns {"payload": <json string>, "sig": <hex>}; payload carries an expiry so a copied response cannot be replayed."""
    body = dict(payload)
    body["iat"] = int(time.time())
    body["exp"] = body["iat"] + settings.handshake_ttl_seconds
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True)
    sig = hmac.new(settings.ea_signing_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return {"payload": raw, "sig": sig}


def verify_ea_signature(raw: str, sig: str) -> bool:
    expected = hmac.new(settings.ea_signing_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
