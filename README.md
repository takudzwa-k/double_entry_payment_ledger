# Double-Entry Payment Ledger

A backend API for moving money between accounts using double-entry
bookkeeping. Focus: correctness, idempotency, concurrency safety, auditability.

## Run the database
    cp .env.example .env
    docker compose up -d

## How to run
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/pytest -v

## Try it
    .venv/bin/python -m app.seed
    .venv/bin/python -m app.cli +263771000001
    # Commands: BAL, SEND 5.50 TO 0771000002, HELP, quit
    # Note: pytest uses a separate ledger_test database and never wipes ledger.
