from pathlib import Path
import os
import sys
import pytest
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TEST_DB_NAME = "ledger_test"
os.environ["POSTGRES_DB"] = TEST_DB_NAME

from app.db import apply_migrations, get_connection, get_db_name, reset_schema


def ensure_test_db_name(dbname: str) -> None:
    if not dbname or not dbname.endswith("_test"):
        raise RuntimeError(
            f"Refusing to wipe non-test database {dbname!r}: "
            "test database name must end with '_test'"
        )


def ensure_test_database_exists() -> None:
    dbname = get_db_name()
    ensure_test_db_name(dbname)
    with get_connection(dbname="postgres", autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))


def reset_test_db() -> None:
    dbname = get_db_name()
    ensure_test_db_name(dbname)
    with get_connection(autocommit=True) as conn:
        ensure_test_db_name(conn.info.dbname)
        reset_schema(conn)
        apply_migrations(conn)


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    ensure_test_database_exists()


@pytest.fixture(autouse=True)
def fresh_db():
    reset_test_db()


def _create_account(
    name: str,
    handle: str,
    currency: str = "USD",
    kind: str = "customer",
    allow_negative: bool = False,
) -> int:
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO accounts (name, currency, kind, allow_negative, handle)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (name, currency, kind, allow_negative, handle),
                )
                return int(cur.fetchone()[0])


def _deposit(
    system_handle: str,
    customer_handle: str,
    amount_cents: int,
    currency: str = "USD",
) -> int:
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM accounts WHERE handle = %s", (system_handle,))
                sys_id = int(cur.fetchone()[0])
                cur.execute("SELECT id FROM accounts WHERE handle = %s", (customer_handle,))
                cust_id = int(cur.fetchone()[0])
                cur.execute(
                    "INSERT INTO transactions (kind, description) VALUES ('deposit', 'Test deposit') RETURNING id"
                )
                tx_id = int(cur.fetchone()[0])
                cur.execute(
                    """
                    INSERT INTO entries (transaction_id, account_id, currency, amount)
                    VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)
                    """,
                    (tx_id, sys_id, currency, -amount_cents, tx_id, cust_id, currency, amount_cents),
                )
                return tx_id
