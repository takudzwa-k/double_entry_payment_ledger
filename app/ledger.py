from dataclasses import dataclass
from datetime import datetime
import psycopg

from app.db import get_connection


class LedgerError(Exception):
    """Base error for ledger operations."""


class UnknownAccount(LedgerError):
    """Raised when an account handle does not exist."""


class InsufficientFunds(LedgerError):
    """Raised when a transfer would overdraw an account."""


class CurrencyMismatch(LedgerError):
    """Raised when accounts involved in a transfer use different currencies."""


class InvalidAmount(LedgerError):
    """Raised when an amount is not a positive integer number of cents."""


class SameAccountTransfer(LedgerError, ValueError):
    """Raised when attempting to transfer from an account to itself."""


class IdempotencyConflict(LedgerError):
    """Raised when an idempotency key is reused with different transfer details."""


@dataclass(frozen=True)
class TransferResult:
    transaction_id: int
    duplicate: bool


@dataclass(frozen=True)
class HistoryEntry:
    transaction_id: int
    kind: str
    created_at: datetime
    amount_cents: int
    currency: str
    counterparty: str | None


def balance(handle: str) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.id, COALESCE(SUM(e.amount), 0)::BIGINT
                FROM accounts a
                LEFT JOIN entries e ON e.account_id = a.id
                WHERE a.handle = %s
                GROUP BY a.id
                """,
                (handle,),
            )
            row = cur.fetchone()
            if row is None:
                raise UnknownAccount(f"Unknown account handle: {handle!r}")
            return int(row[1])


def currency(handle: str) -> str:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT currency FROM accounts WHERE handle = %s",
                (handle,),
            )
            row = cur.fetchone()
            if row is None:
                raise UnknownAccount(f"Unknown account handle: {handle!r}")
            return str(row[0]).strip()


def history(handle: str, limit: int = 5) -> list[HistoryEntry]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 10:
        raise ValueError(f"limit must be an integer between 1 and 10, got {limit!r}")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH target AS (
                    SELECT id
                    FROM accounts
                    WHERE handle = %s
                ),
                acct_txs AS (
                    SELECT
                        t.id AS transaction_id,
                        t.kind,
                        t.created_at,
                        e.currency,
                        SUM(e.amount)::BIGINT AS amount_cents,
                        COUNT(*) AS self_entries
                    FROM target
                    JOIN entries e ON e.account_id = target.id
                    JOIN transactions t ON t.id = e.transaction_id
                    GROUP BY t.id, t.kind, t.created_at, e.currency
                    ORDER BY t.created_at DESC, t.id DESC
                    LIMIT %s
                ),
                other_parties AS (
                    SELECT
                        e.transaction_id,
                        COUNT(*) AS other_entries,
                        COUNT(DISTINCT e.account_id) AS other_accounts,
                        MIN(a.handle) AS only_handle
                    FROM acct_txs atx
                    JOIN entries e ON e.transaction_id = atx.transaction_id
                    JOIN accounts a ON a.id = e.account_id
                    CROSS JOIN target
                    WHERE e.account_id <> target.id
                    GROUP BY e.transaction_id
                )
                SELECT
                    target.id AS target_id,
                    atx.transaction_id,
                    atx.kind,
                    atx.created_at,
                    atx.amount_cents,
                    atx.currency,
                    CASE
                        WHEN atx.self_entries = 1
                         AND op.other_entries = 1
                         AND op.other_accounts = 1
                        THEN op.only_handle
                        ELSE NULL
                    END AS counterparty
                FROM target
                LEFT JOIN acct_txs atx ON TRUE
                LEFT JOIN other_parties op ON op.transaction_id = atx.transaction_id
                ORDER BY atx.created_at DESC NULLS LAST, atx.transaction_id DESC NULLS LAST
                """,
                (handle, limit),
            )
            rows = cur.fetchall()

    if not rows:
        raise UnknownAccount(f"Unknown account handle: {handle!r}")

    entries: list[HistoryEntry] = []
    for _, tx_id, kind, created_at, amount_cents, curr, counterparty in rows:
        if tx_id is None:
            continue
        entries.append(
            HistoryEntry(
                transaction_id=int(tx_id),
                kind=str(kind),
                created_at=created_at,
                amount_cents=int(amount_cents),
                currency=str(curr).strip(),
                counterparty=counterparty,
            )
        )
    return entries


def transfer(
    from_handle: str,
    to_handle: str,
    amount_cents: int,
    key: str,
) -> TransferResult:
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int) or amount_cents <= 0:
        raise InvalidAmount(f"amount_cents must be a positive integer, got {amount_cents!r}")

    if from_handle == to_handle:
        raise SameAccountTransfer(f"Cannot transfer to the same account: {from_handle!r}")

    with get_connection() as conn:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, currency FROM accounts WHERE handle = %s",
                        (from_handle,),
                    )
                    from_row = cur.fetchone()
                    if from_row is None:
                        raise UnknownAccount(f"Unknown account handle: {from_handle!r}")

                    cur.execute(
                        "SELECT id, currency FROM accounts WHERE handle = %s",
                        (to_handle,),
                    )
                    to_row = cur.fetchone()
                    if to_row is None:
                        raise UnknownAccount(f"Unknown account handle: {to_handle!r}")

                    from_id, from_currency = from_row
                    to_id, to_currency = to_row

                    if from_id == to_id:
                        raise SameAccountTransfer(
                            f"Cannot transfer to the same account: {from_handle!r}"
                        )

                    if from_currency != to_currency:
                        raise CurrencyMismatch(
                            f"Currency mismatch: {from_handle!r} ({from_currency}) vs "
                            f"{to_handle!r} ({to_currency})"
                        )

                    cur.execute(
                        """
                        INSERT INTO transactions (kind, description, idempotency_key)
                        VALUES ('transfer', %s, %s)
                        RETURNING id
                        """,
                        (f"Transfer {from_handle} -> {to_handle}", key),
                    )
                    tx_id = int(cur.fetchone()[0])

                    cur.execute(
                        """
                        INSERT INTO entries (transaction_id, account_id, currency, amount)
                        VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)
                        """,
                        (
                            tx_id,
                            from_id,
                            from_currency,
                            -amount_cents,
                            tx_id,
                            to_id,
                            to_currency,
                            amount_cents,
                        ),
                    )
            return TransferResult(transaction_id=tx_id, duplicate=False)
        except psycopg.errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "transactions_idempotency_key_key" and key is not None:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, kind FROM transactions WHERE idempotency_key = %s",
                        (key,),
                    )
                    tx_row = cur.fetchone()
                    if tx_row is not None:
                        existing_tx_id = int(tx_row[0])
                        existing_kind = tx_row[1]
                        cur.execute(
                            """
                            SELECT account_id, currency, amount
                            FROM entries
                            WHERE transaction_id = %s
                            """,
                            (existing_tx_id,),
                        )
                        existing_entries = sorted(
                            (int(acc_id), curr, int(amt))
                            for acc_id, curr, amt in cur.fetchall()
                        )
                        expected_entries = sorted(
                            [
                                (int(from_id), from_currency, -amount_cents),
                                (int(to_id), to_currency, amount_cents),
                            ]
                        )
                        if existing_kind == "transfer" and existing_entries == expected_entries:
                            return TransferResult(transaction_id=existing_tx_id, duplicate=True)
                        raise IdempotencyConflict(
                            f"Idempotency key {key!r} already used for a different transfer "
                            f"(transaction_id={existing_tx_id})"
                        ) from exc
            raise
        except psycopg.errors.RaiseException as exc:
            if "cannot be overdrawn" in str(exc):
                raise InsufficientFunds(str(exc)) from exc
            raise
        except psycopg.errors.ForeignKeyViolation as exc:
            if exc.diag.constraint_name == "entries_account_id_currency_fkey":
                raise CurrencyMismatch(str(exc)) from exc
            raise
