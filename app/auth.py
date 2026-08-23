"""Backward-compatible imports for the application's own session auth."""
from __future__ import annotations

from typing import Optional

from app.api.auth_deps import (
    AccessContext,
    bootstrap_env_admins,
    get_current_access,
    require_access,
    require_admin,
    require_auth,
    require_page,
    resolve_access,
)
from app.config import settings
from app.security.roles import normalize_email


def is_authorized_email(email: Optional[str]) -> bool:
    """Compatibility helper for configured environment administrators."""
    return normalize_email(email) in settings.admin_emails


__all__ = [
    "AccessContext",
    "bootstrap_env_admins",
    "get_current_access",
    "is_authorized_email",
    "require_access",
    "require_admin",
    "require_auth",
    "require_page",
    "resolve_access",
]