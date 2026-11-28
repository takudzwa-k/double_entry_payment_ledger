from datetime import datetime, timezone
import pytest

from app.commands import HISTORY_USAGE_REPLY, format_date, handle_message
from app.db import get_connection
from app.ledger import UnknownAccount, history
from app.pin import set_pin
from app.seed import seed
from conftest import _create_account, _deposit


def test_format_date_harare_timezone():
    dt1 = datetime(2026, 11, 28, 23, 30, tzinfo=timezone.utc)
    dt2 = datetime(2026, 12, 5, 10, 0, tzinfo=timezone.utc)

    assert format_date(dt1) == "29 Nov"
    assert format_date(dt2) == "5 Dec"


def test_history_new_account_no_activity():
    _create_account("Tendai wallet", "+263771000001")

    assert handle_message("+263771000001", "HISTORY", "h-empty") == "No transactions yet."


def test_history_after_seed_shows_deposit():
    seed(reset=False)

    reply = handle_message("+263771000001", "HISTORY", "h-seed")
    lines = reply.splitlines()

    assert lines[0] == "Last 1 transaction:"
    assert len(lines) == 2
    assert lines[1].endswith(": Deposit 30.00 USD")


def test_history_send_shows_sent_and_received_for_both_accounts():
    seed(reset=False)

    send_reply = handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "h-send-1")
    assert send_reply == "Sent 5.50 USD to +263771000002. New balance: 24.50 USD"

    tendai_lines = handle_message("+263771000001", "HISTORY", "h-tendai-1").splitlines()
    shop_lines = handle_message("+263771000002", "HISTORY", "h-shop-1").splitlines()

    assert tendai_lines[0] == "Last 2 transactions:"
    assert tendai_lines[1].endswith(": Sent 5.50 USD to +263771000002")
    assert tendai_lines[2].endswith(": Deposit 30.00 USD")

    assert shop_lines[0] == "Last 1 transaction:"
    assert shop_lines[1].endswith(": Received 5.50 USD from +263771000001")


def test_history_order_newest_first_after_three_sends():
    seed(reset=False)

    handle_message("+263771000001", "SEND 1.00 TO 0771000002 1234", "h-ord-1")
    handle_message("+263771000001", "SEND 2.50 TO 0771000002 1234", "h-ord-2")
    handle_message("+263771000001", "SEND 3.75 TO 0771000002 1234", "h-ord-3")

    lines = handle_message("+263771000001", "HISTORY", "h-ord-check").splitlines()
    assert lines[0] == "Last 4 transactions:"
    assert lines[1].endswith(": Sent 3.75 USD to +263771000002")
    assert lines[2].endswith(": Sent 2.50 USD to +263771000002")
    assert lines[3].endswith(": Sent 1.00 USD to +263771000002")
    assert lines[4].endswith(": Deposit 30.00 USD")


def test_history_limits_default_5_custom_2_and_10():
    _create_account("Cash source (USD)", "sys-usd", currency="USD", kind="system", allow_negative=True)
    _create_account("Tendai wallet", "+263771000001")
    _create_account("Shop wallet", "+263771000002")
    _deposit("sys-usd", "+263771000001", 5000)
    set_pin("+263771000001", "1234")

    # 1 deposit + 11 sends = 12 transactions
    for i in range(1, 12):
        reply = handle_message("+263771000001", f"SEND 1.{i:02d} TO 0771000002 1234", f"h-lim-s-{i}")
        assert reply.startswith("Sent ")

    lines_2 = handle_message("+263771000001", "HISTORY 2", "h-lim-2").splitlines()
    assert lines_2[0] == "Last 2 transactions:"
    assert len(lines_2) == 3
    assert lines_2[1].endswith(": Sent 1.11 USD to +263771000002")
    assert lines_2[2].endswith(": Sent 1.10 USD to +263771000002")

    lines_default = handle_message("+263771000001", "HISTORY", "h-lim-def").splitlines()
    assert lines_default[0] == "Last 5 transactions:"
    assert len(lines_default) == 6

    lines_10 = handle_message("+263771000001", "HISTORY 10", "h-lim-10").splitlines()
    assert lines_10[0] == "Last 10 transactions:"
    assert len(lines_10) == 11


def test_history_header_singular_vs_plural():
    seed(reset=False)

    single = handle_message("+263771000001", "HISTORY 1", "h-sing").splitlines()
    assert single[0] == "Last 1 transaction:"

    handle_message("+263771000001", "SEND 2.00 TO 0771000002 1234", "h-plur-send")
    plural = handle_message("+263771000001", "HISTORY 2", "h-plur").splitlines()
    assert plural[0] == "Last 2 transactions:"


@pytest.mark.parametrize("bad_cmd", ["HISTORY 0", "HISTORY 11", "HISTORY abc", "HISTORY 1 2"])
def test_history_bad_arguments_get_usage_reply(bad_cmd):
    seed(reset=False)

    assert handle_message("+263771000001", bad_cmd, f"h-bad-{bad_cmd}") == HISTORY_USAGE_REPLY


def test_history_lowercase_works():
    seed(reset=False)
    handle_message("+263771000001", "SEND 2.00 TO 0771000002 1234", "h-low-1")
    handle_message("+263771000001", "SEND 3.00 TO 0771000002 1234", "h-low-2")

    lines = handle_message("+263771000001", "history 3", "h-low-check").splitlines()
    assert lines[0] == "Last 3 transactions:"
    assert len(lines) == 4


def test_history_privacy_excludes_other_accounts_activity():
    seed(reset=False)
    handle_message("+263771000003", "SEND 4.25 TO 0771000002 1234", "h-rudo-send")

    tendai_hist = handle_message("+263771000001", "HISTORY 10", "h-priv-tendai")
    assert "+263771000003" not in tendai_hist
    assert "+263771000004" not in tendai_hist
    assert "15.00" not in tendai_hist
    assert "4.25" not in tendai_hist
    assert "100.00" not in tendai_hist
    assert "ZIG" not in tendai_hist


def test_history_failed_send_does_not_appear():
    seed(reset=False)

    failed = handle_message("+263771000001", "SEND 999.00 TO 0771000002 1234", "h-fail-send")
    assert failed == "Not enough money. Balance: 30.00 USD"

    hist = handle_message("+263771000001", "HISTORY", "h-fail-check")
    assert "999.00" not in hist
    assert hist.splitlines()[0] == "Last 1 transaction:"


def test_history_reversal_shows_signed_amount_for_both_accounts():
    seed(reset=False)

    handle_message("+263771000001", "SEND 5.50 TO 0771000002 1234", "h-rev-orig")

    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM transactions WHERE idempotency_key = %s",
                    ("sms:h-rev-orig",),
                )
                orig_tx_id = int(cur.fetchone()[0])
                cur.execute("SELECT id FROM accounts WHERE handle = %s", ("+263771000001",))
                tendai_id = int(cur.fetchone()[0])
                cur.execute("SELECT id FROM accounts WHERE handle = %s", ("+263771000002",))
                shop_id = int(cur.fetchone()[0])

                cur.execute(
                    """
                    INSERT INTO transactions (kind, description, reverses_id)
                    VALUES ('reversal', 'Reverse test send', %s)
                    RETURNING id
                    """,
                    (orig_tx_id,),
                )
                rev_tx_id = int(cur.fetchone()[0])
                cur.execute(
                    """
                    INSERT INTO entries (transaction_id, account_id, currency, amount)
                    VALUES (%s, %s, 'USD', 550), (%s, %s, 'USD', -550)
                    """,
                    (rev_tx_id, tendai_id, rev_tx_id, shop_id),
                )

    tendai_lines = handle_message("+263771000001", "HISTORY", "h-rev-t").splitlines()
    shop_lines = handle_message("+263771000002", "HISTORY", "h-rev-s").splitlines()

    assert tendai_lines[1].endswith(": Reversal +5.50 USD")
    assert shop_lines[1].endswith(": Reversal -5.50 USD")


def test_history_three_leg_transaction_shows_unknown_counterparty():
    seed(reset=False)

    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM accounts WHERE handle = %s", ("+263771000001",))
                tendai_id = int(cur.fetchone()[0])
                cur.execute("SELECT id FROM accounts WHERE handle = %s", ("+263771000002",))
                shop_id = int(cur.fetchone()[0])
                cur.execute("SELECT id FROM accounts WHERE handle = %s", ("+263771000003",))
                rudo_id = int(cur.fetchone()[0])

                cur.execute(
                    """
                    INSERT INTO transactions (kind, description)
                    VALUES ('transfer', 'Split payment 3 legs')
                    RETURNING id
                    """
                )
                tx_id = int(cur.fetchone()[0])
                cur.execute(
                    """
                    INSERT INTO entries (transaction_id, account_id, currency, amount)
                    VALUES
                        (%s, %s, 'USD', -500),
                        (%s, %s, 'USD', 300),
                        (%s, %s, 'USD', 200)
                    """,
                    (tx_id, tendai_id, tx_id, shop_id, tx_id, rudo_id),
                )

    tendai_lines = handle_message("+263771000001", "HISTORY 1", "h-3leg-t").splitlines()
    shop_lines = handle_message("+263771000002", "HISTORY 1", "h-3leg-s").splitlines()

    assert tendai_lines[1].endswith(": Sent 5.00 USD to unknown")
    assert shop_lines[1].endswith(": Received 3.00 USD from unknown")


def test_history_replayed_same_message_id_returns_original_reply():
    seed(reset=False)

    first = handle_message("+263771000001", "HISTORY", "h-replay-1")
    assert first.splitlines()[0] == "Last 1 transaction:"

    handle_message("+263771000001", "SEND 5.00 TO 0771000002 1234", "h-replay-send")

    replay = handle_message("+263771000001", "HISTORY", "h-replay-1")
    assert replay == first


def test_help_mentions_history():
    reply = handle_message("+263771000001", "HELP", "h-help")
    assert "HISTORY" in reply


def test_ledger_history_validation_and_unknown_account():
    seed(reset=False)

    with pytest.raises(ValueError):
        history("+263771000001", limit=0)

    with pytest.raises(ValueError):
        history("+263771000001", limit=11)

    with pytest.raises(ValueError):
        history("+263771000001", limit=True)

    with pytest.raises(UnknownAccount):
        history("+263779999999")

    assert (
        handle_message("+263779999999", "HISTORY", "h-unk-acct")
        == "No account found for your number."
    )
