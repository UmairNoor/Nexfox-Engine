"""Request dependencies: current user (with revocation + suspension), tenant scoping, role guards, audit helper.

Roles: owner | member | viewer (customer roles, tenant-scoped); support (read-only admin); admin (super-admin).
"""
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db, scope_to_tenant
from .models import AuditLog, User
from .observability import bind, scrub
from .security import decode_token

bearer = HTTPBearer(auto_error=False)
ADMIN_ROLES = {"admin", "support"}


def user_from_token(db: Session, token: str, kind: str = "access") -> User:
    try:
        data = decode_token(token, kind)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session expired. Log in again.")
    user = db.get(User, int(data["sub"]))
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found.")
    if int(data.get("tv", 0)) != (user.token_version or 0):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session was signed out. Log in again.")
    if user.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is suspended. Contact support.")
    return user


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Log in to continue.")
    user = user_from_token(db, creds.credentials)
    # Row-Level Security applies to customer sessions only; admin endpoints work across tenants by design
    # and every admin action is authorised by role and written to the audit log.
    scope_to_tenant(db, None if user.role in ADMIN_ROLES else user.tenant_id)
    bind(user_id=user.id, tenant_id=user.tenant_id)
    return user


def owner_or_member(user: User = Depends(current_user)) -> User:
    if user.role == "viewer":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Viewers cannot change settings. Ask the account owner.")
    return user


def _require_2fa(user: User) -> None:
    if not user.totp_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins must enable two-factor authentication first.")


def staff_user(user: User = Depends(current_user)) -> User:
    """Read access to the admin console (admin or support)."""
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access only.")
    _require_2fa(user)
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    """Super-admin: every state-changing admin operation."""
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access only." if user.role not in ADMIN_ROLES
                            else "Support staff have read-only access.")
    _require_2fa(user)
    return user


def audit(db: Session, user: User | None, action: str, request: Request | None = None, tenant_id: int | None = None, **detail) -> None:
    db.add(AuditLog(tenant_id=tenant_id if tenant_id is not None else (user.tenant_id if user else None),
                    user_id=user.id if user else None, action=action, detail=scrub(detail),
                    ip=request.client.host if request and request.client else None))
