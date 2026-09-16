"""The ORM is the schema. These tests hold that line."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import DefaultClause, inspect, text

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

    def test_a_nullable_column_is_added_in_place(self, schema_engine):
        """The one repair that is safe without a migration tool.

        A nullable column added to a live table is what shipping a new optional
        field looks like -- patient_name and accession_number were exactly that --
        and the alternative on offer was dropping the studies table.
        """
        from chester.models import Study
        from chester.schema import add_missing_columns, drift

        with schema_engine.begin() as connection:
            connection.execute(text("ALTER TABLE studies DROP COLUMN accession_number"))

        accession_number = Study.__table__.c.accession_number
        accession_number.server_default = DefaultClause("'unknown'")
        try:
            assert add_missing_columns() == []
            assert any("accession_number" in problem for problem in drift())
        finally:
            accession_number.server_default = None

        assert add_missing_columns() == ["studies.accession_number"]
        assert drift() == []

        # Nothing to do the second time: the step runs on every start.
        assert add_missing_columns() == []

    def test_a_not_null_column_is_left_as_drift(self, schema_engine):
        """Narrow on purpose.

        A NOT NULL column with no server default cannot be added to a table that
        has rows, so guessing a value for them is the alternative -- and a guess
        about what a row means is exactly what this project has no migration tool
        in order not to make.
        """
        from chester.models import Study
        from chester.schema import add_missing_columns, drift

        with schema_engine.begin() as connection:
            connection.execute(text("ALTER TABLE studies DROP COLUMN source"))

        source = Study.__table__.c.source
        source.server_default = DefaultClause("'upload'")
        try:
            assert add_missing_columns() == []
            assert any("source" in problem for problem in drift())
        finally:
            source.server_default = None

        with schema_engine.begin() as connection:
            connection.execute(text("ALTER TABLE studies ADD COLUMN source VARCHAR(32) NOT NULL"))
        assert drift() == []

    def test_a_missing_table_is_reported(self, schema_engine):
        from chester.schema import create, drift

        with schema_engine.begin() as connection:
            connection.execute(text("DROP TABLE access_control_audit_log"))

        assert any("access_control_audit_log" in problem for problem in drift())

        # A whole missing table is the one shape create_all *can* fix.
        create()
        assert drift() == []

    def test_a_changed_type_blocks_schema_startup(self, schema_engine):
        from chester.schema import drift, main

        with schema_engine.begin() as connection:
            connection.execute(text("ALTER TABLE studies RENAME COLUMN body_part TO old_body_part"))
            connection.execute(text("ALTER TABLE studies ADD COLUMN body_part INTEGER"))
            connection.execute(text("ALTER TABLE studies DROP COLUMN old_body_part"))

        try:
            assert any("studies.body_part: type" in problem for problem in drift())
            assert main() == 1
        finally:
            with schema_engine.begin() as connection:
                connection.execute(text("ALTER TABLE studies DROP COLUMN body_part"))
                connection.execute(text("ALTER TABLE studies ADD COLUMN body_part VARCHAR(64)"))

    def test_changed_nullability_blocks_schema_startup(self, schema_engine):
        from chester.schema import drift, main

        with schema_engine.begin() as connection:
            connection.execute(text("ALTER TABLE studies RENAME COLUMN body_part TO old_body_part"))
            connection.execute(
                text("ALTER TABLE studies ADD COLUMN body_part VARCHAR(64) NOT NULL")
            )
            connection.execute(text("ALTER TABLE studies DROP COLUMN old_body_part"))

        try:
            assert any(
                "studies.body_part: nullable is False, expected True" in problem
                for problem in drift()
            )
            assert main() == 1
        finally:
            with schema_engine.begin() as connection:
                connection.execute(text("ALTER TABLE studies DROP COLUMN body_part"))
                connection.execute(text("ALTER TABLE studies ADD COLUMN body_part VARCHAR(64)"))


def test_api_startup_refuses_schema_drift(monkeypatch):
    import chester.main as main_module

    monkeypatch.setattr(main_module, "schema_drift", lambda: ["studies.body_part: incompatible"])

    async def start():
        async with main_module.lifespan(main_module.app):
            pass

    with pytest.raises(RuntimeError, match="studies.body_part"):
        asyncio.run(start())


def test_worker_startup_refuses_schema_drift(monkeypatch):
    import chester.worker as worker

    monkeypatch.setattr(worker, "schema_drift", lambda: ["studies.body_part: incompatible"])

    assert worker.main() == 1
