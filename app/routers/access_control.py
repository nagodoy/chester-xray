"""Administrator-only access control management endpoints."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.api.auth_deps import AccessContext, require_admin, resolve_access
from app.database import get_db
from app.models import AccessControlAuditLog, AllowedDomain, AllowedEmail, LegacyOwnerAlias, Study
from app.security.roles import (
    PAGE_LABELS,
    ROLE_LABELS,
    normalize_allowed_pages,
    normalize_domain,
    normalize_email,
    normalize_role,
)

router = APIRouter(prefix="/api", tags=["access-control"])


class AllowedEmailWrite(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = "technician"
    allowed_pages: Optional[list[str]] = None
    active: bool = True


class AllowedEmailPatch(BaseModel):
    role: Optional[str] = None
    allowed_pages: Optional[list[str]] = None
    active: Optional[bool] = None


class AllowedDomainWrite(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    role: str = "technician"
    allowed_pages: Optional[list[str]] = None
    active: bool = True


class AllowedDomainPatch(BaseModel):
    role: Optional[str] = None
    allowed_pages: Optional[list[str]] = None
    active: Optional[bool] = None


class LegacyOwnerMigration(BaseModel):
    legacy_owner_id: str = Field(min_length=1, max_length=128)
    email: str = Field(min_length=3, max_length=320)


def _email_payload(item: AllowedEmail) -> dict:
    return {
        "id": item.id,
        "email": item.email,
        "role": item.role,
        "role_label": ROLE_LABELS.get(item.role, item.role),
        "allowed_pages": item.allowed_pages,
        "active": item.active,
        "is_env_admin": item.is_env_admin,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _domain_payload(item: AllowedDomain) -> dict:
    return {
        "id": item.id,
        "domain": item.domain,
        "role": item.role,
        "role_label": ROLE_LABELS.get(item.role, item.role),
        "allowed_pages": item.allowed_pages,
        "active": item.active,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _audit(
    db: Session,
    actor: AccessContext,
    action: str,
    target_type: str,
    target_key: str,
    target_role: Optional[str],
    details: Optional[dict] = None,
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


def _ensure_email_mutable(
    db: Session,
    item: AllowedEmail,
    actor: AccessContext,
    *,
    next_role: Optional[str] = None,
    next_active: Optional[bool] = None,
) -> None:
    if item.is_env_admin:
        raise HTTPException(status_code=409, detail="Administradores de ambiente são somente leitura.")
    if item.email == actor.email and (
        (next_role is not None and next_role != item.role)
        or (next_active is not None and next_active != item.active)
    ):
        raise HTTPException(status_code=409, detail="Você não pode alterar seu próprio papel ou acesso.")
    would_remove_admin = item.active and item.role == "admin" and (
        next_role is not None and next_role != "admin"
        or next_active is False
    )
    if would_remove_admin:
        count = (
            db.query(AllowedEmail)
            .filter(AllowedEmail.active.is_(True), AllowedEmail.role == "admin")
            .count()
        )
        if count <= 1:
            raise HTTPException(status_code=409, detail="Não é possível remover ou desativar o último administrador.")


@router.get("/access-control/metadata")
def access_metadata(_actor: AccessContext = Depends(require_admin)):
    return {
        "roles": [{"value": key, "label": label} for key, label in ROLE_LABELS.items()],
        "pages": [{"value": key, "label": label} for key, label in PAGE_LABELS.items()],
    }


@router.get("/allowed-emails")
def list_allowed_emails(
    _actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return [_email_payload(item) for item in db.query(AllowedEmail).order_by(AllowedEmail.email).all()]


@router.get("/allowed-emails/env-admins")
def list_env_admins(
    _actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return [
        _email_payload(item)
        for item in db.query(AllowedEmail)
        .filter(AllowedEmail.is_env_admin.is_(True))
        .order_by(AllowedEmail.email)
        .all()
    ]


@router.post("/allowed-emails", status_code=status.HTTP_201_CREATED)
def create_allowed_email(
    body: AllowedEmailWrite,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    email = normalize_email(body.email)
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido.")
    try:
        role = normalize_role(body.role)
    except ValueError:
        raise HTTPException(status_code=400, detail="Papel inválido.")
    if db.query(AllowedEmail).filter(AllowedEmail.email == email).first():
        raise HTTPException(status_code=409, detail="Este email já possui uma regra de acesso.")
    item = AllowedEmail(
        email=email,
        role=role,
        allowed_pages=normalize_allowed_pages(body.allowed_pages),
        active=body.active,
        created_by=actor.email,
    )
    db.add(item)
    db.flush()
    _audit(db, actor, "create", "allowed_email", item.email, item.role, {"active": item.active})
    db.commit()
    db.refresh(item)
    return _email_payload(item)


@router.patch("/allowed-emails/{entry_id}")
def update_allowed_email(
    entry_id: str,
    body: AllowedEmailPatch,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.query(AllowedEmail).filter(AllowedEmail.id == entry_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Regra de email não encontrada.")
    values = body.model_dump(exclude_unset=True)
    role = None
    if "role" in values:
        try:
            role = normalize_role(values["role"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Papel inválido.")
    active = values.get("active")
    _ensure_email_mutable(db, item, actor, next_role=role, next_active=active)
    before = {"role": item.role, "allowed_pages": item.allowed_pages, "active": item.active}
    if role is not None:
        item.role = role
    if "allowed_pages" in values:
        item.allowed_pages = normalize_allowed_pages(values["allowed_pages"])
    if active is not None:
        item.active = active
    _audit(db, actor, "update", "allowed_email", item.email, item.role, {"before": before})
    db.commit()
    db.refresh(item)
    return _email_payload(item)


@router.delete("/allowed-emails/{entry_id}")
def delete_allowed_email(
    entry_id: str,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.query(AllowedEmail).filter(AllowedEmail.id == entry_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Regra de email não encontrada.")
    _ensure_email_mutable(db, item, actor, next_active=False)
    _audit(db, actor, "delete", "allowed_email", item.email, item.role)
    db.delete(item)
    db.commit()
    return {"ok": True}


@router.get("/allowed-domains")
def list_allowed_domains(
    _actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return [_domain_payload(item) for item in db.query(AllowedDomain).order_by(AllowedDomain.domain).all()]


@router.post("/allowed-domains", status_code=status.HTTP_201_CREATED)
def create_allowed_domain(
    body: AllowedDomainWrite,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    domain = normalize_domain(body.domain)
    if "." not in domain:
        raise HTTPException(status_code=400, detail="Domínio inválido.")
    try:
        role = normalize_role(body.role)
    except ValueError:
        raise HTTPException(status_code=400, detail="Papel inválido.")
    if db.query(AllowedDomain).filter(AllowedDomain.domain == domain).first():
        raise HTTPException(status_code=409, detail="Este domínio já possui uma regra de acesso.")
    item = AllowedDomain(
        domain=domain,
        role=role,
        allowed_pages=normalize_allowed_pages(body.allowed_pages),
        active=body.active,
        created_by=actor.email,
    )
    db.add(item)
    db.flush()
    _audit(db, actor, "create", "allowed_domain", item.domain, item.role, {"active": item.active})
    db.commit()
    db.refresh(item)
    return _domain_payload(item)


@router.patch("/allowed-domains/{entry_id}")
def update_allowed_domain(
    entry_id: str,
    body: AllowedDomainPatch,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.query(AllowedDomain).filter(AllowedDomain.id == entry_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Regra de domínio não encontrada.")
    values = body.model_dump(exclude_unset=True)
    before = {"role": item.role, "allowed_pages": item.allowed_pages, "active": item.active}
    if "role" in values:
        try:
            item.role = normalize_role(values["role"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Papel inválido.")
    if "allowed_pages" in values:
        item.allowed_pages = normalize_allowed_pages(values["allowed_pages"])
    if "active" in values:
        item.active = values["active"]
    _audit(db, actor, "update", "allowed_domain", item.domain, item.role, {"before": before})
    db.commit()
    db.refresh(item)
    return _domain_payload(item)


@router.delete("/allowed-domains/{entry_id}")
def delete_allowed_domain(
    entry_id: str,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.query(AllowedDomain).filter(AllowedDomain.id == entry_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Regra de domínio não encontrada.")
    _audit(db, actor, "delete", "allowed_domain", item.domain, item.role)
    db.delete(item)
    db.commit()
    return {"ok": True}


@router.get("/access-control-audit")
def get_access_control_audit(
    limit: int = 100,
    _actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(AccessControlAuditLog)
        .order_by(desc(AccessControlAuditLog.created_at))
        .limit(max(1, min(limit, 250)))
        .all()
    )
    return [
        {
            "id": row.id,
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


@router.get("/legacy-study-owners")
def list_legacy_study_owners(
    _actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.query(Study.owner_id, func.count(Study.id)).group_by(Study.owner_id).all()
    aliases = {item.legacy_owner_id: item.email for item in db.query(LegacyOwnerAlias).all()}
    return [
        {"owner_id": owner_id, "study_count": count, "mapped_email": aliases.get(owner_id)}
        for owner_id, count in rows
        if owner_id != "legacy-unassigned"
    ]


@router.post("/legacy-study-owners/migrate")
def migrate_legacy_study_owner(
    body: LegacyOwnerMigration,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    legacy_owner_id = body.legacy_owner_id.strip()
    email = normalize_email(body.email)
    if legacy_owner_id == "legacy-unassigned":
        raise HTTPException(status_code=409, detail="Estudos sem owner não podem ser atribuídos automaticamente.")
    if not re.fullmatch(r"user_[A-Za-z0-9]+", legacy_owner_id):
        raise HTTPException(
            status_code=400,
            detail="A origem deve ser um identificador legado no formato user_…",
        )
    if not legacy_owner_id or legacy_owner_id == email:
        raise HTTPException(status_code=400, detail="Owner legado inválido.")
    if resolve_access(db, email) is None:
        raise HTTPException(status_code=400, detail="O email de destino precisa estar autorizado.")
    alias = db.query(LegacyOwnerAlias).filter(
        LegacyOwnerAlias.legacy_owner_id == legacy_owner_id
    ).first()
    if alias and alias.email != email:
        raise HTTPException(status_code=409, detail="Este owner legado já foi associado a outro email.")
    if alias is None:
        alias = LegacyOwnerAlias(
            legacy_owner_id=legacy_owner_id,
            email=email,
            created_by=actor.email,
        )
        db.add(alias)
    moved = db.query(Study).filter(Study.owner_id == legacy_owner_id).update(
        {Study.owner_id: email}, synchronize_session=False
    )
    _audit(
        db, actor, "migrate_legacy_owner", "study_owner", legacy_owner_id, None,
        {"email": email, "study_count": moved},
    )
    db.commit()
    return {"ok": True, "legacy_owner_id": legacy_owner_id, "email": email, "study_count": moved}