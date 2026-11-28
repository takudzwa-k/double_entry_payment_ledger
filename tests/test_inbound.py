from concurrent.futures import ThreadPoolExecutor
import threading
import pytest

from app import commands
from app.commands import handle_message
from app.db import get_connection
from app.ledger import balance
from app.pin import change_pin, set_pin
from conftest import _create_account, _deposit


def _setup_two_users():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")
    _deposit("sys-usd", "+263771000001", 3000)
    set_pin("+263771000001", "1234")
    set_pin("+263771000002", "5678")


def _pin_state(handle: str) -> tuple[int, bool]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    pin_failed_attempts,
                    (pin_locked_until IS NOT NULL AND pin_locked_until > now())
                FROM accounts
                WHERE handle = %s
                """,
                (handle,),
            )
            attempts, is_locked = cur.fetchone()
            return int(attempts), bool(is_locked)


def test_same_message_id_twice_moves_money_once_with_identical_reply():
    _setup_two_users()

    r1 = handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "inb-dup-1")
    r2 = handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "inb-dup-1")

    assert r1 == "Sent 5.50 USD to +263771000002. New balance: 24.50 USD"
    assert r2 == r1
    assert balance("+263771000001") == 2450
    assert balance("+263771000002") == 550


def test_wrong_pin_delivered_three_times_same_id_counts_once():
    _setup_two_users()

    replies = [
        handle_message("+263771000001", "SEND 5.50 TO 0771000002 0000", "inb-wrong-dup")
        for _ in range(3)
    ]

    assert replies == ["Wrong PIN. 2 attempts left."] * 3
    attempts, is_locked = _pin_state("+263771000001")
    assert attempts == 1
    assert is_locked is False
    assert balance("+263771000001") == 3000
    assert balance("+263771000002") == 0


def test_wrong_pin_three_different_ids_locks_account():
    _setup_two_users()

    r1 = handle_message("+263771000001", "SEND 5.50 TO 0771000002 0000", "inb-w1")
    r2 = handle_message("+263771000001", "SEND 5.50 TO 0771000002 0000", "inb-w2")
    r3 = handle_message("+263771000001", "SEND 5.50 TO 0771000002 0000", "inb-w3")

    assert r1 == "Wrong PIN. 2 attempts left."
    assert r2 == "Wrong PIN. 1 attempt left."
    assert r3.startswith("Account locked. Try again in ")
    attempts, is_locked = _pin_state("+263771000001")
    assert attempts == 3
    assert is_locked is True


def test_valid_send_replayed_after_lock_returns_original_reply():
    _setup_two_users()

    first = handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "inb-lock-replay")
    assert first == "Sent 5.50 USD to +263771000002. New balance: 24.50 USD"

    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE accounts
                    SET pin_failed_attempts = 3,
                        pin_locked_until = now() + INTERVAL '15 minutes'
                    WHERE handle = %s
                    """,
                    ("+263771000001",),
                )

    replay = handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "inb-lock-replay")
    assert replay == first
    assert balance("+263771000001") == 2450
    assert balance("+263771000002") == 550


def test_valid_send_replayed_after_pin_change_returns_original_reply():
    _setup_two_users()

    first = handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "inb-pinchg-replay")
    assert first == "Sent 5.50 USD to +263771000002. New balance: 24.50 USD"

    change_pin("+263771000001", "1234", "9999")

    replay = handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "inb-pinchg-replay")
    assert replay == first
    assert balance("+263771000001") == 2450
    assert balance("+263771000002") == 550


def test_bal_replay_returns_original_reply_after_balance_changes():
    _setup_two_users()

    first = handle_message("+263771000001", "BAL", "inb-bal-1")
    assert first == "Balance: 30.00 USD"

    handle_message("+263771000001", "SEND 5.00 TO 0771000002 1234", "inb-bal-send")
    assert balance("+263771000001") == 2500

    replay = handle_message("+263771000001", "BAL", "inb-bal-1")
    assert replay == "Balance: 30.00 USD"


def test_same_message_id_from_different_sender_rejected_without_changes():
    _setup_two_users()

    first = handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "inb-sender-1")
    assert first == "Sent 5.50 USD to +263771000002. New balance: 24.50 USD"

    other = handle_message("+263771000002", "BAL", "inb-sender-1")
    assert other == "This message was already used. Please try again."
    assert balance("+263771000001") == 2450
    assert balance("+263771000002") == 550


def test_ten_parallel_deliveries_same_id_moves_money_once_identical_replies():
    _setup_two_users()

    workers = 10
    barrier = threading.Barrier(workers)

    def deliver(_idx: int) -> str:
        barrier.wait()
        return handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "inb-parallel-1")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        replies = list(pool.map(deliver, range(workers)))

    expected = "Sent 5.50 USD to +263771000002. New balance: 24.50 USD"
    assert replies == [expected] * workers
    assert balance("+263771000001") == 2450
    assert balance("+263771000002") == 550


def test_stale_processing_row_is_taken_over_and_processed_once():
    _setup_two_users()

    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO inbound_messages (message_id, sender, status, created_at)
                    VALUES (%s, %s, 'processing', now() - INTERVAL '10 minutes')
                    """,
                    ("inb-stale-1", "+263771000001"),
                )

    reply = handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "inb-stale-1")
    assert reply == "Sent 5.50 USD to +263771000002. New balance: 24.50 USD"
    assert balance("+263771000001") == 2450
    assert balance("+263771000002") == 550

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, reply, completed_at IS NOT NULL FROM inbound_messages WHERE message_id = %s",
                ("inb-stale-1",),
            )
            status, stored_reply, has_completed = cur.fetchone()
    assert status == "done"
    assert stored_reply == reply
    assert has_completed is True


def test_fresh_processing_row_times_out_with_processing_reply(monkeypatch):
    _setup_two_users()
    monkeypatch.setattr(commands, "PROCESSING_WAIT_LIMIT_SECONDS", 0.3)

    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO inbound_messages (message_id, sender, status, created_at)
                    VALUES (%s, %s, 'processing', now())
                    """,
                    ("inb-fresh-1", "+263771000001"),
                )

    reply = handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "inb-fresh-1")
    assert reply == "Your message is being processed. Please try again shortly."
    assert balance("+263771000001") == 3000
    assert balance("+263771000002") == 0


def test_unexpected_error_deletes_processing_row_and_retry_succeeds(monkeypatch, caplog):
    _setup_two_users()

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(commands, "_process_message", boom)

    with caplog.at_level("ERROR"):
        failed = handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "inb-retry-1")

    assert failed == "Something went wrong. Please try again."
    assert "RuntimeError" in caplog.text
    assert "1234" not in caplog.text

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM inbound_messages WHERE message_id = %s", ("inb-retry-1",))
            assert int(cur.fetchone()[0]) == 0

    monkeypatch.undo()

    retried = handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "inb-retry-1")
    assert retried == "Sent 5.50 USD to +263771000002. New balance: 24.50 USD"
    assert balance("+263771000001") == 2450
    assert balance("+263771000002") == 550


@pytest.mark.parametrize("bad_id", ["", "   ", None, 123])
def test_empty_or_invalid_message_id_returns_generic_reply_and_stores_nothing(bad_id):
    _setup_two_users()

    reply = handle_message("+263771000001", "BAL", bad_id)
    assert reply == "Something went wrong. Please try again."

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM inbound_messages")
            assert int(cur.fetchone()[0]) == 0


def test_privacy_no_pin_or_command_text_stored_in_inbound_messages():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")
    _deposit("sys-usd", "+263771000001", 3000)

    assert handle_message("+263771000001", "PIN SET 1234", "priv-1") == "PIN set."
    assert handle_message("+263771000001", "BAL", "priv-2") == "Balance: 30.00 USD"
    assert (
        handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "priv-3")
        == "Sent 5.50 USD to +263771000002. New balance: 24.50 USD"
    )
    assert (
        handle_message("+263771000001", "SEND 5.50 TO 0771000002 9876", "priv-4")
        == "Wrong PIN. 2 attempts left."
    )
    assert handle_message("+263771000001", "PIN CHANGE 1234 4321", "priv-5") == "PIN changed."

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM inbound_messages")
            rows = cur.fetchall()

    assert len(rows) == 5
    for row in rows:
        for col in row:
            text_val = "" if col is None else str(col)
            assert "1234" not in text_val
            assert "9876" not in text_val
            assert "4321" not in text_val
            assert "SEND 5.50" not in text_val
            assert "PIN SET" not in text_val
            assert "PIN CHANGE" not in text_val
