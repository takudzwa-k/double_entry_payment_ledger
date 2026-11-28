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
    .venv/bin/python -m app.seed       # Demo PIN for all customers: 1234
    .venv/bin/python -m app.cli +263771000001
    # Commands: BAL, SEND 5.50 TO 0771000002 1234, HISTORY [n], PIN SET <pin>, PIN CHANGE <old> <new>, HELP
    # Note: pytest uses a separate ledger_test database and never wipes ledger.

## Inbound message log
Every SMS `message_id` is logged in `inbound_messages` so duplicate or concurrent deliveries are processed once and return the stored reply.
Only the sender and reply are saved — raw message text (which may contain a PIN) is never stored or logged.
