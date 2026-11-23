import pytest

from app.ledger import (
    CurrencyMismatch,
    IdempotencyConflict,
    InsufficientFunds,
    InvalidAmount,
    SameAccountTransfer,
    UnknownAccount,
    balance,
    currency,
    transfer,
)
from conftest import _create_account, _deposit


def test_new_account_balance_is_zero():
    _create_account("Tendai wallet", "+263771000001")
    assert balance("+263771000001") == 0


def test_valid_transfer_moves_money_and_balances_sum_to_zero():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")
    _deposit("sys-usd", "+263771000001", 5000)

    result = transfer("+263771000001", "+263771000002", 2000, "tx-valid-1")

    assert result.duplicate is False
    assert isinstance(result.transaction_id, int)
    assert balance("sys-usd") == -5000
    assert balance("+263771000001") == 3000
    assert balance("+263771000002") == 2000
    assert balance("sys-usd") + balance("+263771000001") + balance("+263771000002") == 0


def test_transfer_insufficient_funds():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")
    _deposit("sys-usd", "+263771000001", 1000)

    with pytest.raises(InsufficientFunds):
        transfer("+263771000001", "+263771000002", 2500, "tx-overdraft-1")

    assert balance("+263771000001") == 1000
    assert balance("+263771000002") == 0


def test_transfer_duplicate_key_moves_money_only_once():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")
    _deposit("sys-usd", "+263771000001", 3000)

    first = transfer("+263771000001", "+263771000002", 2000, "tx-idem-1")
    second = transfer("+263771000001", "+263771000002", 2000, "tx-idem-1")

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.transaction_id == first.transaction_id
    assert balance("+263771000001") == 1000
    assert balance("+263771000002") == 2000


def test_unknown_handle_raises_unknown_account():
    _create_account("Tendai wallet", "+263771000001")

    with pytest.raises(UnknownAccount):
        balance("+263779999999")

    with pytest.raises(UnknownAccount):
        currency("+263779999999")

    with pytest.raises(UnknownAccount):
        transfer("+263779999999", "+263771000001", 500, "tx-unk-from")

    with pytest.raises(UnknownAccount):
        transfer("+263771000001", "+263779999999", 500, "tx-unk-to")


@pytest.mark.parametrize("bad_amount", [0, -100, 10.5, 10.0, "100", True, None])
def test_invalid_amount_raises_invalid_amount(bad_amount):
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")

    with pytest.raises(InvalidAmount):
        transfer("+263771000001", "+263771000002", bad_amount, f"tx-bad-{bad_amount}")


def test_transfer_to_self_is_rejected():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _deposit("sys-usd", "+263771000001", 2000)

    with pytest.raises(SameAccountTransfer):
        transfer("+263771000001", "+263771000001", 500, "tx-self-1")

    assert balance("+263771000001") == 2000


def test_currency_mismatch_raises_currency_mismatch():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai USD wallet", "+263771000001", currency="USD")
    _create_account("Shop ZIG wallet", "+263771000002", currency="ZIG")
    _deposit("sys-usd", "+263771000001", 2000, currency="USD")

    with pytest.raises(CurrencyMismatch):
        transfer("+263771000001", "+263771000002", 500, "tx-curr-1")

    assert balance("+263771000001") == 2000
    assert balance("+263771000002") == 0


def test_idempotency_conflict_different_amount_leaves_balances_unchanged():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")
    _deposit("sys-usd", "+263771000001", 5000)

    first = transfer("+263771000001", "+263771000002", 2000, "tx-conflict-amt")
    assert first.duplicate is False

    with pytest.raises(IdempotencyConflict):
        transfer("+263771000001", "+263771000002", 1500, "tx-conflict-amt")

    assert balance("sys-usd") == -5000
    assert balance("+263771000001") == 3000
    assert balance("+263771000002") == 2000


def test_idempotency_conflict_different_accounts_leaves_balances_unchanged():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop A wallet", "+263771000002")
    _create_account("Shop B wallet", "+263771000003")
    _deposit("sys-usd", "+263771000001", 5000)

    first = transfer("+263771000001", "+263771000002", 2000, "tx-conflict-acc")
    assert first.duplicate is False

    # Same key, different recipient
    with pytest.raises(IdempotencyConflict):
        transfer("+263771000001", "+263771000003", 2000, "tx-conflict-acc")

    # Same key, reversed direction (different from/to)
    with pytest.raises(IdempotencyConflict):
        transfer("+263771000002", "+263771000001", 2000, "tx-conflict-acc")

    assert balance("sys-usd") == -5000
    assert balance("+263771000001") == 3000
    assert balance("+263771000002") == 2000
    assert balance("+263771000003") == 0
