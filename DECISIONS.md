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