# Design Decisions

## Postgres via Docker Compose
Chose Docker so anyone can run the same database version with one command,
avoiding "works on my machine" issues. Pinned to Postgres 16 for
reproducibility. Credentials live in `.env`, not in source control.

## Money stored as integers
(Write this one yourself: why integers in minor units and not floats?
Hint: try `0.1 + 0.2` in Python.)
