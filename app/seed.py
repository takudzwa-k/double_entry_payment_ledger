import argparse
import sys

from app.commands import format_cents
from app.db import apply_migrations, get_connection, get_db_name, reset_schema

DEMO_ACCOUNTS = [
    ("sys-usd", "Cash source (USD)", "USD", "system", True, 0, None),
    ("sys-zig", "Cash source (ZIG)", "ZIG", "system", True, 0, None),
    ("+263771000001", "Tendai wallet", "USD", "customer", False, 3000, "seed-1"),
    ("+263771000002", "Shop wallet", "USD", "customer", False, 0, None),
    ("+263771000003", "Rudo wallet", "USD", "customer", False, 1500, "seed-2"),
    ("+263771000004", "Farai ZIG wallet", "ZIG", "customer", False, 10000, "seed-3"),
]

SYSTEM_HANDLES = {
    "USD": "sys-usd",
    "ZIG": "sys-zig",
}


def _print_summary(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.handle, a.name, a.currency, COALESCE(SUM(e.amount), 0)::BIGINT AS balance_cents
            FROM accounts a
            LEFT JOIN entries e ON e.account_id = a.id
            GROUP BY a.id, a.handle, a.name, a.currency
            ORDER BY a.id
            """
        )
        rows = cur.fetchall()

    print(f"{'Handle':<16} {'Name':<20} {'Curr':<5} {'Balance':>12}")
    print("-" * 56)
    for handle, name, curr, bal_cents in rows:
        print(f"{handle or '':<16} {name:<20} {curr.strip():<5} {format_cents(int(bal_cents)):>12}")
    print()
    print("Try it: python -m app.cli +263771000001")


def seed(reset: bool = False) -> bool:
    with get_connection() as conn:
        if reset:
            reset_schema(conn)
            apply_migrations(conn)
        else:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.accounts')")
                table_exists = cur.fetchone()[0] is not None
            conn.commit()

            if not table_exists:
                apply_migrations(conn)
            else:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM accounts")
                    existing_count = int(cur.fetchone()[0])
                conn.commit()
                if existing_count > 0:
                    print(
                        f"Database {get_db_name()!r} already has {existing_count} accounts; "
                        "no changes made. Use --reset to wipe and re-seed."
                    )
                    return False

        with conn.transaction():
            with conn.cursor() as cur:
                account_ids: dict[str, int] = {}
                for handle, name, curr, kind, allow_negative, _, _ in DEMO_ACCOUNTS:
                    cur.execute(
                        """
                        INSERT INTO accounts (name, currency, kind, allow_negative, handle)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (name, curr, kind, allow_negative, handle),
                    )
                    account_ids[handle] = int(cur.fetchone()[0])

                for handle, name, curr, _, _, deposit_cents, idem_key in DEMO_ACCOUNTS:
                    if deposit_cents <= 0:
                        continue
                    sys_id = account_ids[SYSTEM_HANDLES[curr]]
                    cust_id = account_ids[handle]
                    cur.execute(
                        """
                        INSERT INTO transactions (kind, description, idempotency_key)
                        VALUES ('deposit', %s, %s)
                        RETURNING id
                        """,
                        (f"Seed deposit for {name}", idem_key),
                    )
                    tx_id = int(cur.fetchone()[0])
                    cur.execute(
                        """
                        INSERT INTO entries (transaction_id, account_id, currency, amount)
                        VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)
                        """,
                        (
                            tx_id,
                            sys_id,
                            curr,
                            -deposit_cents,
                            tx_id,
                            cust_id,
                            curr,
                            deposit_cents,
                        ),
                    )

        _print_summary(conn)
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed demo accounts and balances.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop schema, re-apply migrations, and re-seed demo data.",
    )
    args = parser.parse_args(argv)

    if args.reset:
        dbname = get_db_name()
        answer = input(
            f"This will wipe database {dbname!r} and re-seed demo data. Type 'yes' to confirm: "
        )
        if not sys.stdin.isatty():
            print(answer)
        if answer.strip() != "yes":
            print("Aborted.")
            return 1

    seed(reset=args.reset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
