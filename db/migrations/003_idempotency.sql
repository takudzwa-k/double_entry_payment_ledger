BEGIN;

-- RULE 4: prevent duplicate transactions via an optional unique idempotency key
ALTER TABLE transactions
    ADD COLUMN idempotency_key TEXT UNIQUE
    CHECK (idempotency_key <> '');

COMMIT;
