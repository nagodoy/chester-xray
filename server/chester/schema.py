"""Create the database schema from the ORM, and report what that cannot fix.

This project has no migration tool. The ORM in `chester.models` is the schema, and
`Base.metadata.create_all` builds it on an empty database.

That comes with one sharp edge worth naming, because nothing else in the codebase
will: `create_all` only ever creates *whole tables*. It never adds a column to a
table that already exists. So a model change made against a live database applies
to new tables and silently not at all to existing ones, and the mismatch surfaces
much later as a confusing query error.

`drift()` compares tables, columns, types, nullability, server defaults, primary
keys, foreign keys, unique/check constraints, and indexes. `main()` refuses to
finish while any difference is present. Run it once before starting the
application -- not from inside the API or the worker, which start in parallel and
would race each other issuing DDL.

    python -m chester.schema

One shape of change it can fix in place: a *nullable* column added to a table
that already exists. `add_missing_columns()` issues the `ALTER TABLE ... ADD
COLUMN` for those, which is the one DDL that cannot invent or rewrite a value --
every existing row reads the new column as NULL. Anything else -- a dropped
column, a changed type, a new NOT NULL column (even with a default), a renamed
table -- is still reported by `drift()` and still has no in-place upgrade path:
apply a reviewed migration or drop the affected tables and recreate them.
"""

from __future__ import annotations

import logging
import re
import sys

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, inspect

import chester.models  # noqa: F401  -- registers every mapping on Base.metadata
from chester.db import Base, engine

logger = logging.getLogger(__name__)


def create() -> None:
    """Create every table the ORM declares that does not already exist."""
    Base.metadata.create_all(engine)


def add_missing_columns() -> list[str]:
    """Add nullable columns the models declare and the database lacks.

    Returns what it added, as "table.column", so the caller can log it. Deliberately
    narrow: a NOT NULL column would assign or require a value for existing rows,
    and a changed type requires knowing what the values mean. Both stay drift.
    """
    from sqlalchemy import text
    from sqlalchemy.schema import CreateColumn

    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    added: list[str] = []

    with engine.begin() as connection:
        for name, table in sorted(Base.metadata.tables.items()):
            if name not in present:
                continue
            actual = {column["name"] for column in inspector.get_columns(name)}
            for column in table.columns:
                if column.name in actual:
                    continue
                if not column.nullable or column.server_default is not None:
                    continue
                clause = CreateColumn(column).compile(bind=connection.engine)
                connection.execute(text(f"ALTER TABLE {name} ADD COLUMN {clause}"))
                added.append(f"{name}.{column.name}")

    return added


def _type_sql(column_type) -> str:
    """Return a canonical PostgreSQL spelling while retaining type parameters."""
    sql = " ".join(str(column_type.compile(dialect=engine.dialect)).upper().split())
    aliases = (
        (r"\bCHARACTER VARYING\b", "VARCHAR"),
        (r"\bTIMESTAMP(\(\d+\))? WITHOUT TIME ZONE\b", r"TIMESTAMP\1"),
        (r"\bTIMESTAMP(\(\d+\))? WITH TIME ZONE\b", r"TIMESTAMPTZ\1"),
        (r"\bTIME(\(\d+\))? WITHOUT TIME ZONE\b", r"TIME\1"),
        (r"\bTIME(\(\d+\))? WITH TIME ZONE\b", r"TIMETZ\1"),
        (r"\bDOUBLE PRECISION\b", "FLOAT(53)"),
        (r"\bDECIMAL\b", "NUMERIC"),
        (r"\bINT4\b", "INTEGER"),
        (r"\bINT8\b", "BIGINT"),
        (r"\bINT2\b", "SMALLINT"),
        (r"\bBOOL\b", "BOOLEAN"),
    )
    for pattern, replacement in aliases:
        sql = re.sub(pattern, replacement, sql)

    # Some dialect compilers omit timezone even though reflected SQLAlchemy types
    # retain it as metadata. Keep that semantic distinction in the comparison.
    if getattr(column_type, "timezone", None) is True:
        sql = re.sub(r"^TIMESTAMP(?=\(|$)", "TIMESTAMPTZ", sql)
        sql = re.sub(r"^TIME(?=\(|$)", "TIMETZ", sql)
    return re.sub(r"\s*([(),])\s*", r"\1", sql)


def _strip_outer_parentheses(sql: str) -> str:
    """Remove only parentheses that enclose the complete expression."""
    while sql.startswith("(") and sql.endswith(")"):
        depth = 0
        encloses_all = True
        for index, character in enumerate(sql):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0 and index != len(sql) - 1:
                    encloses_all = False
                    break
        if not encloses_all or depth != 0:
            break
        sql = sql[1:-1].strip()
    return sql


def _protect_sql_tokens(sql: str) -> tuple[str, list[str]]:
    """Replace quoted PostgreSQL tokens with inert placeholders."""
    tokens: list[str] = []
    protected: list[str] = []
    index = 0
    while index < len(sql):
        start = index
        delimiter = None
        character = sql[index]
        if character in {"'", '"'}:
            delimiter = character
            index += 1
            while index < len(sql):
                if sql[index] == "\\" and delimiter == "'" and index + 1 < len(sql):
                    index += 2
                    continue
                if sql[index] == delimiter:
                    if index + 1 < len(sql) and sql[index + 1] == delimiter:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
        elif character == "$":
            match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", sql[index:])
            if match:
                delimiter = match.group(0)
                index += len(delimiter)
                closing = sql.find(delimiter, index)
                index = len(sql) if closing < 0 else closing + len(delimiter)

        if delimiter is None:
            protected.append(character)
            index += 1
            continue

        token = sql[start:index]
        token_index = len(tokens)
        tokens.append(token)
        token_kind = "q" if delimiter == '"' else "s"
        protected.append(f"\x00{token_kind}{token_index}\x00")
    return "".join(protected), tokens


def _restore_sql_tokens(sql: str, tokens: list[str]) -> str:
    for index, token in enumerate(tokens):
        sql = sql.replace(f"\x00s{index}\x00", token)
        sql = sql.replace(f"\x00q{index}\x00", token)
    return sql


def _expression_sql(value) -> str | None:
    """Canonicalize PostgreSQL expression syntax without evaluating the SQL."""
    if value is None:
        return None
    argument = getattr(value, "arg", value)
    sql, tokens = _protect_sql_tokens(str(argument).strip())
    sql = _strip_outer_parentheses(" ".join(sql.split()))

    # PostgreSQL commonly adds these no-op coercions to reflected string literals.
    # Casts on identifiers or other expressions remain significant because their
    # equivalence cannot be established without evaluating the expression's type.
    sql = re.sub(
        r"(\x00s\d+\x00)\s*::\s*(?:character varying|varchar|text)\b",
        r"\1",
        sql,
        flags=re.I,
    )

    # Quoted tokens are placeholders here, so only keywords and unquoted
    # identifiers are case-folded and only structural whitespace is changed.
    sql = sql.lower()
    sql = re.sub(r"\s*([(),=<>+\-*/])\s*", r"\1", sql)
    sql = re.sub(r"\s+", " ", sql).strip()

    # Reflection often wraps a column or literal independently.
    previous = None
    while sql != previous:
        previous = sql
        sql = re.sub(r"(?<![\w.])\(([\w.\x00]+)\)", r"\1", sql)
        sql = _strip_outer_parentheses(sql)
    return _restore_sql_tokens(sql, tokens)


def _default_sql(value) -> str | None:
    """Return the canonical PostgreSQL expression used for a server default."""
    return _expression_sql(value)


def _column_tuple(columns) -> tuple[str, ...]:
    return tuple(column.name if hasattr(column, "name") else column for column in columns)


def _constraint_sets(table, inspector, name: str) -> dict[str, set[tuple]]:
    expected_unique = {
        _column_tuple(constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    actual_unique = {
        tuple(constraint["column_names"]) for constraint in inspector.get_unique_constraints(name)
    }

    expected_foreign = {
        (
            _column_tuple(constraint.columns),
            constraint.referred_table.name,
            _column_tuple(constraint.elements[i].column for i in range(len(constraint.elements))),
            (constraint.ondelete or "").upper(),
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    actual_foreign = {
        (
            tuple(constraint["constrained_columns"]),
            constraint["referred_table"],
            tuple(constraint["referred_columns"]),
            (constraint.get("options", {}).get("ondelete") or "").upper(),
        )
        for constraint in inspector.get_foreign_keys(name)
    }

    expected_checks = {
        _expression_sql(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    actual_checks = {
        _expression_sql(constraint["sqltext"])
        for constraint in inspector.get_check_constraints(name)
    }
    return {
        "unique constraint": expected_unique,
        "actual unique constraint": actual_unique,
        "foreign key": expected_foreign,
        "actual foreign key": actual_foreign,
        "check constraint": expected_checks,
        "actual check constraint": actual_checks,
    }


def _index_sets(table, inspector, name: str) -> tuple[set[tuple], set[tuple]]:
    expected = {(_column_tuple(index.columns), bool(index.unique)) for index in table.indexes}
    actual = {
        (tuple(index["column_names"]), bool(index.get("unique")))
        for index in inspector.get_indexes(name)
        if not index.get("duplicates_constraint")
    }
    return expected, actual


def _describe(values: set[tuple] | set[str]) -> str:
    return ", ".join(sorted(map(str, values)))


def drift() -> list[str]:
    """Return the differences between the ORM and the live database.

    Read-only, so it is safe to call from a running process. The comparison is
    intentionally strict: unexplained extra database objects are drift too.
    """
    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    problems: list[str] = []

    for name, table in sorted(Base.metadata.tables.items()):
        if name not in present:
            problems.append(f"{name}: table missing")
            continue
        reflected = {column["name"]: column for column in inspector.get_columns(name)}
        expected = {column.name: column for column in table.columns}
        missing = set(expected) - set(reflected)
        if missing:
            problems.append(f"{name}: missing column(s) {', '.join(sorted(missing))}")
        extra = set(reflected) - set(expected)
        if extra:
            problems.append(f"{name}: extra column(s) {', '.join(sorted(extra))}")

        for column_name in sorted(set(expected) & set(reflected)):
            model_column = expected[column_name]
            actual_column = reflected[column_name]
            expected_type = _type_sql(model_column.type)
            actual_type = _type_sql(actual_column["type"])
            if expected_type != actual_type:
                problems.append(
                    f"{name}.{column_name}: type is {actual_type}, expected {expected_type}"
                )
            if bool(model_column.nullable) != bool(actual_column["nullable"]):
                problems.append(
                    f"{name}.{column_name}: nullable is {actual_column['nullable']}, "
                    f"expected {model_column.nullable}"
                )
            expected_default = _default_sql(model_column.server_default)
            actual_default = _default_sql(actual_column.get("default"))
            if expected_default != actual_default:
                problems.append(
                    f"{name}.{column_name}: server default is {actual_default!r}, "
                    f"expected {expected_default!r}"
                )

        expected_pk = _column_tuple(table.primary_key.columns)
        actual_pk = tuple(inspector.get_pk_constraint(name).get("constrained_columns") or ())
        if expected_pk != actual_pk:
            problems.append(f"{name}: primary key is {actual_pk}, expected {expected_pk}")

        constraints = _constraint_sets(table, inspector, name)
        for label in ("unique constraint", "foreign key", "check constraint"):
            wanted = constraints[label]
            found = constraints[f"actual {label}"]
            if wanted - found:
                problems.append(f"{name}: missing {label}(s) {_describe(wanted - found)}")
            if found - wanted:
                problems.append(f"{name}: extra {label}(s) {_describe(found - wanted)}")

        expected_indexes, actual_indexes = _index_sets(table, inspector, name)
        if expected_indexes - actual_indexes:
            problems.append(
                f"{name}: missing index(es) {_describe(expected_indexes - actual_indexes)}"
            )
        if actual_indexes - expected_indexes:
            problems.append(
                f"{name}: extra index(es) {_describe(actual_indexes - expected_indexes)}"
            )

    return problems


def main() -> int:
    create()
    added = add_missing_columns()
    if added:
        logger.info("Added column(s) to existing tables: %s", ", ".join(added))
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
