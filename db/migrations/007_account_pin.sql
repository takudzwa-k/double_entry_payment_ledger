BEGIN;

ALTER TABLE accounts
    ADD COLUMN pin_hash            TEXT NULL,
    ADD COLUMN pin_failed_attempts INT NOT NULL DEFAULT 0,
    ADD COLUMN pin_locked_until    TIMESTAMPTZ NULL;

COMMIT;
