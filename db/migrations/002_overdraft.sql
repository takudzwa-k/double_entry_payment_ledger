BEGIN;

-- RULE 3: customer accounts (allow_negative = FALSE) cannot end a transaction below zero
CREATE FUNCTION check_account_not_overdrawn() RETURNS trigger AS $$
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
    FOR UPDATE;

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

CREATE CONSTRAINT TRIGGER entries_no_overdraft
AFTER INSERT ON entries
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION check_account_not_overdrawn();

COMMIT;
