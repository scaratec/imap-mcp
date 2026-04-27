# Dovecot test users

Two passwd-files hold the test accounts for the two instances.

| Instance | File       | Accounts                                       |
|----------|------------|------------------------------------------------|
| imap-a   | `a.passwd` | `gupta@a.local`, `osthues@a.local`             |
| imap-b   | `b.passwd` | `personal@b.local`, `archive@b.local`          |

Password for **every** test account is `test123`.

These credentials are for the local Docker fixture only. They never
leave the BDD harness and must not be copied into any real config.

## Hash generation

Passwords are stored as Dovecot's BLF-CRYPT (bcrypt) format. To
regenerate a line:

```
docker run --rm dovecot/dovecot:2.3 doveadm pw -s BLF-CRYPT -p test123
```

Each line in a passwd file follows Dovecot's syntax:

```
user:password-hash::::::
```

The six trailing colons are placeholders for uid/gid/home/quota/extra
fields that are supplied by `dovecot.conf` defaults.
