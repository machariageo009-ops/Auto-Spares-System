
import os
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INSTANCE = ROOT / "instance"
INSTANCE.mkdir(exist_ok=True)

SCHEMA_FILE = ROOT / "schema.sql"

LEGACY_DATABASE = ROOT / "auto_spares.db"

DATABASE = Path(
    os.environ.get(
        "DATABASE_PATH",
        INSTANCE / "auto_spares.db"
    )
)


PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS parts (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    brand TEXT,
    engine TEXT,
    specification TEXT,
    buying_price DOUBLE PRECISION NOT NULL DEFAULT 0,
    selling_price DOUBLE PRECISION NOT NULL DEFAULT 0,
    stock_quantity INTEGER NOT NULL DEFAULT 0,
    low_stock_level INTEGER NOT NULL DEFAULT 5,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppliers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    company TEXT,
    parts_supplied TEXT,
    location TEXT,
    email TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    id SERIAL PRIMARY KEY,
    part_id INTEGER NOT NULL REFERENCES parts(id),
    quantity INTEGER NOT NULL,
    unit_price DOUBLE PRECISION NOT NULL,
    total_amount DOUBLE PRECISION NOT NULL,
    profit DOUBLE PRECISION NOT NULL,
    sale_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_history (
    id SERIAL PRIMARY KEY,
    part_id INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    old_price DOUBLE PRECISION,
    new_price DOUBLE PRECISION NOT NULL,
    reason TEXT,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_users (
    id SERIAL PRIMARY KEY,
    full_name TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def database_url():
    url = os.environ.get("DATABASE_URL", "").strip()

    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]

    return url


def using_postgres():
    return bool(database_url())


def is_hosted():
    return bool(
        os.environ.get("RENDER")
        or os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("FLY_APP_NAME")
        or os.environ.get("PUBLIC_URL")
        or using_postgres()
    )


class Row(dict):

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def wrap_row(row):

    if row is None:
        return None

    if isinstance(row, sqlite3.Row):
        row = dict(row)

    if isinstance(row, dict):
        cleaned = {}

        for key, value in row.items():

            if (
                hasattr(value, "as_tuple")
                and not isinstance(
                    value,
                    (int, float, str, bool, type(None))
                )
            ):
                cleaned[key] = float(value)
            else:
                cleaned[key] = value

        return Row(cleaned)

    return row


class Cursor:

    def __init__(self, cur):
        self.cur = cur

    @property
    def lastrowid(self):
        return self.cur.lastrowid

    def fetchone(self):
        return wrap_row(self.cur.fetchone())

    def fetchall(self):
        return [
            wrap_row(row)
            for row in self.cur.fetchall()
        ]


class Connection:

    def __init__(self, raw, postgres):
        self.raw = raw
        self.postgres = postgres

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.postgres else sql

    def execute(self, sql, params=()):
        cur = self.raw.cursor()
        cur.execute(self._sql(sql), params or ())
        return Cursor(cur)

    def insert(self, sql, params=()):

        sql = sql.strip().rstrip(";")

        if self.postgres:

            if "RETURNING" not in sql.upper():
                sql += " RETURNING id"

            row = self.execute(sql, params).fetchone()

            return row["id"] if row else None

        return self.execute(sql, params).lastrowid

    def commit(self):
        self.raw.commit()

    def close(self):
        self.raw.close()


def get_db():

    url = database_url()

    if url:

        import psycopg2
        from psycopg2.extras import RealDictCursor

        raw = psycopg2.connect(
            url,
            cursor_factory=RealDictCursor
        )

        return Connection(raw, True)

    if LEGACY_DATABASE.exists() and not DATABASE.exists():
        LEGACY_DATABASE.replace(DATABASE)

    raw = sqlite3.connect(DATABASE)

    raw.row_factory = sqlite3.Row

    raw.execute("PRAGMA foreign_keys = ON")

    return Connection(raw, False)


def _sqlite_columns(conn, table):

    return {
        row["name"]
        for row in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    }


def init_db():

    conn = get_db()

    if conn.postgres:

        statements = [
            item.strip()
            for item in PG_SCHEMA.split(";")
            if item.strip()
        ]

        for statement in statements:
            conn.execute(statement)

    else:

        conn.raw.executescript(
            SCHEMA_FILE.read_text(encoding="utf-8")
        )

        parts_columns = _sqlite_columns(conn, "parts")

        if "engine" not in parts_columns:
            conn.execute(
                "ALTER TABLE parts ADD COLUMN engine TEXT"
            )

        if "specification" not in parts_columns:
            conn.execute(
                "ALTER TABLE parts ADD COLUMN specification TEXT"
            )

        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        if "price_history" not in tables:

            conn.execute("""
                CREATE TABLE price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    part_id INTEGER NOT NULL,
                    old_price REAL,
                    new_price REAL NOT NULL,
                    reason TEXT,
                    changed_at TEXT NOT NULL,
                    FOREIGN KEY (part_id)
                        REFERENCES parts(id)
                        ON DELETE CASCADE
                )
            """)

        # Administrator account table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT NOT NULL
            )
        """)

        # Password reset tokens table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id)
                    REFERENCES admin_users(id)
                    ON DELETE CASCADE
            )
        """)

    conn.commit()
    conn.close()
