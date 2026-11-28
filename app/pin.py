import hashlib
import hmac
import re
import secrets

from app.db import get_connection
from app.ledger import LedgerError, UnknownAccount

PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
MAX_FAILED_ATTEMPTS = 3
LOCKOUT_MINUTES = 15

_PIN_RE = re.compile(r"[0-9]{4}")


class InvalidPinFormat(LedgerError):
    """Raised when a PIN is not exactly 4 digits."""


class NoPinSet(LedgerError):
    """Raised when an account has no PIN configured."""


class PinAlreadySet(LedgerError):
    """Raised when attempting to set a PIN on an account that already has one."""


class WrongPin(LedgerError):
    """Raised when a PIN is incorrect and the account is not yet locked."""

    def __init__(self, attempts_left: int) -> None:
        self.attempts_left = attempts_left
        super().__init__(f"Wrong PIN ({attempts_left} attempts left)")


class AccountLocked(LedgerError):
    """Raised when an account is temporarily locked due to failed PIN attempts."""

    def __init__(self, minutes_left: int) -> None:
        self.minutes_left = minutes_left
        super().__init__(f"Account locked ({minutes_left} minutes left)")


def validate_pin_format(pin: str) -> None:
    if not isinstance(pin, str) or not _PIN_RE.fullmatch(pin):
        raise InvalidPinFormat("PIN must be 4 digits.")


def hash_pin(pin: str) -> str:
    validate_pin_format(pin)
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def check_pin_hash(pin: str, stored_hash: str) -> bool:
    validate_pin_format(pin)
    parts = stored_hash.split("$")
    if len(parts) != 4:
        return False
    algorithm, iter_str, salt_hex, expected_hex = parts
    if algorithm != PBKDF2_ALGORITHM:
        return False
    try:
        iterations = int(iter_str)
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    computed = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(computed.hex(), expected_hex)


def set_pin(handle: str, pin: str) -> None:
    validate_pin_format(pin)
    pin_hash = hash_pin(pin)

    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, pin_hash FROM accounts WHERE handle = %s FOR UPDATE",
                    (handle,),
                )
                row = cur.fetchone()
                if row is None:
                    raise UnknownAccount(f"Unknown account handle: {handle!r}")
                account_id, existing_hash = int(row[0]), row[1]
                if existing_hash is not None:
                    raise PinAlreadySet("PIN is already set for this account.")
                cur.execute(
                    """
                    UPDATE accounts
                    SET pin_hash = %s,
                        pin_failed_attempts = 0,
                        pin_locked_until = NULL
                    WHERE id = %s
                    """,
                    (pin_hash, account_id),
                )


def _check_and_update_pin(handle: str, pin: str, new_pin: str | None = None) -> None:
    validate_pin_format(pin)
    if new_pin is not None:
        validate_pin_format(new_pin)

    error_to_raise: Exception | None = None

    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        pin_hash,
                        pin_failed_attempts,
                        pin_locked_until,
                        (pin_locked_until IS NOT NULL AND pin_locked_until > now()) AS is_locked,
                        CASE
                            WHEN pin_locked_until IS NOT NULL AND pin_locked_until > now()
                            THEN GREATEST(
                                1,
                                CEIL(EXTRACT(EPOCH FROM (pin_locked_until - now())) / 60.0)
                            )::INT
                            ELSE 0
                        END AS minutes_left
                    FROM accounts
                    WHERE handle = %s
                    FOR UPDATE
                    """,
                    (handle,),
                )
                row = cur.fetchone()
                if row is None:
                    raise UnknownAccount(f"Unknown account handle: {handle!r}")

                account_id = int(row[0])
                stored_hash = row[1]
                failed_attempts = int(row[2])
                locked_until = row[3]
                is_locked = bool(row[4])
                minutes_left = int(row[5])

                if stored_hash is None:
                    raise NoPinSet("No PIN set for this account.")

                if is_locked:
                    error_to_raise = AccountLocked(minutes_left)
                elif check_pin_hash(pin, stored_hash):
                    if new_pin is not None:
                        new_hash = hash_pin(new_pin)
                        cur.execute(
                            """
                            UPDATE accounts
                            SET pin_hash = %s,
                                pin_failed_attempts = 0,
                                pin_locked_until = NULL
                            WHERE id = %s
                            """,
                            (new_hash, account_id),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE accounts
                            SET pin_failed_attempts = 0,
                                pin_locked_until = NULL
                            WHERE id = %s
                            """,
                            (account_id,),
                        )
                else:
                    # If a previous lock has expired, start a fresh count.
                    effective_attempts = 0 if locked_until is not None else failed_attempts
                    new_attempts = effective_attempts + 1

                    if new_attempts >= MAX_FAILED_ATTEMPTS:
                        cur.execute(
                            """
                            UPDATE accounts
                            SET pin_failed_attempts = %s,
                                pin_locked_until = now() + (%s * INTERVAL '1 minute')
                            WHERE id = %s
                            """,
                            (MAX_FAILED_ATTEMPTS, LOCKOUT_MINUTES, account_id),
                        )
                        error_to_raise = AccountLocked(LOCKOUT_MINUTES)
                    else:
                        cur.execute(
                            """
                            UPDATE accounts
                            SET pin_failed_attempts = %s,
                                pin_locked_until = NULL
                            WHERE id = %s
                            """,
                            (new_attempts, account_id),
                        )
                        error_to_raise = WrongPin(MAX_FAILED_ATTEMPTS - new_attempts)

    if error_to_raise is not None:
        raise error_to_raise


def verify_pin(handle: str, pin: str) -> None:
    _check_and_update_pin(handle, pin, new_pin=None)


def change_pin(handle: str, old_pin: str, new_pin: str) -> None:
    _check_and_update_pin(handle, old_pin, new_pin=new_pin)
