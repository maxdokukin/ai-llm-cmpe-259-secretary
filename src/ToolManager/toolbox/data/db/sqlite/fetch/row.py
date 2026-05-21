import json
import os
import re
import sqlite3
from pathlib import Path
from typing import List


DB_PATH = Path(__file__).resolve().parents[7] / "data" / "test" / "synthetic_data.sqlite"

returns_data = True

tool_schema = {
    "type": "function",
    "function": {
        "name": "sqlite_fetch_row",
        "description": (
            "Fetches a single row from a specified SQLite table using the slug "
            "as the unique identifier."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "The name of the SQLite table to query.",
                },
                "slug": {
                    "type": "string",
                    "description": "The slug identifier for the specific row to fetch.",
                },
            },
            "required": ["table_name", "slug"],
        },
    },
}


_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"


def _get_db_path() -> Path:
    return Path(os.environ.get("SQLITE_DB_PATH", DB_PATH)).expanduser().resolve()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


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


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return column_name in _get_table_columns(conn, table_name)


def execute(table_name: str, slug: str) -> str:
    try:
        db_path = _get_db_path()

        if not db_path.exists():
            return f"Error: SQLite database not found at: {db_path}"

        if not table_name or not slug:
            return "Error: table_name and slug are required."

        if not re.fullmatch(_IDENTIFIER, table_name):
            return (
                "Error: Invalid table_name. Use only letters, numbers, and "
                "underscores, and do not start with a number."
            )

        with _connect(db_path) as conn:
            if not _table_exists(conn, table_name):
                return f"Error: Table '{table_name}' not found in database."

            if not _column_exists(conn, table_name, "slug"):
                return f"Error: Table '{table_name}' does not contain a slug column."

            row = conn.execute(
                f"SELECT * FROM {_quote_identifier(table_name)} WHERE slug = ? LIMIT 1",
                (slug,),
            ).fetchone()

        if row is None:
            return f"Error: Entry with slug '{slug}' not found in table '{table_name}'."

        return json.dumps(dict(row), default=str)

    except Exception as e:
        return f"Error executing row fetch: {str(e)}"


if __name__ == "__main__":
    print(execute("projects", "dune-buggy"))