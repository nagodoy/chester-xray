"""Reviewed, manual migration of legacy PostgreSQL JSON columns to JSONB.

Run this before publishing a database created while ``JsonDocument`` mapped to
plain PostgreSQL ``json``:

    python -m chester.json_migration upgrade

The operation is deliberately separate from ``chester.schema`` because changing a
column type rewrites data and takes an ACCESS EXCLUSIVE table lock.  All columns
are converted in one transaction.  A temporary snapshot is compared after every
conversion, so any changed or missing document rolls the whole transaction back.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from chester.db import engine

logger = logging.getLogger(__name__)

# These are the nine columns created as JSON before JsonDocument was corrected.
# Newer JsonDocument columns were created as JSONB and need no conversion.
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

VALID_DIRECTIONS = {"upgrade": ("json", "jsonb"), "downgrade": ("jsonb", "json")}


def column_types(db_engine: Engine = engine) -> dict[tuple[str, str], str]:
    """Return the live PostgreSQL type of every affected column."""
    if db_engine.dialect.name != "postgresql":
        raise RuntimeError("The JSON migration only supports PostgreSQL.")

    inspector = inspect(db_engine)
    result: dict[tuple[str, str], str] = {}
    for table, column in JSON_COLUMNS:
        columns = {item["name"]: item for item in inspector.get_columns(table)}
        if column not in columns:
            raise RuntimeError(f"Required column {table}.{column} does not exist.")
        result[(table, column)] = str(columns[column]["type"]).lower()
    return result


def _snapshot_and_convert(
    connection: Connection, table: str, column: str, source: str, target: str
) -> None:
    """Convert one column and prove its JSON value and row association survived."""
    snapshot = f"_json_migration_{table}_{column}"
    connection.execute(
        text(
            f'CREATE TEMP TABLE "{snapshot}" ON COMMIT DROP AS '
            f'SELECT id, "{column}"::jsonb AS document FROM "{table}"'
        )
    )
    connection.execute(
        text(
            f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
            f'TYPE {target} USING "{column}"::{target}'
        )
    )
    changed = connection.execute(
        text(
            f'SELECT count(*) FROM "{snapshot}" snapshot '
            f'FULL OUTER JOIN "{table}" live USING (id) '
            f"WHERE snapshot.id IS NULL OR live.id IS NULL "
            f'OR snapshot.document IS DISTINCT FROM live."{column}"::jsonb'
        )
    ).scalar_one()
    if changed:
        raise RuntimeError(
            f"{table}.{column}: {changed} document(s) changed during {source} to {target}"
        )


def migrate(direction: str, db_engine: Engine = engine, *, lock_timeout: str = "5s") -> list[str]:
    """Apply an idempotent upgrade or downgrade and return converted columns."""
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"direction must be one of {', '.join(sorted(VALID_DIRECTIONS))}")
    source, target = VALID_DIRECTIONS[direction]
    types = column_types(db_engine)
    unexpected = {
        f"{table}.{column}={actual}"
        for (table, column), actual in types.items()
        if actual not in {source, target}
    }
    if unexpected:
        raise RuntimeError("Unexpected column type(s): " + ", ".join(sorted(unexpected)))

    pending = [pair for pair, actual in types.items() if actual == source]
    with db_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('lock_timeout', :timeout, true)"),
            {"timeout": lock_timeout},
        )
        for table, column in pending:
            _snapshot_and_convert(connection, table, column, source, target)

    converted = [f"{table}.{column}" for table, column in pending]
    final_types = column_types(db_engine)
    wrong = [
        f"{table}.{column}" for (table, column), actual in final_types.items() if actual != target
    ]
    if wrong:
        raise RuntimeError(f"Migration did not produce {target}: {', '.join(wrong)}")
    return converted


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=sorted(VALID_DIRECTIONS))
    parser.add_argument(
        "--lock-timeout",
        default="5s",
        help="PostgreSQL lock timeout (default: 5s); retry during a quiet period if exceeded",
    )
    args = parser.parse_args(argv)
    converted = migrate(args.direction, lock_timeout=args.lock_timeout)
    if converted:
        logger.info("Converted and verified: %s", ", ".join(converted))
    else:
        logger.info("Nothing to convert; all affected columns already have the target type.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
