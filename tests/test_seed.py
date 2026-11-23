import pytest

from app.commands import handle_message
from app.db import get_connection
from app.ledger import balance, currency
from app.seed import seed
from conftest import ensure_test_db_name, reset_test_db


def test_seed_creates_six_accounts_with_expected_balances():
    assert seed(reset=False) is True

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM accounts")
            assert int(cur.fetchone()[0]) == 6

    expected = [
        ("sys-usd", "USD", -4500),
        ("sys-zig", "ZIG", -10000),
        ("+263771000001", "USD", 3000),
        ("+263771000002", "USD", 0),
        ("+263771000003", "USD", 1500),
        ("+263771000004", "ZIG", 10000),
    ]
    for handle, expected_currency, expected_balance in expected:
        assert currency(handle) == expected_currency
        assert balance(handle) == expected_balance


def test_seed_balances_sum_to_zero_per_currency():
    seed(reset=False)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT currency, SUM(amount)::BIGINT
                FROM entries
                GROUP BY currency
                ORDER BY currency
                """
            )
            totals = {curr.strip(): int(total) for curr, total in cur.fetchall()}

    assert totals == {"USD": 0, "ZIG": 0}


def test_seed_again_without_reset_changes_nothing():
    assert seed(reset=False) is True
    handle_message("+263771000001", "SEND 5.00 TO 0771000002", "m-before-reseed")

    assert seed(reset=False) is False

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM accounts")
            assert int(cur.fetchone()[0]) == 6

    assert balance("+263771000001") == 2500
    assert balance("+263771000002") == 500


def test_seed_then_bal_command():
    seed(reset=False)

    assert handle_message("+263771000001", "BAL", "m1") == "Balance: 30.00 USD"


def test_seed_then_send_from_tendai_to_shop():
    seed(reset=False)

    reply = handle_message("+263771000001", "SEND 5.50 TO 0771000002", "m2")

    assert reply == "Sent 5.50 USD to +263771000002. New balance: 24.50 USD"
    assert balance("+263771000001") == 2450
    assert balance("+263771000002") == 550


def test_safety_check_refuses_non_test_database(monkeypatch):
    with pytest.raises(RuntimeError, match="_test"):
        ensure_test_db_name("ledger")

    with pytest.raises(RuntimeError, match="_test"):
        ensure_test_db_name("")

    monkeypatch.setenv("POSTGRES_DB", "ledger")
    with pytest.raises(RuntimeError, match="_test"):
        reset_test_db()
