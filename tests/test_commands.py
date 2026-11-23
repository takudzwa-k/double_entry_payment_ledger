import pytest

from app import ledger
from app.commands import (
    HELP_TEXT,
    INVALID_AMOUNT_REPLY,
    UNKNOWN_COMMAND_REPLY,
    format_cents,
    handle_message,
    normalize_handle,
    parse_amount_cents,
)
from app.ledger import InvalidAmount, balance
from conftest import _create_account, _deposit


# --- Money parsing and formatting ---

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5", 500),
        ("5.5", 550),
        ("5.50", 550),
        ("0.05", 5),
        ("1000", 100000),
    ],
)
def test_parse_amount_cents_valid(raw, expected):
    assert parse_amount_cents(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "0", "0.00", "-5", "5.555", "abc", "1e3", "5,50", ".5", "5."],
)
def test_parse_amount_cents_invalid(raw):
    with pytest.raises(InvalidAmount):
        parse_amount_cents(raw)


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (550, "5.50"),
        (3000, "30.00"),
        (5, "0.05"),
        (0, "0.00"),
    ],
)
def test_format_cents(cents, expected):
    assert format_cents(cents) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0771000002", "+263771000002"),
        ("077 100 0002", "+263771000002"),
        ("077-100-0002", "+263771000002"),
        ("263771000002", "+263771000002"),
        ("+263771000002", "+263771000002"),
        ("+263 771-000-002", "+263771000002"),
    ],
)
def test_normalize_handle(raw, expected):
    assert normalize_handle(raw) == expected


# --- Command parsing ---

@pytest.mark.parametrize("text", ["BAL", "bal", "  Bal  "])
def test_command_parsing_bal_variants(text):
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _deposit("sys-usd", "+263771000001", 3000)

    assert handle_message("+263771000001", text, "m-bal") == "Balance: 30.00 USD"


def test_command_parsing_help():
    assert handle_message("+263771000001", "HELP", "m-help") == HELP_TEXT
    assert handle_message("+263771000001", "  help ", "m-help-2") == HELP_TEXT


@pytest.mark.parametrize("text", ["", "   ", "HELLO", "SEND 5", "SEND TO x", "BAL extra"])
def test_command_parsing_unknown(text):
    assert handle_message("+263771000001", text, "m-unk") == UNKNOWN_COMMAND_REPLY


def test_command_parsing_lowercase_send():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "x")
    _deposit("sys-usd", "+263771000001", 1000)

    reply = handle_message("+263771000001", "send 5 to x", "m-lower")
    assert reply == "Sent 5.00 USD to x. New balance: 5.00 USD"


# --- End-to-end command handling ---

def test_e2e_bal_reply():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _deposit("sys-usd", "+263771000001", 3000)

    assert handle_message("+263771000001", "BAL", "m-1") == "Balance: 30.00 USD"


def test_e2e_successful_send():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")
    _deposit("sys-usd", "+263771000001", 3000)

    reply = handle_message("+263771000001", "SEND 5.50 TO +263771000002", "m-2")

    assert reply == "Sent 5.50 USD to +263771000002. New balance: 24.50 USD"
    assert balance("+263771000001") == 2450
    assert balance("+263771000002") == 550


def test_e2e_replayed_message_id_moves_money_once():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")
    _deposit("sys-usd", "+263771000001", 3000)

    first = handle_message("+263771000001", "SEND 5.50 TO +263771000002", "m-dup-1")
    second = handle_message("+263771000001", "SEND 5.50 TO +263771000002", "m-dup-1")

    assert first == "Sent 5.50 USD to +263771000002. New balance: 24.50 USD"
    assert second == first
    assert balance("+263771000001") == 2450
    assert balance("+263771000002") == 550


def test_e2e_idempotency_conflict_reply():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")
    _deposit("sys-usd", "+263771000001", 3000)

    handle_message("+263771000001", "SEND 5.50 TO +263771000002", "m-conf-1")
    conflict = handle_message("+263771000001", "SEND 10.00 TO +263771000002", "m-conf-1")

    assert conflict == "This message was already used. Please try again."
    assert balance("+263771000001") == 2450
    assert balance("+263771000002") == 550


def test_e2e_insufficient_funds():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")
    _deposit("sys-usd", "+263771000001", 1000)

    reply = handle_message("+263771000001", "SEND 25.00 TO +263771000002", "m-insuff")

    assert reply == "Not enough money. Balance: 10.00 USD"
    assert balance("+263771000001") == 1000
    assert balance("+263771000002") == 0


def test_e2e_unknown_recipient():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _deposit("sys-usd", "+263771000001", 1000)

    reply = handle_message("+263771000001", "SEND 5.00 TO 0779999999", "m-no-recip")

    assert reply == "Recipient not found."
    assert balance("+263771000001") == 1000


def test_e2e_unknown_sender():
    _create_account("Shop wallet", "+263771000002")

    assert handle_message("+263779999999", "BAL", "m-no-sender-1") == "No account found for your number."
    assert (
        handle_message("+263779999999", "SEND 5.00 TO +263771000002", "m-no-sender-2")
        == "No account found for your number."
    )


def test_e2e_send_to_yourself():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _deposit("sys-usd", "+263771000001", 1000)

    reply = handle_message("+263771000001", "SEND 5.00 TO 0771000001", "m-self")

    assert reply == "You can't send money to yourself."
    assert balance("+263771000001") == 1000


def test_e2e_currency_mismatch():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai USD", "+263771000001", currency="USD")
    _create_account("Shop ZIG", "+263771000002", currency="ZIG")
    _deposit("sys-usd", "+263771000001", 1000, currency="USD")

    reply = handle_message("+263771000001", "SEND 5.00 TO +263771000002", "m-curr")

    assert reply == "You can't send between different currencies."
    assert balance("+263771000001") == 1000
    assert balance("+263771000002") == 0


def test_e2e_local_number_normalization():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")
    _deposit("sys-usd", "+263771000001", 3000)

    reply = handle_message("0771000001", "SEND 5.50 TO 0771000002", "m-local")

    assert reply == "Sent 5.50 USD to +263771000002. New balance: 24.50 USD"
    assert balance("+263771000001") == 2450
    assert balance("+263771000002") == 550


def test_e2e_invalid_amount_reply():
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")

    assert handle_message("+263771000001", "SEND abc TO 0771000002", "m-bad-amt") == INVALID_AMOUNT_REPLY


def test_e2e_unexpected_error_returns_generic_reply(monkeypatch, caplog):
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated database outage")

    monkeypatch.setattr(ledger, "transfer", boom)

    with caplog.at_level("ERROR"):
        reply = handle_message("+263771000001", "SEND 5.00 TO +263771000002", "m-boom")

    assert reply == "Something went wrong. Please try again."
    assert "simulated database outage" in caplog.text
