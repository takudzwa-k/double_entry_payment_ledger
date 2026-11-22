BEGIN;

CREATE OR REPLACE FUNCTION check_account_not_overdrawn() RETURNS trigger AS $$
DECLARE
    v_allow_negative BOOLEAN;
    v_balance        BIGINT;
BEGIN
    -- Credits can never overdraw an account
    IF NEW.amount > 0 THEN
        RETURN NULL;
    END IF;

    -- Lock the account row so concurrent withdrawals serialize
    SELECT allow_negative INTO v_allow_negative
    FROM accounts
    WHERE id = NEW.account_id
    FOR NO KEY UPDATE;

    IF v_allow_negative THEN
        RETURN NULL;
    END IF;

    SELECT COALESCE(SUM(amount), 0) INTO v_balance
    FROM entries
    WHERE account_id = NEW.account_id;

    IF v_balance < 0 THEN
        RAISE EXCEPTION 'Account % cannot be overdrawn (balance: %)',
            NEW.account_id, v_balance;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

COMMIT;
