# Design Decisions

## Postgres via Docker Compose
Chose Docker so anyone can run the same database version with one command,
avoiding "works on my machine" issues. Pinned to Postgres 16 for
reproducibility. Credentials live in `.env`, not in source control.

## Money stored as integers
Stored money as whole numbers of cents because computers count in binary,
not in tens. Decimals like 0.1 can't be stored exactly, so `0.1 + 0.2`
gives `0.30000000000000004`. A bank can't lose tiny amounts, because the
errors add up across millions of transactions and the books stop balancing.
Whole numbers are always exact.

## Append-only ledger
Entries and transactions can never be edited or deleted. A mistake is
fixed by adding a new reversing transaction. I did this so the history
is trustworthy: if old rows could change, nobody could prove what really
happened. The database enforces it with triggers, not just my code.

## Double-entry, checked by the database
Every transaction has at least two entries, and they must add up to zero
in each currency. The check runs when the transaction commits, so a
multi-line transaction is judged as a whole. Because it lives in the
database, a bug in my Python code cannot break the rule.

## Balances are calculated, never stored
A balance is the sum of an account's entries. There is no balance column
that could drift out of sync with the history.

## One currency per account
An account has one fixed currency, and every entry must match it. A
transfer between two different currencies is rejected. I did this so
money in one currency can never be silently added to another.

## No overdrafts, safe under parallel payments
Customer accounts cannot go below zero. The check locks the account row,
so two payments at the same moment cannot both spend the same money. My
concurrency test first showed a deadlock, because the foreign key lock
and my lock clashed. I fixed it with a weaker lock (FOR NO KEY UPDATE),
and then the loser of the race fails with a clean "overdrawn" error.

## Idempotency keys
A transaction can carry a unique key, so a retried request cannot post
twice. If the same key arrives with different details (another amount or
other accounts), it is rejected as a conflict instead of being treated as
a harmless retry. Otherwise a $500 payment could be silently ignored.

## Reversals
A reversal must exactly mirror the original: same accounts, flipped
amounts. A transaction can be reversed only once, and a reversal cannot
be reversed. A reversal still follows the overdraft rule, so if the
receiver already spent the money, the reversal fails.

## Phone numbers as account handles
Accounts are found by a unique handle, which is a phone number. The
sender always comes from the channel, never from the message text, so
nobody can send money "as" someone else by typing their number.

## PIN protection
SEND needs a 4-digit PIN. PINs are stored hashed (PBKDF2-SHA256, random
salt, 600,000 rounds), never in plain text, and never logged. Three wrong
tries lock the account for 15 minutes. The counter is saved in its own
transaction before any transfer starts, so a wrong guess is always
counted, and the database clock decides when a lock ends.

## Inbound message log
Phone networks sometimes deliver the same message twice. Each message ID
is handled only once, and a repeat gets the stored reply. This stops one
typo from counting as several wrong PINs. I store only the sender and the
reply, never the message text, because the text contains the PIN.

## Money in text commands
Amounts typed by users are read as text and converted straight to cents.
Floats are never used, for the same reason as the integer decision above.

## Separate test database
Tests run on ledger_test, never on the app database. The test code
refuses to wipe any database whose name does not end in _test. I added
this after noticing that every test run was erasing my demo data.

## Known limits (things I would do next)
- BAL and HISTORY do not ask for a PIN. A thief with the phone could see
  balances, though not send money.
- PINs travel in plain text over SMS. A real USSD channel would hide them.
- The channel must prove who the sender is. This code trusts it.
- Old rows in the message log are never cleaned up yet.