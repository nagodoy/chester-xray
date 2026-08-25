"""The migration is the schema. These tests hold that line."""

from __future__ import annotations

from sqlalchemy import inspect

EXPECTED_TABLES = {
    "access_control_audit_log",
    "allowed_domains",
    "analysis_jobs",
    "analysis_results",
    "audit_events",
    "auth_challenges",
    "auth_sessions",
    "instances",
    "organizations",
    "stored_objects",
    "studies",
    "users",
}


def test_migration_creates_every_table(migrated_engine):
    present = set(inspect(migrated_engine).get_table_names())
    assert present >= EXPECTED_TABLES


def test_migration_matches_the_models(migrated_engine):
    """Every table and column declared on the ORM exists in the migrated schema.

    The previous project maintained db/schema.sql by hand alongside the ORM, so the
    two could drift silently. CI additionally runs `alembic check`, which catches
    the reverse direction.
    """
    import chester.models  # noqa: F401
    from chester.db import Base

    inspector = inspect(migrated_engine)
    for table_name, table in Base.metadata.tables.items():
        assert table_name in inspector.get_table_names(), f"missing table {table_name}"
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        expected = {column.name for column in table.columns}
        assert expected <= actual, f"{table_name} missing columns {expected - actual}"


def test_studies_are_owned_by_a_user_and_an_organization(migrated_engine):
    """The core of the rewrite: ownership is a foreign key, not an email string."""
    columns = {c["name"] for c in inspect(migrated_engine).get_columns("studies")}
    assert {"owner_user_id", "organization_id"} <= columns
    assert "owner_id" not in columns
