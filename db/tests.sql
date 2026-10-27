-- SETUP: create three accounts
INSERT INTO accounts (name, currency, kind, allow_negative) VALUES
  ('Cash source (USD)', 'USD', 'system', TRUE),
  ('Tendai wallet',     'USD', 'customer', FALSE),
  ('Shop wallet',       'USD', 'customer', FALSE);

-- VALID: deposit $50 into Tendai's wallet
WITH t AS (
  INSERT INTO transactions (kind, description)
  VALUES ('deposit', 'Tendai tops up $50') RETURNING id
)
INSERT INTO entries (transaction_id, account_id, currency, amount)
SELECT t.id, v.account_id, v.currency, v.amount
FROM t, (VALUES (1, 'USD', -5000), (2, 'USD', 5000)) AS v(account_id, currency, amount);

-- BALANCES: a balance is the sum of an account's entries
SELECT a.name, COALESCE(SUM(e.amount), 0) AS balance_cents
FROM accounts a LEFT JOIN entries e ON e.account_id = a.id
GROUP BY a.id, a.name ORDER BY a.id;

-- SHOULD FAIL 1: unbalanced (-1000 and +900)
WITH t AS (
  INSERT INTO transactions (kind, description) VALUES ('transfer', 'bad') RETURNING id
)
INSERT INTO entries (transaction_id, account_id, currency, amount)
SELECT t.id, v.account_id, v.currency, v.amount
FROM t, (VALUES (2, 'USD', -1000), (3, 'USD', 900)) AS v(account_id, currency, amount);

-- SHOULD FAIL 2: wrong currency for the account
INSERT INTO entries (transaction_id, account_id, currency, amount) VALUES (1, 2, 'ZIG', 100);

-- SHOULD FAIL 3: zero amount
INSERT INTO entries (transaction_id, account_id, currency, amount) VALUES (1, 2, 'USD', 0);

-- SHOULD FAIL 4: rewriting history
UPDATE entries SET amount = 999999 WHERE id = (SELECT MIN(id) FROM entries);

-- SHOULD FAIL 5: deleting history
DELETE FROM entries WHERE id = (SELECT MIN(id) FROM entries);