#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MIGRATIONS_DIR="$REPO_ROOT/db/migrations"
CONTAINER="ledger_db"
DB_USER="ledger_user"
DB_NAME="ledger"
ROUNDS=10

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

ERRORS_LOG="$TMP_DIR/all_errors.txt"
: > "$ERRORS_LOG"

PASS_COUNT=0
FAIL_COUNT=0

run_transfer() {
  local label="$1"
  local outfile="$2"
  docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 <<SQL >"$outfile" 2>&1
BEGIN;
WITH t AS (
  INSERT INTO transactions (kind, description)
  VALUES ('transfer', 'Concurrent withdrawal ($label)') RETURNING id
)
INSERT INTO entries (transaction_id, account_id, currency, amount)
SELECT t.id, v.account_id, v.currency, v.amount
FROM t, (VALUES (2, 'USD', -2000), (3, 'USD', 2000)) AS v(account_id, currency, amount);
SELECT pg_sleep(1);
COMMIT;
SQL
}

for round in $(seq 1 "$ROUNDS"); do
  echo "=== Round $round / $ROUNDS ==="

  # 1. Reset schema and apply all migrations in order
  docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -q \
    -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null

  for migration in $(LC_ALL=C ls "$MIGRATIONS_DIR"/*.sql); do
    docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -q < "$migration" >/dev/null
  done

  # 2 & 3. Create accounts and deposit $30 (3000 cents) into Tendai's wallet
  docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -q <<'SQL' >/dev/null
INSERT INTO accounts (name, currency, kind, allow_negative) VALUES
  ('Cash source (USD)', 'USD', 'system', TRUE),
  ('Tendai wallet',     'USD', 'customer', FALSE),
  ('Shop wallet',       'USD', 'customer', FALSE);

WITH t AS (
  INSERT INTO transactions (kind, description)
  VALUES ('deposit', 'Tendai tops up $30') RETURNING id
)
INSERT INTO entries (transaction_id, account_id, currency, amount)
SELECT t.id, v.account_id, v.currency, v.amount
FROM t, (VALUES (1, 'USD', -3000), (2, 'USD', 3000)) AS v(account_id, currency, amount);
SQL

  # 4 & 5. Start two concurrent psql sessions and capture outputs and exit codes
  OUT1="$TMP_DIR/round_${round}_s1.out"
  OUT2="$TMP_DIR/round_${round}_s2.out"

  run_transfer "session 1" "$OUT1" &
  PID1=$!
  run_transfer "session 2" "$OUT2" &
  PID2=$!

  RC1=0
  wait "$PID1" || RC1=$?
  RC2=0
  wait "$PID2" || RC2=$?

  # 6. Query final balance and committed transfer count
  BALANCE="$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -v ON_ERROR_STOP=1 \
    -c "SELECT COALESCE(SUM(amount), 0) FROM entries WHERE account_id = 2;")"
  COMMITTED="$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -v ON_ERROR_STOP=1 \
    -c "SELECT COUNT(*) FROM transactions WHERE kind = 'transfer';")"

  ERR_LINES="$(grep -h '^ERROR:' "$OUT1" "$OUT2" || true)"
  if [[ -n "$ERR_LINES" ]]; then
    printf '%s\n' "$ERR_LINES" >> "$ERRORS_LOG"
  fi

  echo "Session 1 exit code: $RC1"
  echo "Session 2 exit code: $RC2"
  echo "Tendai final balance: $BALANCE cents"
  echo "Transfers committed: $COMMITTED"
  if [[ -n "$ERR_LINES" ]]; then
    echo "Failed session error:"
    printf '%s\n' "$ERR_LINES"
  else
    echo "Failed session error: (none)"
  fi

  # 7. Check invariants
  if (( BALANCE >= 0 )) && (( COMMITTED == 1 )) && { { (( RC1 == 0 )) && (( RC2 != 0 )); } || { (( RC1 != 0 )) && (( RC2 == 0 )); }; }; then
    echo "Result: PASS"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "Result: FAIL"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
  echo ""
done

echo "=== SUMMARY ==="
echo "PASS: $PASS_COUNT / $ROUNDS"
echo "FAIL: $FAIL_COUNT / $ROUNDS"
echo "Distinct error messages seen:"
if [[ -s "$ERRORS_LOG" ]]; then
  sort -u "$ERRORS_LOG" | sed 's/^/  - /'
else
  echo "  (none)"
fi
