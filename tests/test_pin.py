from concurrent.futures import ThreadPoolExecutor
import re
import threading
import pytest

from app.db import get_connection
from app.pin import (
    AccountLocked,
    InvalidPinFormat,
    NoPinSet,
    PinAlreadySet,
    WrongPin,
    change_pin,
    check_pin_hash,
    hash_pin,
    set_pin,
    verify_pin,
)
from conftest import _create_account


# --- Hashing ---

def test_hash_pin_uses_random_salt_and_both_verify():
    h1 = hash_pin("1234")
    h2 = hash_pin("1234")

    assert h1 != h2
    assert check_pin_hash("1234", h1) is True
    assert check_pin_hash("1234", h2) is True


def test_hash_pin_wrong_pin_does_not_verify_and_format_matches():
    stored = hash_pin("1234")

    assert re.fullmatch(r"pbkdf2_sha256\$600000\$[0-9a-f]{32}\$[0-9a-f]{64}", stored)
    assert check_pin_hash("9999", stored) is False


# --- PIN rules ---

def test_set_pin_works_once_then_raises_pin_already_set():
    _create_account("Tendai wallet", "+263771000001")

    set_pin("+263771000001", "1234")
    verify_pin("+263771000001", "1234")

    with pytest.raises(PinAlreadySet):
        set_pin("+263771000001", "5678")


@pytest.mark.parametrize("bad_pin", ["123", "12345", "abcd", "12 34", ""])
def test_invalid_pin_formats_rejected(bad_pin):
    _create_account("Tendai wallet", "+263771000001")

    with pytest.raises(InvalidPinFormat):
        set_pin("+263771000001", bad_pin)

    set_pin("+263771000001", "1234")

    with pytest.raises(InvalidPinFormat):
        verify_pin("+263771000001", bad_pin)

    with pytest.raises(InvalidPinFormat):
        change_pin("+263771000001", "1234", bad_pin)


def test_change_pin_works_and_wrong_old_pin_counts_as_failed_attempt():
    _create_account("Tendai wallet", "+263771000001")
    set_pin("+263771000001", "1234")

    with pytest.raises(WrongPin) as exc_info:
        change_pin("+263771000001", "0000", "5678")
    assert exc_info.value.attempts_left == 2

    change_pin("+263771000001", "1234", "5678")
    verify_pin("+263771000001", "5678")

    with pytest.raises(WrongPin) as exc_info2:
        verify_pin("+263771000001", "1234")
    assert exc_info2.value.attempts_left == 2


def test_wrong_pin_decrements_attempts_then_locks():
    _create_account("Tendai wallet", "+263771000001")
    set_pin("+263771000001", "1234")

    with pytest.raises(WrongPin) as exc1:
        verify_pin("+263771000001", "0000")
    assert exc1.value.attempts_left == 2

    with pytest.raises(WrongPin) as exc2:
        verify_pin("+263771000001", "0000")
    assert exc2.value.attempts_left == 1

    with pytest.raises(AccountLocked) as exc3:
        verify_pin("+263771000001", "0000")
    assert 1 <= exc3.value.minutes_left <= 15


def test_three_wrong_locks_and_correct_pin_rejected_while_locked():
    _create_account("Tendai wallet", "+263771000001")
    set_pin("+263771000001", "1234")

    for _ in range(2):
        with pytest.raises(WrongPin):
            verify_pin("+263771000001", "9999")
    with pytest.raises(AccountLocked):
        verify_pin("+263771000001", "9999")

    # Even the correct PIN is rejected while locked, and counter stays at 3.
    with pytest.raises(AccountLocked):
        verify_pin("+263771000001", "1234")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pin_failed_attempts, pin_locked_until > now() FROM accounts WHERE handle = %s",
                ("+263771000001",),
            )
            attempts, is_locked = cur.fetchone()
    assert attempts == 3
    assert is_locked is True


def test_lock_expires_then_correct_pin_works():
    _create_account("Tendai wallet", "+263771000001")
    set_pin("+263771000001", "1234")

    for _ in range(2):
        with pytest.raises(WrongPin):
            verify_pin("+263771000001", "9999")
    with pytest.raises(AccountLocked):
        verify_pin("+263771000001", "9999")

    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE accounts SET pin_locked_until = now() - INTERVAL '1 minute' WHERE handle = %s",
                    ("+263771000001",),
                )

    verify_pin("+263771000001", "1234")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pin_failed_attempts, pin_locked_until FROM accounts WHERE handle = %s",
                ("+263771000001",),
            )
            attempts, locked_until = cur.fetchone()
    assert attempts == 0
    assert locked_until is None


def test_correct_pin_resets_failed_counter():
    _create_account("Tendai wallet", "+263771000001")
    set_pin("+263771000001", "1234")

    with pytest.raises(WrongPin) as exc1:
        verify_pin("+263771000001", "0000")
    assert exc1.value.attempts_left == 2

    with pytest.raises(WrongPin) as exc2:
        verify_pin("+263771000001", "0000")
    assert exc2.value.attempts_left == 1

    verify_pin("+263771000001", "1234")

    with pytest.raises(WrongPin) as exc3:
        verify_pin("+263771000001", "0000")
    assert exc3.value.attempts_left == 2


def test_no_pin_set_raises_no_pin_set():
    _create_account("Tendai wallet", "+263771000001")

    with pytest.raises(NoPinSet):
        verify_pin("+263771000001", "1234")


# --- Parallel guesses ---

def test_parallel_wrong_guesses_lock_at_three():
    _create_account("Tendai wallet", "+263771000001")
    set_pin("+263771000001", "1234")

    workers = 10
    barrier = threading.Barrier(workers)

    def attempt_wrong_pin(_idx: int) -> type[Exception]:
        barrier.wait()
        try:
            verify_pin("+263771000001", "9999")
        except (WrongPin, AccountLocked) as exc:
            return type(exc)
        raise AssertionError("Expected WrongPin or AccountLocked")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(attempt_wrong_pin, range(workers)))

    wrong_count = sum(1 for r in results if r is WrongPin)
    locked_count = sum(1 for r in results if r is AccountLocked)

    assert wrong_count <= 3
    assert wrong_count + locked_count == workers
    assert locked_count >= 7

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pin_failed_attempts, pin_locked_until > now() FROM accounts WHERE handle = %s",
                ("+263771000001",),
            )
            attempts, is_locked = cur.fetchone()

    assert attempts == 3
    assert is_locked is True
