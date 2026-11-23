from pathlib import Path
import os
import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"


def _load_env(env_path: Path = REPO_ROOT / ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_db_name() -> str:
    env = _load_env()
    return os.environ.get("POSTGRES_DB", env["POSTGRES_DB"])


def get_connection(
    dbname: str | None = None,
    autocommit: bool = False,
) -> psycopg.Connection:
    env = _load_env()
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", env.get("POSTGRES_HOST", "localhost")),
        port=int(os.environ.get("POSTGRES_PORT", env.get("POSTGRES_PORT", "5432"))),
        dbname=dbname if dbname is not None else os.environ.get("POSTGRES_DB", env["POSTGRES_DB"]),
        user=os.environ.get("POSTGRES_USER", env["POSTGRES_USER"]),
        password=os.environ.get("POSTGRES_PASSWORD", env["POSTGRES_PASSWORD"]),
        autocommit=autocommit,
    )


def reset_schema(conn: psycopg.Connection) -> None:
    prev_autocommit = conn.autocommit
    if not prev_autocommit:
        conn.rollback()
        conn.autocommit = True
    try:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        if not prev_autocommit:
            conn.autocommit = prev_autocommit


def apply_migrations(conn: psycopg.Connection) -> None:
    prev_autocommit = conn.autocommit
    if not prev_autocommit:
        conn.rollback()
        conn.autocommit = True
    try:
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(migration.read_text())
    finally:
        if not prev_autocommit:
            conn.autocommit = prev_autocommit
