# BDD Docker fixtures

Test-only Dovecot servers for the behave suite.

## Why two instances

Cross-account scenarios (ADR 0006) must hit two *independent* IMAP
servers for the test to mean anything. A single server with two
users wouldn't exercise the network partition, auth-per-server, or
saga boundaries that matter. So we run `imap-a` and `imap-b`.

## Starting manually

```sh
cd bdd/docker
docker compose up -d
docker compose ps         # should show both healthy
docker compose logs imap-a
docker compose down -v    # tear down incl. mail volumes
```

`bdd/environment.py` does the same thing via `docker compose` from
behave hooks. Manual start is for interactive debugging.

## Ports (bound to 127.0.0.1 only)

| Service | Host port | Container port |
|---------|-----------|----------------|
| imap-a  | 11143     | 143 (IMAP)     |
| imap-b  | 12143     | 143 (IMAP)     |

No TLS, no SMTP, no IMAPS — this is a test fixture. Plain IMAP is
fine because nothing crosses the loopback interface.

## Seeding

`bdd/support/imap_fixture.py` connects as each test user and uses
IMAP `APPEND` / `CREATE` to set up folders and messages per
scenario. The container's Maildir volumes are ephemeral and are
wiped between test runs by `docker compose down -v`.

## Credentials

All four test users share password `test123`. Users are defined in
`./dovecot/users/*.passwd`. See the README there.

## Production note

This fixture is NOT a production configuration. Plain auth, plain
IMAP, no quota, no rate limiting. It exists solely to give the BDD
suite a real IMAP server to speak to.
