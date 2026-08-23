"""Safe owner identity translation used during the Clerk-to-email cutover."""
from sqlalchemy.orm import Session

from app.models import LegacyOwnerAlias


def resolve_ingest_owner(db: Session, owner_id: str) -> str:
    """Translate only administrator-approved legacy owners; never guess identities."""
    alias = db.query(LegacyOwnerAlias).filter(
        LegacyOwnerAlias.legacy_owner_id == owner_id
    ).first()
    return alias.email if alias else owner_id