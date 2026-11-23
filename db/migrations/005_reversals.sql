BEGIN;

-- RULE 5: a reversal cannot reverse another reversal
-- (Reversing the same transaction twice is already enforced by UNIQUE on transactions.reverses_id)
CREATE FUNCTION check_reversal_target() RETURNS trigger AS $$
DECLARE
    v_target_kind TEXT;
BEGIN
    IF NEW.kind <> 'reversal' THEN
        RETURN NEW;
    END IF;

    IF NEW.reverses_id = NEW.id THEN
        RAISE EXCEPTION 'Transaction % cannot reverse itself', NEW.id;
    END IF;

    SELECT kind INTO v_target_kind
    FROM transactions
    WHERE id = NEW.reverses_id;

    IF v_target_kind = 'reversal' THEN
        RAISE EXCEPTION 'Transaction % cannot reverse transaction %: target is already a reversal',
            NEW.id, NEW.reverses_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER transactions_no_reversal_of_reversal
BEFORE INSERT ON transactions
FOR EACH ROW EXECUTE FUNCTION check_reversal_target();

-- RULE 6: a reversal must match the original's accounts and currencies with every amount negated
CREATE FUNCTION assert_reversal_matches_original(p_tx_id BIGINT) RETURNS void AS $$
DECLARE
    v_kind        TEXT;
    v_reverses_id BIGINT;
    v_rev_count   INT;
    v_mismatch    BOOLEAN;
BEGIN
    SELECT kind, reverses_id INTO v_kind, v_reverses_id
    FROM transactions
    WHERE id = p_tx_id;

    IF v_kind <> 'reversal' THEN
        RETURN;
    END IF;

    SELECT COUNT(*) INTO v_rev_count
    FROM entries
    WHERE transaction_id = p_tx_id;

    IF v_rev_count = 0 THEN
        RAISE EXCEPTION 'Reversal % has no entries (must reverse transaction %)',
            p_tx_id, v_reverses_id;
    END IF;

    WITH orig AS (
        SELECT account_id, currency, -amount AS expected_amount, COUNT(*) AS cnt
        FROM entries
        WHERE transaction_id = v_reverses_id
        GROUP BY account_id, currency, -amount
    ),
    rev AS (
        SELECT account_id, currency, amount, COUNT(*) AS cnt
        FROM entries
        WHERE transaction_id = p_tx_id
        GROUP BY account_id, currency, amount
    )
    SELECT EXISTS (
        SELECT 1
        FROM orig
        FULL OUTER JOIN rev
          ON orig.account_id = rev.account_id
         AND orig.currency = rev.currency
         AND orig.expected_amount = rev.amount
         AND orig.cnt = rev.cnt
        WHERE orig.account_id IS NULL OR rev.account_id IS NULL
    ) INTO v_mismatch;

    IF v_mismatch THEN
        RAISE EXCEPTION 'Reversal % does not match negated entries of transaction %',
            p_tx_id, v_reverses_id;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION check_reversal_on_transaction() RETURNS trigger AS $$
BEGIN
    PERFORM assert_reversal_matches_original(NEW.id);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER transactions_reversal_matches
AFTER INSERT ON transactions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
WHEN (NEW.kind = 'reversal')
EXECUTE FUNCTION check_reversal_on_transaction();

CREATE FUNCTION check_reversal_on_entry() RETURNS trigger AS $$
BEGIN
    PERFORM assert_reversal_matches_original(NEW.transaction_id);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER entries_reversal_matches
AFTER INSERT ON entries
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION check_reversal_on_entry();

COMMIT;
