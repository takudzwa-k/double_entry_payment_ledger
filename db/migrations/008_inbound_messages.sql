BEGIN;

CREATE TABLE inbound_messages (
    message_id   TEXT PRIMARY KEY,
    sender       TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('processing', 'done')),
    reply        TEXT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ NULL,
    CHECK ((status = 'done') = (reply IS NOT NULL))
);

CREATE INDEX inbound_messages_created_at_idx ON inbound_messages (created_at);

COMMIT;
