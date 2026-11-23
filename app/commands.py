import logging
import re

from app import ledger
from app.ledger import (
    CurrencyMismatch,
    IdempotencyConflict,
    InsufficientFunds,
    InvalidAmount,
    SameAccountTransfer,
    UnknownAccount,
)

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Commands:\n"
    "BAL - check your balance\n"
    "SEND <amount> TO <phone> - send money (e.g. SEND 5.50 TO 0771000002)\n"
    "HELP - show this message"
)

INVALID_AMOUNT_REPLY = "Invalid amount. Example: SEND 5.50 TO 0771000002"
UNKNOWN_COMMAND_REPLY = "Unknown command. Reply HELP."


def parse_amount_cents(text: str) -> int:
    if not isinstance(text, str):
        raise InvalidAmount(f"Amount must be a string, got {text!r}")

    cleaned = text.strip()
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", cleaned):
        raise InvalidAmount(f"Invalid amount format: {text!r}")

    if "." in cleaned:
        whole_part, frac_part = cleaned.split(".", 1)
        frac_part = frac_part.ljust(2, "0")
    else:
        whole_part, frac_part = cleaned, "00"

    cents = int(whole_part) * 100 + int(frac_part)
    if cents <= 0:
        raise InvalidAmount(f"Amount must be greater than zero: {text!r}")

    return cents


def format_cents(amount_cents: int) -> str:
    sign = "-" if amount_cents < 0 else ""
    whole, frac = divmod(abs(amount_cents), 100)
    return f"{sign}{whole}.{frac:02d}"


def normalize_handle(handle: str) -> str:
    cleaned = re.sub(r"[\s-]+", "", handle)
    if re.fullmatch(r"0[0-9]{9}", cleaned):
        return "+263" + cleaned[1:]
    if re.fullmatch(r"263[0-9]{9}", cleaned):
        return "+" + cleaned
    return cleaned


def handle_message(sender_handle: str, text: str, message_id: str) -> str:
    try:
        sender = normalize_handle(sender_handle)
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

        if cmd == "SEND" and len(parts) >= 4 and parts[2].upper() == "TO":
            raw_amount = parts[1]
            raw_recipient = "".join(parts[3:])

            try:
                amount_cents = parse_amount_cents(raw_amount)
            except InvalidAmount:
                return INVALID_AMOUNT_REPLY

            recipient = normalize_handle(raw_recipient)

            try:
                cur_code = ledger.currency(sender)
            except UnknownAccount:
                return "No account found for your number."

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
                return "This message was already used. Please try again."

            new_bal_cents = ledger.balance(sender)
            return (
                f"Sent {format_cents(amount_cents)} {cur_code} to {recipient}. "
                f"New balance: {format_cents(new_bal_cents)} {cur_code}"
            )

        return UNKNOWN_COMMAND_REPLY
    except Exception:
        logger.exception(
            "Unexpected error handling message %r from %r", message_id, sender_handle
        )
        return "Something went wrong. Please try again."
