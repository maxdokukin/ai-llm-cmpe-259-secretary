import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


DB_PATH = Path(__file__).resolve().parents[7] / "data" / "test" / "synthetic_data.sqlite"

returns_data = True

tool_schema = {
    "type": "function",
    "function": {
        "name": "sqlite_select",
        "description": (
            "Executes a restricted, read-only SQLite SELECT query. "
            "Supports simple SELECT/FROM/WHERE/ORDER BY/LIMIT queries only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "A restricted SELECT query. Supported shape: "
                        "SELECT columns FROM table "
                        "[WHERE col op value [AND col op value ...]] "
                        "[ORDER BY col ASC|DESC] [LIMIT n]. "
                        "Examples: "
                        "SELECT * FROM projects LIMIT 5; "
                        "SELECT id, title FROM projects WHERE slug = 'dune-buggy';"
                    ),
                }
            },
            "required": ["query"],
        },
    },
}


_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_TABLE_IDENTIFIER = _IDENTIFIER

_FORBIDDEN_KEYWORDS = [
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "CREATE",
    "REPLACE",
    "MERGE",
    "EXEC",
    "EXECUTE",
    "CALL",
    "COMMIT",
    "ROLLBACK",
    "UPSERT",
    "COPY",
    "VACUUM",
    "ANALYZE",
    "LOCK",
    "DO",
    "BEGIN",
    "END",
    "ATTACH",
    "DETACH",
    "PRAGMA",
]

_SELECT_RE = re.compile(
    rf"""
    ^\s*
    SELECT\s+(?P<columns>.+?)
    \s+FROM\s+(?P<table>{_TABLE_IDENTIFIER})
    (?:\s+WHERE\s+(?P<where>.*?)(?=\s+ORDER\s+BY|\s+LIMIT|\s*;?\s*$))?
    (?:\s+ORDER\s+BY\s+(?P<order_col>{_IDENTIFIER})(?:\s+(?P<order_dir>ASC|DESC))?)?
    (?:\s+LIMIT\s+(?P<limit>\d+))?
    \s*;?\s*$
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

_CONDITION_RE = re.compile(
    rf"""
    ^\s*
    (?P<column>{_IDENTIFIER})
    \s*
    (?P<operator>=|!=|<>|>=|<=|>|<|ILIKE|LIKE|IS|IN)
    \s*
    (?P<value>.+?)
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


def _get_db_path() -> Path:
    return Path(os.environ.get("SQLITE_DB_PATH", DB_PATH)).expanduser().resolve()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _reject_comments(query: str) -> bool:
    return "--" in query or "/*" in query or "*/" in query


def _has_stacked_queries(query: str) -> bool:
    stripped = query.strip()
    return ";" in stripped.rstrip(";")


def _contains_forbidden_keyword(query: str) -> bool:
    pattern = re.compile(
        r"\b(?:" + "|".join(map(re.escape, _FORBIDDEN_KEYWORDS)) + r")\b",
        re.IGNORECASE,
    )
    return bool(pattern.search(query))


def _is_query_safe(query: str) -> bool:
    clean_query = query.strip()

    if not clean_query:
        return False

    if not clean_query.upper().startswith("SELECT"):
        return False

    if _reject_comments(clean_query):
        return False

    if _has_stacked_queries(clean_query):
        return False

    if _contains_forbidden_keyword(clean_query):
        return False

    return True


def _parse_columns(columns: str) -> List[str]:
    columns = columns.strip()

    if columns == "*":
        return ["*"]

    parts = [part.strip() for part in columns.split(",")]

    if not parts or any(not part for part in parts):
        raise ValueError("Invalid SELECT column list.")

    for part in parts:
        if not re.fullmatch(_IDENTIFIER, part):
            raise ValueError(
                "Only simple column names are supported in SELECT. "
                "Aliases, functions, casts, and expressions are not allowed."
            )

    return parts


def _split_outside_quotes_and_parens(value: str, separator: str) -> List[str]:
    items = []
    current = []
    quote: Optional[str] = None
    paren_depth = 0
    i = 0

    while i < len(value):
        ch = value[i]

        if quote:
            current.append(ch)

            if ch == quote:
                if i + 1 < len(value) and value[i + 1] == quote:
                    current.append(value[i + 1])
                    i += 1
                else:
                    quote = None
        else:
            if ch in ("'", '"'):
                quote = ch
                current.append(ch)
            elif ch == "(":
                paren_depth += 1
                current.append(ch)
            elif ch == ")":
                paren_depth -= 1
                if paren_depth < 0:
                    raise ValueError("Unbalanced parentheses.")
                current.append(ch)
            elif (
                paren_depth == 0
                and value[i : i + len(separator)].upper() == separator.upper()
            ):
                items.append("".join(current).strip())
                current = []
                i += len(separator) - 1
            else:
                current.append(ch)

        i += 1

    if quote:
        raise ValueError("Unclosed quoted string.")

    if paren_depth != 0:
        raise ValueError("Unbalanced parentheses.")

    items.append("".join(current).strip())
    return items


def _split_where_conditions(where: str) -> List[str]:
    if not where:
        return []

    return _split_outside_quotes_and_parens(where, " AND ")


def _parse_literal(raw: str) -> Any:
    value = raw.strip()

    if not value:
        raise ValueError("Empty value in WHERE condition.")

    upper = value.upper()

    if upper == "NULL":
        return None

    if upper == "TRUE":
        return True

    if upper == "FALSE":
        return False

    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")

    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('""', '"')

    if re.fullmatch(r"-?\d+", value):
        return int(value)

    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)

    raise ValueError(
        f"Unsupported literal value: {raw!r}. "
        "Use quoted strings, numbers, true, false, or null."
    )


def _parse_in_list(raw: str) -> List[Any]:
    value = raw.strip()

    if not (value.startswith("(") and value.endswith(")")):
        raise ValueError("IN requires a parenthesized value list.")

    inner = value[1:-1].strip()

    if not inner:
        raise ValueError("IN list cannot be empty.")

    parts = _split_outside_quotes_and_parens(inner, ",")
    return [_parse_literal(part) for part in parts]


def _parse_query(query: str) -> Dict[str, Any]:
    match = _SELECT_RE.match(query)

    if not match:
        raise ValueError(
            "Unsupported query shape. Use: "
            "SELECT columns FROM table "
            "[WHERE col op value [AND col op value ...]] "
            "[ORDER BY col ASC|DESC] [LIMIT n]"
        )

    columns = _parse_columns(match.group("columns"))
    table = match.group("table")
    where = match.group("where")
    order_col = match.group("order_col")
    order_dir = match.group("order_dir") or "ASC"
    limit_raw = match.group("limit")

    conditions = []

    if where:
        for condition_text in _split_where_conditions(where):
            condition_match = _CONDITION_RE.match(condition_text)

            if not condition_match:
                raise ValueError(f"Unsupported WHERE condition: {condition_text!r}")

            column = condition_match.group("column")
            operator = condition_match.group("operator").upper()
            raw_value = condition_match.group("value")

            if operator == "IN":
                value = _parse_in_list(raw_value)
            else:
                value = _parse_literal(raw_value)

            conditions.append(
                {
                    "column": column,
                    "operator": operator,
                    "value": value,
                }
            )

    limit = None
    if limit_raw is not None:
        limit = int(limit_raw)
        if limit < 1 or limit > 1000:
            raise ValueError("LIMIT must be between 1 and 1000.")

    return {
        "columns": columns,
        "table": table,
        "conditions": conditions,
        "order_col": order_col,
        "order_dir": order_dir.upper(),
        "limit": limit,
    }


def _quote_identifier(identifier: str) -> str:
    if not re.fullmatch(_IDENTIFIER, identifier):
        raise ValueError(f"Invalid identifier: {identifier!r}")
    return f'"{identifier}"'


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _get_table_columns(conn: sqlite3.Connection, table_name: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({_quote_identifier(table_name)})").fetchall()
    return [row["name"] for row in rows]


def _validate_column(column: str, available_columns: Sequence[str]) -> None:
    if column not in available_columns:
        raise ValueError(f"Column '{column}' not found in table.")


def _sqlite_value(value: Any) -> Any:
    if value is True:
        return 1
    if value is False:
        return 0
    return value


def _build_where_clause(
    conditions: Sequence[Dict[str, Any]],
    available_columns: Sequence[str],
) -> Tuple[str, List[Any]]:
    if not conditions:
        return "", []

    clauses = []
    params: List[Any] = []

    for condition in conditions:
        column = condition["column"]
        operator = condition["operator"]
        value = condition["value"]

        _validate_column(column, available_columns)
        quoted_column = _quote_identifier(column)

        if operator == "=":
            if value is None:
                clauses.append(f"{quoted_column} IS NULL")
            else:
                clauses.append(f"{quoted_column} = ?")
                params.append(_sqlite_value(value))

        elif operator in ("!=", "<>"):
            if value is None:
                clauses.append(f"{quoted_column} IS NOT NULL")
            else:
                clauses.append(f"{quoted_column} != ?")
                params.append(_sqlite_value(value))

        elif operator in (">", ">=", "<", "<="):
            if value is None:
                raise ValueError(f"Operator {operator} does not support NULL.")
            clauses.append(f"{quoted_column} {operator} ?")
            params.append(_sqlite_value(value))

        elif operator == "LIKE":
            if not isinstance(value, str):
                raise ValueError("LIKE requires a quoted string value.")
            clauses.append(f"{quoted_column} LIKE ?")
            params.append(value)

        elif operator == "ILIKE":
            if not isinstance(value, str):
                raise ValueError("ILIKE requires a quoted string value.")
            clauses.append(f"LOWER({quoted_column}) LIKE LOWER(?)")
            params.append(value)

        elif operator == "IS":
            if value is None:
                clauses.append(f"{quoted_column} IS NULL")
            elif value is True:
                clauses.append(f"{quoted_column} IS 1")
            elif value is False:
                clauses.append(f"{quoted_column} IS 0")
            else:
                raise ValueError("IS only supports NULL, TRUE, or FALSE.")

        elif operator == "IN":
            if not isinstance(value, list):
                raise ValueError("IN requires a list of values.")
            if not value:
                raise ValueError("IN list cannot be empty.")

            placeholders = ", ".join("?" for _ in value)
            clauses.append(f"{quoted_column} IN ({placeholders})")
            params.extend(_sqlite_value(item) for item in value)

        else:
            raise ValueError(f"Unsupported operator: {operator}")

    return " WHERE " + " AND ".join(clauses), params


def _build_sql(conn: sqlite3.Connection, parsed: Dict[str, Any]) -> Tuple[str, List[Any]]:
    table_name = parsed["table"]

    if not _table_exists(conn, table_name):
        raise ValueError(f"Table '{table_name}' not found in database.")

    available_columns = _get_table_columns(conn, table_name)
    if not available_columns:
        raise ValueError(f"Table '{table_name}' has no columns.")

    if parsed["columns"] == ["*"]:
        select_sql = "*"
    else:
        for column in parsed["columns"]:
            _validate_column(column, available_columns)
        select_sql = ", ".join(_quote_identifier(column) for column in parsed["columns"])

    where_sql, params = _build_where_clause(parsed["conditions"], available_columns)

    order_sql = ""
    if parsed["order_col"]:
        _validate_column(parsed["order_col"], available_columns)
        order_sql = (
            f" ORDER BY {_quote_identifier(parsed['order_col'])} "
            f"{parsed['order_dir']}"
        )

    limit_sql = ""
    if parsed["limit"] is not None:
        limit_sql = " LIMIT ?"
        params.append(parsed["limit"])

    sql = (
        f"SELECT {select_sql} "
        f"FROM {_quote_identifier(table_name)}"
        f"{where_sql}"
        f"{order_sql}"
        f"{limit_sql}"
    )

    return sql, params


def execute(query: str) -> str:
    try:
        if not _is_query_safe(query):
            return (
                "Error: Security violation. Only standalone SELECT queries are "
                "permitted. Comments, stacked queries, and modification keywords "
                "are blocked."
            )

        db_path = _get_db_path()
        if not db_path.exists():
            return f"Error: SQLite database not found at: {db_path}"

        parsed = _parse_query(query)

        with _connect(db_path) as conn:
            sql, params = _build_sql(conn, parsed)
            rows = conn.execute(sql, params).fetchall()

        return json.dumps([dict(row) for row in rows], default=str)

    except Exception as e:
        return f"Error executing query: {str(e)}"


if __name__ == "__main__":
    print(execute("SELECT id, title, slug FROM projects ORDER BY ranking ASC LIMIT 5"))