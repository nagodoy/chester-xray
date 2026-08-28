"""Request-scoped authentication and authorization dependencies."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from chester.db import get_session
from chester.models import AuthSession, User, utcnow
from chester.security.access import AccessContext
from chester.security.tokens import hash_session_token

SESSION_HEADER = "X-Session-Token"


def client_ip(request: Request) -> str | None:
    """The caller's address, preferring the proxy header this runs behind."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else None)


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": SESSION_HEADER},
    )


def get_current_access(
    request: Request,
    db: Session = Depends(get_session),
) -> AccessContext:
    """Resolve the caller from their session token, or reject the request."""
    token = request.headers.get(SESSION_HEADER, "").strip()
    if not token:
        raise _unauthorized()

    auth_session = (
        db.query(AuthSession).filter(AuthSession.token_hash == hash_session_token(token)).first()
    )
    now = utcnow()
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= now
    ):
        raise _unauthorized("Session is invalid or expired")

    user = db.get(User, auth_session.user_id)
    if user is None or not user.active:
        # Access was withdrawn while the session was live; end it now rather than
        # letting the token keep working until it expires.
        auth_session.revoked_at = now
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not authorized for this application",
        )

    auth_session.last_seen_at = now
    return AccessContext.from_user(user)


def require_access(access: AccessContext = Depends(get_current_access)) -> AccessContext:
    return access


def require_admin(access: AccessContext = Depends(get_current_access)) -> AccessContext:
    if not access.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return access


def require_page(page: str) -> Callable[..., AccessContext]:
    """Gate an endpoint on a page permission."""

    def dependency(access: AccessContext = Depends(get_current_access)) -> AccessContext:
        if not access.can_access_page(page):
            raise HTTPException(status_code=403, detail="Page access denied")
        return access

    return dependency


def require_role(*roles: str) -> Callable[..., AccessContext]:
    """Gate an endpoint on the caller's role.

    Page permissions say which screens someone sees; roles say what they may do.
    The previous implementation enforced only the former, so every role that could
    reach a screen could perform every action on it.
    """
    allowed = frozenset(roles)

    def dependency(access: AccessContext = Depends(get_current_access)) -> AccessContext:
        if access.role not in allowed:
            raise HTTPException(status_code=403, detail="Este papel não pode executar esta ação.")
        return access

    return dependency
