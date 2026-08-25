"""The ORM is the schema. These tests hold that line."""

from __future__ import annotations

from sqlalchemy import inspect, text

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


def test_every_table_is_created(schema_engine):
    present = set(inspect(schema_engine).get_table_names())
    assert present >= EXPECTED_TABLES


def test_the_schema_matches_the_models(schema_engine):
    """Every table and column the ORM declares exists in the created schema."""
    import chester.models  # noqa: F401
    from chester.db import Base

    inspector = inspect(schema_engine)
    for table_name, table in Base.metadata.tables.items():
        assert table_name in inspector.get_table_names(), f"missing table {table_name}"
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        expected = {column.name for column in table.columns}
        assert expected <= actual, f"{table_name} missing columns {expected - actual}"


def test_studies_are_owned_by_a_user_and_an_organization(schema_engine):
    """The core of the rewrite: ownership is a foreign key, not an email string."""
    columns = {c["name"] for c in inspect(schema_engine).get_columns("studies")}
    assert {"owner_user_id", "organization_id"} <= columns
    assert "owner_id" not in columns


def test_creating_the_schema_twice_is_harmless(schema_engine):
    """Startup runs the create step every time, so it has to be idempotent."""
    from chester.schema import create, drift

    create()
    assert drift() == []


class TestDriftDetection:
    """Without a migration tool, drift is only caught if something looks for it.

    `create_all` never alters a table it already sees, so a model change made
    against a live database is applied to nothing. These tests prove the reporting
    that stands in for that -- the check the deploy step and the API startup both
    rely on to refuse to run quietly against a stale database.
    """

    def test_a_missing_column_is_reported(self, schema_engine):
        from chester.schema import create, drift

        with schema_engine.begin() as connection:
            connection.execute(text("ALTER TABLE studies DROP COLUMN body_part"))

        problems = drift()
        assert any("studies" in problem and "body_part" in problem for problem in problems)

        # create_all cannot repair a table that already exists -- the point of the check.
        create()
        assert any("body_part" in problem for problem in drift())

        with schema_engine.begin() as connection:
            connection.execute(text("ALTER TABLE studies ADD COLUMN body_part VARCHAR(64)"))
        assert drift() == []

    def test_a_missing_table_is_reported(self, schema_engine):
        from chester.schema import create, drift

        with schema_engine.begin() as connection:
            connection.execute(text("DROP TABLE access_control_audit_log"))

        assert any("access_control_audit_log" in problem for problem in drift())

        # A whole missing table is the one shape create_all *can* fix.
        create()
        assert drift() == []
