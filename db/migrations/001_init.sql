BEGIN;

-- ACCOUNTS: anything that can hold money
CREATE TABLE accounts (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           TEXT NOT NULL,
    currency       CHAR(3) NOT NULL CHECK (currency IN ('USD', 'ZIG')),
    kind           TEXT NOT NULL CHECK (kind IN ('customer', 'system')),
    allow_negative BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id, currency),
    CHECK (kind = 'system' OR allow_negative = FALSE)
);

-- TRANSACTIONS: one business event (deposit, transfer, reversal)
CREATE TABLE transactions (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('deposit', 'transfer', 'reversal')),
    description TEXT,
    reverses_id BIGINT UNIQUE REFERENCES transactions (id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((kind = 'reversal') = (reverses_id IS NOT NULL))
);

-- ENTRIES: the individual debit/credit lines
CREATE TABLE entries (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transaction_id BIGINT NOT NULL REFERENCES transactions (id),
    account_id     BIGINT NOT NULL,
    currency       CHAR(3) NOT NULL,
    amount         BIGINT NOT NULL CHECK (amount <> 0),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (account_id, currency) REFERENCES accounts (id, currency)
);

CREATE INDEX entries_account_idx ON entries (account_id);
CREATE INDEX entries_transaction_idx ON entries (transaction_id);

-- RULE 1: every transaction must have 2+ entries and sum to zero per currency
CREATE FUNCTION check_transaction_balanced() RETURNS trigger AS $$
DECLARE
    entry_count  INT;
    bad_currency TEXT;
BEGIN
    SELECT COUNT(*) INTO entry_count
    FROM entries WHERE transaction_id = NEW.transaction_id;

    IF entry_count < 2 THEN
        RAISE EXCEPTION 'Transaction % has fewer than 2 entries', NEW.transaction_id;
    END IF;

    SELECT currency INTO bad_currency
    FROM entries
    WHERE transaction_id = NEW.transaction_id
    GROUP BY currency
    HAVING SUM(amount) <> 0
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION 'Transaction % does not balance in %',
            NEW.transaction_id, bad_currency;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER entries_must_balance
AFTER INSERT ON entries
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION check_transaction_balanced();

-- RULE 2: the ledger is append-only (no edits, no deletes)
CREATE FUNCTION forbid_modification() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed: the ledger is append-only',
        TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER entries_append_only
BEFORE UPDATE OR DELETE ON entries
FOR EACH ROW EXECUTE FUNCTION forbid_modification();

CREATE TRIGGER transactions_append_only
BEFORE UPDATE OR DELETE ON transactions
FOR EACH ROW EXECUTE FUNCTION forbid_modification();

COMMIT;