"""Create the database schema from the ORM, and report what that cannot fix.

This project has no migration tool. The ORM in `chester.models` is the schema, and
`Base.metadata.create_all` builds it on an empty database.

That comes with one sharp edge worth naming, because nothing else in the codebase
will: `create_all` only ever creates *whole tables*. It never adds a column to a
table that already exists. So a model change made against a live database applies
to new tables and silently not at all to existing ones, and the mismatch surfaces
much later as a confusing query error.

`drift()` looks for exactly that, and `main()` refuses to finish while any is
present. Run it once before starting the application -- not from inside the API or
the worker, which start in parallel and would race each other issuing DDL.

    python -m chester.schema

The fix for reported drift is to drop the affected tables and let this recreate
them. There is no in-place upgrade path, by design.
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import inspect

import chester.models  # noqa: F401  -- registers every mapping on Base.metadata
from chester.db import Base, engine

logger = logging.getLogger(__name__)


def create() -> None:
    """Create every table the ORM declares that does not already exist."""
    Base.metadata.create_all(engine)


def drift() -> list[str]:
    """Return the differences between the ORM and the live database.

    Read-only, so it is safe to call from a running process. Reports tables the
    database is missing entirely and columns missing from tables it does have --
    the two shapes `create_all` leaves behind.
    """
    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    problems: list[str] = []

    for name, table in sorted(Base.metadata.tables.items()):
        if name not in present:
            problems.append(f"{name}: table missing")
            continue
        actual = {column["name"] for column in inspector.get_columns(name)}
        missing = {column.name for column in table.columns} - actual
        if missing:
            problems.append(f"{name}: missing column(s) {', '.join(sorted(missing))}")

    return problems


def main() -> int:
    create()
    problems = drift()
    if problems:
        logger.error(
            "The database does not match the models, and creating tables cannot "
            "resolve it. Drop the affected tables and run this again. Found: %s",
            "; ".join(problems),
        )
        return 1
    logger.info("Schema is up to date (%d tables).", len(Base.metadata.tables))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
