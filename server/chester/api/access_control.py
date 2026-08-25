"""Administrator-only management of who may sign in."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from chester.api.deps import require_admin
from chester.db import get_session
from chester.models import AccessControlAuditLog, AllowedDomain, Organization, User
from chester.security.access import AccessContext
from chester.security.roles import (
    PAGE_LABELS,
    ROLE_ADMIN,
    ROLE_LABELS,
    normalize_allowed_pages,
    normalize_domain,
    normalize_email,
    normalize_role,
)

router = APIRouter(prefix="/api/access-control", tags=["access-control"])

ENV_ADMIN_IMMUTABLE = "Administradores de ambiente são somente leitura."
SELF_CHANGE_REFUSED = "Você não pode alterar seu próprio papel ou acesso."
LAST_ADMIN_REFUSED = "Não é possível remover ou desativar o último administrador."


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = "technician"
    allowed_pages: list[str] | None = None
    active: bool = True


class UserPatch(BaseModel):
    role: str | None = None
    allowed_pages: list[str] | None = None
    active: bool | None = None


class DomainCreate(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    role: str = "technician"
    allowed_pages: list[str] | None = None
    active: bool = True


class DomainPatch(BaseModel):
    role: str | None = None
    allowed_pages: list[str] | None = None
    active: bool | None = None


def _user_payload(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "role_label": ROLE_LABELS.get(user.role, user.role),
        "allowed_pages": user.allowed_pages,
        "active": user.active,
        "is_env_admin": user.is_env_admin,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


def _domain_payload(rule: AllowedDomain) -> dict:
    return {
        "id": str(rule.id),
        "domain": rule.domain,
        "role": rule.role,
        "role_label": ROLE_LABELS.get(rule.role, rule.role),
        "allowed_pages": rule.allowed_pages,
        "active": rule.active,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def _audit(
    db: Session,
    actor: AccessContext,
    action: str,
    target_type: str,
    target_key: str,
    target_role: str | None,
    details: dict | None = None,
) -> None:
    db.add(
        AccessControlAuditLog(
            actor_email=actor.email,
            actor_role=actor.role,
            action=action,
            target_type=target_type,
            target_key=target_key,
            target_role=target_role,
            details=details,
        )
    )


def _validated_role(value: str) -> str:
    try:
        return normalize_role(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Papel inválido.") from None


def _guard_mutation(
    db: Session,
    user: User,
    actor: AccessContext,
    *,
    next_role: str | None = None,
    next_active: bool | None = None,
) -> None:
    """Refuse changes that would lock the installation out of its own administration."""
    if user.is_env_admin:
        raise HTTPException(status_code=409, detail=ENV_ADMIN_IMMUTABLE)

    if user.id == actor.user_id and (
        (next_role is not None and next_role != user.role)
        or (next_active is not None and next_active != user.active)
    ):
        raise HTTPException(status_code=409, detail=SELF_CHANGE_REFUSED)

    losing_admin = (
        user.active
        and user.role == ROLE_ADMIN
        and ((next_role is not None and next_role != ROLE_ADMIN) or next_active is False)
    )
    if losing_admin:
        remaining = (
            db.query(User)
            .filter(
                User.active.is_(True),
                User.role == ROLE_ADMIN,
                User.organization_id == user.organization_id,
            )
            .count()
        )
        if remaining <= 1:
            raise HTTPException(status_code=409, detail=LAST_ADMIN_REFUSED)


@router.get("/metadata")
def metadata(_actor: AccessContext = Depends(require_admin)):
    """The roles and pages the interface may offer."""
    return {
        "roles": [{"value": key, "label": label} for key, label in ROLE_LABELS.items()],
        "pages": [{"value": key, "label": label} for key, label in PAGE_LABELS.items()],
    }


@router.get("/users")
def list_users(
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    users = (
        db.query(User)
        .filter(User.organization_id == actor.organization_id)
        .order_by(User.email)
        .all()
    )
    return [_user_payload(user) for user in users]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    email = normalize_email(body.email)
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido.")
    role = _validated_role(body.role)

    if db.query(User).filter(User.email == email).first() is not None:
        raise HTTPException(status_code=409, detail="Este email já possui acesso.")

    user = User(
        email=email,
        organization_id=actor.organization_id,
        role=role,
        allowed_pages=normalize_allowed_pages(body.allowed_pages),
        active=body.active,
        created_by=actor.email,
    )
    db.add(user)
    db.flush()
    _audit(db, actor, "create", "user", user.email, user.role, {"active": user.active})
    db.commit()
    db.refresh(user)
    return _user_payload(user)


@router.patch("/users/{user_id}")
def update_user(
    user_id: uuid.UUID,
    body: UserPatch,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    user = _load_user(db, actor, user_id)
    values = body.model_dump(exclude_unset=True)
    role = _validated_role(values["role"]) if "role" in values else None
    _guard_mutation(db, user, actor, next_role=role, next_active=values.get("active"))

    before = {"role": user.role, "allowed_pages": user.allowed_pages, "active": user.active}
    if role is not None:
        user.role = role
    if "allowed_pages" in values:
        user.allowed_pages = normalize_allowed_pages(values["allowed_pages"])
    if "active" in values:
        user.active = values["active"]

    _audit(db, actor, "update", "user", user.email, user.role, {"before": before})
    db.commit()
    db.refresh(user)
    return _user_payload(user)


@router.delete("/users/{user_id}")
def delete_user(
    user_id: uuid.UUID,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    """Deactivate rather than delete: studies reference their owner."""
    user = _load_user(db, actor, user_id)
    _guard_mutation(db, user, actor, next_active=False)

    user.active = False
    _audit(db, actor, "deactivate", "user", user.email, user.role)
    db.commit()
    return {"ok": True}


def _load_user(db: Session, actor: AccessContext, user_id: uuid.UUID) -> User:
    user = (
        db.query(User)
        .filter(User.id == user_id, User.organization_id == actor.organization_id)
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    return user


@router.get("/domains")
def list_domains(
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    rules = (
        db.query(AllowedDomain)
        .filter(AllowedDomain.organization_id == actor.organization_id)
        .order_by(AllowedDomain.domain)
        .all()
    )
    return [_domain_payload(rule) for rule in rules]


@router.post("/domains", status_code=status.HTTP_201_CREATED)
def create_domain(
    body: DomainCreate,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    domain = normalize_domain(body.domain)
    if "." not in domain:
        raise HTTPException(status_code=400, detail="Domínio inválido.")
    role = _validated_role(body.role)

    if db.query(AllowedDomain).filter(AllowedDomain.domain == domain).first() is not None:
        raise HTTPException(status_code=409, detail="Este domínio já possui uma regra.")

    rule = AllowedDomain(
        domain=domain,
        organization_id=actor.organization_id,
        role=role,
        allowed_pages=normalize_allowed_pages(body.allowed_pages),
        active=body.active,
        created_by=actor.email,
    )
    db.add(rule)
    db.flush()
    _audit(db, actor, "create", "domain", rule.domain, rule.role, {"active": rule.active})
    db.commit()
    db.refresh(rule)
    return _domain_payload(rule)


@router.patch("/domains/{domain_id}")
def update_domain(
    domain_id: uuid.UUID,
    body: DomainPatch,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    rule = _load_domain(db, actor, domain_id)
    values = body.model_dump(exclude_unset=True)
    before = {"role": rule.role, "allowed_pages": rule.allowed_pages, "active": rule.active}

    if "role" in values:
        rule.role = _validated_role(values["role"])
    if "allowed_pages" in values:
        rule.allowed_pages = normalize_allowed_pages(values["allowed_pages"])
    if "active" in values:
        rule.active = values["active"]

    _audit(db, actor, "update", "domain", rule.domain, rule.role, {"before": before})
    db.commit()
    db.refresh(rule)
    return _domain_payload(rule)


@router.delete("/domains/{domain_id}")
def delete_domain(
    domain_id: uuid.UUID,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    rule = _load_domain(db, actor, domain_id)
    _audit(db, actor, "delete", "domain", rule.domain, rule.role)
    db.delete(rule)
    db.commit()
    return {"ok": True}


def _load_domain(db: Session, actor: AccessContext, domain_id: uuid.UUID) -> AllowedDomain:
    rule = (
        db.query(AllowedDomain)
        .filter(
            AllowedDomain.id == domain_id,
            AllowedDomain.organization_id == actor.organization_id,
        )
        .first()
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Regra de domínio não encontrada.")
    return rule


@router.get("/audit")
def list_audit(
    limit: int = Query(100, ge=1, le=250),
    _actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    rows = (
        db.query(AccessControlAuditLog)
        .order_by(desc(AccessControlAuditLog.created_at))
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(row.id),
            "actor_email": row.actor_email,
            "actor_role": row.actor_role,
            "action": row.action,
            "target_type": row.target_type,
            "target_key": row.target_key,
            "target_role": row.target_role,
            "details": row.details,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/organization")
def current_organization(
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    organization = db.get(Organization, actor.organization_id)
    return {
        "id": str(organization.id),
        "name": organization.name,
        "slug": organization.slug,
    }
