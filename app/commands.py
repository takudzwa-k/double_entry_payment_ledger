from datetime import datetime, timezone
import logging
import re
import time
from zoneinfo import ZoneInfo

from app import ledger, pin
from app.db import get_connection
from app.ledger import (
    CurrencyMismatch,
    IdempotencyConflict,
    InsufficientFunds,
    InvalidAmount,
    SameAccountTransfer,
    UnknownAccount,
)
from app.pin import (
    AccountLocked,
    InvalidPinFormat,
    NoPinSet,
    PinAlreadySet,
    WrongPin,
)

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Commands:\n"
    "BAL - check your balance\n"
    "SEND <amount> TO <phone> <PIN> - send money (e.g. SEND 5.50 TO 0771000002 1234)\n"
    "HISTORY [n] - last transactions\n"
    "PIN SET <4 digits> - set your PIN\n"
    "PIN CHANGE <old> <new> - change your PIN\n"
    "HELP - show this message"
)

INVALID_AMOUNT_REPLY = "Invalid amount. Example: SEND 5.50 TO 0771000002 1234"
MISSING_PIN_REPLY = "Please include your PIN. Example: SEND 5.50 TO 0771000002 1234"
HISTORY_USAGE_REPLY = "Use: HISTORY or HISTORY 3 (1 to 10)"
UNKNOWN_COMMAND_REPLY = "Unknown command. Reply HELP."
DUPLICATE_MESSAGE_CONFLICT_REPLY = "This message was already used. Please try again."
STILL_PROCESSING_REPLY = "Your message is being processed. Please try again shortly."
GENERIC_ERROR_REPLY = "Something went wrong. Please try again."

POLL_INTERVAL_SECONDS = 0.1
PROCESSING_WAIT_LIMIT_SECONDS = 5.0
STALE_PROCESSING_SECONDS = 120

HARARE_TZ = ZoneInfo("Africa/Harare")
_MONTH_ABBR = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def parse_amount_cents(text: str) -> int:
    if not isinstance(text, str):
        raise InvalidAmount("Amount must be a string.")

    cleaned = text.strip()
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", cleaned):
        raise InvalidAmount("Invalid amount format.")

    if "." in cleaned:
        whole_part, frac_part = cleaned.split(".", 1)
        frac_part = frac_part.ljust(2, "0")
    else:
        whole_part, frac_part = cleaned, "00"

    cents = int(whole_part) * 100 + int(frac_part)
    if cents <= 0:
        raise InvalidAmount("Amount must be greater than zero.")

    return cents


def format_cents(amount_cents: int) -> str:
    sign = "-" if amount_cents < 0 else ""
    whole, frac = divmod(abs(amount_cents), 100)
    return f"{sign}{whole}.{frac:02d}"


def format_date(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone(HARARE_TZ)
    return f"{local_dt.day} {_MONTH_ABBR[local_dt.month]}"


def _format_history_line(item: ledger.HistoryEntry) -> str:
    date_str = format_date(item.created_at)
    abs_amount = format_cents(abs(item.amount_cents))
    cur = item.currency
    counterparty = item.counterparty if item.counterparty else "unknown"

    if item.kind == "transfer":
        if item.amount_cents < 0:
            return f"{date_str}: Sent {abs_amount} {cur} to {counterparty}"
        return f"{date_str}: Received {abs_amount} {cur} from {counterparty}"

    if item.kind == "deposit":
        if item.amount_cents > 0:
            return f"{date_str}: Deposit {abs_amount} {cur}"
        return f"{date_str}: Deposit -{abs_amount} {cur}"

    if item.kind == "reversal":
        sign = "+" if item.amount_cents > 0 else "-"
        return f"{date_str}: Reversal {sign}{abs_amount} {cur}"

    return f"{date_str}: {item.kind} {format_cents(item.amount_cents)} {cur}"


def normalize_handle(handle: str) -> str:
    cleaned = re.sub(r"[\s-]+", "", handle)
    if re.fullmatch(r"0[0-9]{9}", cleaned):
        return "+263" + cleaned[1:]
    if re.fullmatch(r"263[0-9]{9}", cleaned):
        return "+" + cleaned
    return cleaned


def _format_wrong_pin(attempts_left: int) -> str:
    word = "attempt" if attempts_left == 1 else "attempts"
    return f"Wrong PIN. {attempts_left} {word} left."


def _format_locked(minutes_left: int) -> str:
    word = "minute" if minutes_left == 1 else "minutes"
    return f"Account locked. Try again in {minutes_left} {word}."


def _claim_message(message_id: str, sender: str) -> bool:
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO inbound_messages (message_id, sender, status)
                    VALUES (%s, %s, 'processing')
                    ON CONFLICT (message_id) DO NOTHING
                    RETURNING message_id
                    """,
                    (message_id, sender),
                )
                return cur.fetchone() is not None


def _load_message(message_id: str) -> tuple[str, str, str | None, bool] | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    sender,
                    status,
                    reply,
                    (
                        status = 'processing'
                        AND created_at < now() - (%s * INTERVAL '1 second')
                    ) AS is_stale
                FROM inbound_messages
                WHERE message_id = %s
                """,
                (STALE_PROCESSING_SECONDS, message_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return str(row[0]), str(row[1]), row[2], bool(row[3])


def _try_takeover_stale(message_id: str, sender: str) -> bool:
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE inbound_messages
                    SET created_at = now()
                    WHERE message_id = %s
                      AND sender = %s
                      AND status = 'processing'
                      AND created_at < now() - (%s * INTERVAL '1 second')
                    RETURNING message_id
                    """,
                    (message_id, sender, STALE_PROCESSING_SECONDS),
                )
                return cur.fetchone() is not None


def _complete_message(message_id: str, reply: str) -> None:
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE inbound_messages
                    SET status = 'done',
                        reply = %s,
                        completed_at = now()
                    WHERE message_id = %s
                    """,
                    (reply, message_id),
                )


def _delete_processing_message(message_id: str) -> None:
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM inbound_messages
                    WHERE message_id = %s AND status = 'processing'
                    """,
                    (message_id,),
                )


def _process_message(sender: str, text: str, message_id: str) -> str:
    parts = text.strip().split()
    if not parts:
        return UNKNOWN_COMMAND_REPLY

    cmd = parts[0].upper()

    if cmd == "HELP" and len(parts) == 1:
        return HELP_TEXT

    if cmd == "BAL" and len(parts) == 1:
        try:
            cur_code = ledger.currency(sender)
            bal_cents = ledger.balance(sender)
        except UnknownAccount:
            return "No account found for your number."
        return f"Balance: {format_cents(bal_cents)} {cur_code}"

    if cmd == "HISTORY":
        if len(parts) == 1:
            limit = 5
        elif len(parts) == 2 and re.fullmatch(r"[0-9]+", parts[1]):
            limit = int(parts[1])
            if limit < 1 or limit > 10:
                return HISTORY_USAGE_REPLY
        else:
            return HISTORY_USAGE_REPLY

        try:
            items = ledger.history(sender, limit=limit)
        except UnknownAccount:
            return "No account found for your number."

        if not items:
            return "No transactions yet."

        count = len(items)
        noun = "transaction" if count == 1 else "transactions"
        lines = [f"Last {count} {noun}:"]
        lines.extend(_format_history_line(item) for item in items)
        return "\n".join(lines)

    if cmd == "PIN" and len(parts) >= 2:
        subcmd = parts[1].upper()
        if subcmd == "SET" and len(parts) == 3:
            try:
                pin.set_pin(sender, parts[2])
            except InvalidPinFormat:
                return "PIN must be 4 digits."
            except UnknownAccount:
                return "No account found for your number."
            except PinAlreadySet:
                return "You already have a PIN. Use: PIN CHANGE <old> <new>"
            return "PIN set."

        if subcmd == "CHANGE" and len(parts) == 4:
            try:
                pin.change_pin(sender, parts[2], parts[3])
            except InvalidPinFormat:
                return "PIN must be 4 digits."
            except UnknownAccount:
                return "No account found for your number."
            except NoPinSet:
                return "Set a PIN first. Reply: PIN SET 1234"
            except WrongPin as exc:
                return _format_wrong_pin(exc.attempts_left)
            except AccountLocked as exc:
                return _format_locked(exc.minutes_left)
            return "PIN changed."

        return UNKNOWN_COMMAND_REPLY

    if cmd == "SEND" and len(parts) == 4 and parts[2].upper() == "TO":
        return MISSING_PIN_REPLY

    if cmd == "SEND" and len(parts) == 5 and parts[2].upper() == "TO":
        raw_amount = parts[1]
        raw_recipient = parts[3]
        raw_pin = parts[4]

        try:
            amount_cents = parse_amount_cents(raw_amount)
        except InvalidAmount:
            return INVALID_AMOUNT_REPLY

        recipient = normalize_handle(raw_recipient)

        # Verify PIN in its own committed transaction before starting any transfer.
        try:
            pin.verify_pin(sender, raw_pin)
        except InvalidPinFormat:
            return "PIN must be 4 digits."
        except UnknownAccount:
            return "No account found for your number."
        except NoPinSet:
            return "Set a PIN first. Reply: PIN SET 1234"
        except WrongPin as exc:
            return _format_wrong_pin(exc.attempts_left)
        except AccountLocked as exc:
            return _format_locked(exc.minutes_left)

        cur_code = ledger.currency(sender)

        try:
            ledger.transfer(sender, recipient, amount_cents, f"sms:{message_id}")
        except UnknownAccount:
            return "Recipient not found."
        except InsufficientFunds:
            bal_cents = ledger.balance(sender)
            return f"Not enough money. Balance: {format_cents(bal_cents)} {cur_code}"
        except CurrencyMismatch:
            return "You can't send between different currencies."
        except SameAccountTransfer:
            return "You can't send money to yourself."
        except InvalidAmount:
            return INVALID_AMOUNT_REPLY
        except IdempotencyConflict:
            return DUPLICATE_MESSAGE_CONFLICT_REPLY

        new_bal_cents = ledger.balance(sender)
        return (
            f"Sent {format_cents(amount_cents)} {cur_code} to {recipient}. "
            f"New balance: {format_cents(new_bal_cents)} {cur_code}"
        )

    return UNKNOWN_COMMAND_REPLY


def _run_claimed_message(sender: str, text: str, message_id: str) -> str:
    try:
        reply = _process_message(sender, text, message_id)
    except Exception:
        try:
            _delete_processing_message(message_id)
        except Exception as cleanup_exc:
            logger.error(
                "Failed to delete processing row for message %s: %s",
                message_id,
                type(cleanup_exc).__name__,
            )
        raise

    _complete_message(message_id, reply)
    return reply


def handle_message(sender_handle: str, text: str, message_id: str) -> str:
    try:
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("Invalid message_id")

        sender = normalize_handle(sender_handle)

        if _claim_message(message_id, sender):
            return _run_claimed_message(sender, text, message_id)

        deadline = time.monotonic() + PROCESSING_WAIT_LIMIT_SECONDS
        while True:
            record = _load_message(message_id)
            if record is None:
                if _claim_message(message_id, sender):
                    return _run_claimed_message(sender, text, message_id)
            else:
                stored_sender, status, stored_reply, is_stale = record
                if stored_sender != sender:
                    return DUPLICATE_MESSAGE_CONFLICT_REPLY
                if status == "done" and stored_reply is not None:
                    return stored_reply
                if is_stale and _try_takeover_stale(message_id, sender):
                    return _run_claimed_message(sender, text, message_id)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return STILL_PROCESSING_REPLY
            time.sleep(min(POLL_INTERVAL_SECONDS, remaining))
    except Exception as exc:
        logger.error(
            "Unexpected error handling message: %s",
            type(exc).__name__,
        )
        return GENERIC_ERROR_REPLY
