"""Authentication and authorization dependencies for FastAPI."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AccessControlAuditLog, AllowedDomain, AllowedEmail, AuthSession
from app.security.roles import email_domain, normalize_allowed_pages, normalize_email, pages_allow


def utcnow() -> datetime:
    return datetime.utcnow()


def hash_session_token(token: str) -> str:
    return hmac.new(
        settings.session_secret.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


@dataclass(frozen=True)
class AccessContext:
    email: str
    role: str
    allowed_pages: Optional[list[str]]
    is_admin: bool = False
    source: str = "database"

    @property
    def actor_id(self) -> str:
        return self.email

    def can_access_page(self, page: str) -> bool:
        return self.is_admin or pages_allow(self.allowed_pages, page)


def resolve_access(db: Session, email: str) -> Optional[AccessContext]:
    """Resolve access with environment-admin, email, then domain precedence."""
    normalized = normalize_email(email)
    if not normalized:
        return None

    if normalized in settings.admin_emails:
        return AccessContext(
            email=normalized,
            role="admin",
            allowed_pages=None,
            is_admin=True,
            source="environment",
        )

    direct = db.query(AllowedEmail).filter(AllowedEmail.email == normalized).first()
    if direct is not None:
        if not direct.active:
            return None
        return AccessContext(
            email=normalized,
            role=direct.role,
            allowed_pages=normalize_allowed_pages(direct.allowed_pages),
            is_admin=direct.role == "admin",
            source="email",
        )

    domain = email_domain(normalized)
    candidates = (
        db.query(AllowedDomain)
        .filter(AllowedDomain.active.is_(True))
        .all()
    )
    matching = [item for item in candidates if domain == item.domain or domain.endswith(f".{item.domain}")]
    if not matching:
        return None
    selected = max(matching, key=lambda item: len(item.domain))
    return AccessContext(
        email=normalized,
        role=selected.role,
        allowed_pages=normalize_allowed_pages(selected.allowed_pages),
        is_admin=selected.role == "admin",
        source="domain",
    )


def bootstrap_env_admins(db: Session) -> None:
    """Persist configured admins idempotently while keeping them immutable in UI."""
    configured_admins = set(settings.admin_emails)
    for email in configured_admins:
        entry = db.query(AllowedEmail).filter(AllowedEmail.email == email).first()
        if entry is None:
            db.add(
                AllowedEmail(
                    email=email,
                    role="admin",
                    allowed_pages=None,
                    active=True,
                    is_env_admin=True,
                    created_by="environment",
                )
            )
            db.add(
                AccessControlAuditLog(
                    actor_email="environment",
                    actor_role="admin",
                    action="bootstrap",
                    target_type="allowed_email",
                    target_key=email,
                    target_role="admin",
                    details={"source": "ADMIN_USERS/ADMIN_EMAILS"},
                )
            )
        elif not entry.is_env_admin or entry.role != "admin" or not entry.active:
            entry.role = "admin"
            entry.allowed_pages = None
            entry.active = True
            entry.is_env_admin = True
            db.add(
                AccessControlAuditLog(
                    actor_email="environment",
                    actor_role="admin",
                    action="environment_admin_synced",
                    target_type="allowed_email",
                    target_key=email,
                    target_role="admin",
                    details={"source": "ADMIN_USERS/ADMIN_EMAILS"},
                )
            )
    stale_entries = (
        db.query(AllowedEmail)
        .filter(AllowedEmail.is_env_admin.is_(True))
        .all()
    )
    for entry in stale_entries:
        if entry.email in configured_admins:
            continue
        entry.active = False
        entry.is_env_admin = False
        db.add(
            AccessControlAuditLog(
                actor_email="environment",
                actor_role="admin",
                action="environment_admin_revoked",
                target_type="allowed_email",
                target_key=entry.email,
                target_role=entry.role,
                details={"source": "ADMIN_USERS/ADMIN_EMAILS"},
            )
        )
    db.flush()


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "X-Session-Token"},
    )


def get_current_access(
    request: Request,
    db: Session = Depends(get_db),
) -> AccessContext:
    token = request.headers.get("X-Session-Token", "").strip()
    if not token:
        raise _unauthorized()

    session = (
        db.query(AuthSession)
        .filter(AuthSession.token_hash == hash_session_token(token))
        .first()
    )
    now = utcnow()
    if (
        session is None
        or session.revoked_at is not None
        or session.expires_at <= now
    ):
        raise _unauthorized("Session is invalid or expired")

    access = resolve_access(db, session.email)
    if access is None:
        session.revoked_at = now
        raise HTTPException(status_code=403, detail="User is not authorized for this application")

    session.last_seen_at = now
    return access


def require_auth(access: AccessContext = Depends(get_current_access)) -> str:
    """Compatibility dependency returning the authenticated email actor id."""
    return access.actor_id


def require_access(access: AccessContext = Depends(get_current_access)) -> AccessContext:
    return access


def require_admin(access: AccessContext = Depends(get_current_access)) -> AccessContext:
    if not access.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return access


def require_page(page: str):
    def dependency(access: AccessContext = Depends(get_current_access)) -> AccessContext:
        if not access.can_access_page(page):
            raise HTTPException(status_code=403, detail="Page access denied")
        return access

    return dependency