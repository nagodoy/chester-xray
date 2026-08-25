"""store json documents as jsonb

Revision ID: f3826356d4c6
Revises: c9d294c53c45
Create Date: 2026-08-25 18:52:58.682693
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Migrations may reference the project's custom column types by their full path.
import chester.db  # noqa: F401


revision: str = "f3826356d4c6"
down_revision: str | None = "c9d294c53c45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Every column mapped to chester.db.JsonDocument, as (table, column).
JSON_COLUMNS: tuple[tuple[str, str], ...] = (
    ("access_control_audit_log", "details"),
    ("allowed_domains", "allowed_pages"),
    ("analysis_results", "raw_scores"),
    ("analysis_results", "op_normalized_scores"),
    ("analysis_results", "thresholds"),
    ("analysis_results", "above_threshold"),
    ("analysis_results", "above_threshold_findings"),
    ("audit_events", "detail"),
    ("users", "allowed_pages"),
)


def _convert(target: type[sa.types.TypeEngine], existing: type[sa.types.TypeEngine]) -> None:
    """Retype every JSON document column, on PostgreSQL only.

    SQLite has a single JSON representation, so there is nothing to convert there
    and no ``ALTER COLUMN ... TYPE`` to do it with. The test fixtures build their
    schema by running these migrations, so the guard keeps them working rather
    than merely skipping a no-op.

    PostgreSQL casts ``json`` to ``jsonb`` by assignment, so no ``USING`` clause is
    needed. The conversion is lossless for these columns: they hold score maps,
    page lists and audit payloads, none of which depend on key order, duplicate
    keys or whitespace -- the three things ``jsonb`` normalizes away.
    """

    if op.get_bind().dialect.name != "postgresql":
        return

    for table, column in JSON_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=target(),
            existing_type=existing(),
            existing_nullable=True,
        )


def upgrade() -> None:
    _convert(postgresql.JSONB, postgresql.JSON)


def downgrade() -> None:
    _convert(postgresql.JSON, postgresql.JSONB)
