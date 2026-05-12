# CHANGELOG

<!-- version list -->

## v0.11.1 (2026-05-12)

### Bug Fixes

- Silent 7-day filter, Apple Mail MIME detection,
  ([`bdeb31c`](https://github.com/scaratec/imap-mcp/commit/bdeb31ca152f9d2182967f778acb6538342c998e))


## v0.11.0 (2026-05-12)

### Features

- Return attachment bytes via MCP EmbeddedResource
  ([`82ac6ef`](https://github.com/scaratec/imap-mcp/commit/82ac6ef2e29fefa8d8e77a1cf85dbe1c9326a31b))


## v0.10.1 (2026-05-12)

### Bug Fixes

- **ci**: Make release pipeline idempotent for re-runs
  ([`49291b2`](https://github.com/scaratec/imap-mcp/commit/49291b229288221b945d5fa6576a916ad9483bb6))


## v0.10.0 (2026-05-12)

### Features

- Add flagged predicate and explicit IMAP user
  ([`c5ea4d8`](https://github.com/scaratec/imap-mcp/commit/c5ea4d884e53f3594c03e1327450b7c666e10677))


## v0.9.0 (2026-05-12)

### Features

- Resolve localized Gmail folder names via RFC 6154
  ([`20a5324`](https://github.com/scaratec/imap-mcp/commit/20a53240d9141b8a6d6b7c2f1e41aee38d2f4f33))


## v0.8.2 (2026-05-12)

### Bug Fixes

- **tracing**: Read service.version from package metadata instead of hardcoded
  ([`98eee0d`](https://github.com/scaratec/imap-mcp/commit/98eee0d207604f403dd8ae83c87421647cddb071))


## v0.8.1 (2026-05-12)

### Bug Fixes

- Improve create_draft and fetch_body tool descriptions to reduce DENY errors
  ([`016d82d`](https://github.com/scaratec/imap-mcp/commit/016d82dcd9a0fa1fb5398ebd14b7c1629ee16354))


## v0.8.0 (2026-05-12)

### Documentation

- Add pipx installation to README quick start
  ([`55ea8a6`](https://github.com/scaratec/imap-mcp/commit/55ea8a65b4b77be32504b933e763e2ff8fe25968))

- Overhaul README for accuracy after production deployment
  ([`fae2496`](https://github.com/scaratec/imap-mcp/commit/fae2496d4c5194407a0e8b204452458686458f53))

### Features

- Add bulk_mark_seen tool for batch flag operations
  ([`e568006`](https://github.com/scaratec/imap-mcp/commit/e568006bf69f56666fbc1401dd32d1db0c07e23d))


## v0.7.3 (2026-05-12)

### Bug Fixes

- Unfold RFC 5322 subject headers before returning to agent
  ([`94e40cf`](https://github.com/scaratec/imap-mcp/commit/94e40cf4580947551ee1ce67c0539be292d6f8bc))


## v0.7.2 (2026-05-11)

### Bug Fixes

- Improve tool descriptions for better agent tool selection
  ([`ccb1a8b`](https://github.com/scaratec/imap-mcp/commit/ccb1a8bc74cac77ffdebfca22e14eed888bdc41d))

### Documentation

- Update LIM-0011 resolution with N+1 connection fix details
  ([`720bedc`](https://github.com/scaratec/imap-mcp/commit/720bedc9a0cab6ef9e5e41b8bcadad93d7eaddf2))


## v0.7.1 (2026-05-11)

### Bug Fixes

- Batch envelope fetch eliminates N+1 connection bug
  ([`e94f308`](https://github.com/scaratec/imap-mcp/commit/e94f3087ac163a134a1dba2f62841d6673803cfd))

### Testing

- Prove connection-per-message bug via mock connection counter
  ([`0ca6261`](https://github.com/scaratec/imap-mcp/commit/0ca6261ff6bd7b1b02e1c924f5fc0dacf03df302))


## v0.7.0 (2026-05-11)

### Features

- **tracing**: Add request/response attributes to tool spans
  ([`973c0a7`](https://github.com/scaratec/imap-mcp/commit/973c0a7d28a23af979338437b77e8fffaf7b76c8))


## v0.6.0 (2026-05-11)

### Documentation

- Resolve LIM-0011 — search pagination and IMAP pre-filter already implemented
  ([`26bc165`](https://github.com/scaratec/imap-mcp/commit/26bc1653e5bff94c3380b2afbd20b3051b8a98b3))

### Features

- Add OpenTelemetry tracing with Jaeger backend
  ([`096c833`](https://github.com/scaratec/imap-mcp/commit/096c83395be6073b7b5dd98937803495674bb070))


## v0.5.0 (2026-05-11)

### Features

- Add list_messages tool for single-call mail overview
  ([`46235eb`](https://github.com/scaratec/imap-mcp/commit/46235eb9315c8c4c7103be30ab757ba6b9792bd5))


## v0.4.2 (2026-05-11)

### Bug Fixes

- **search**: Skip Gmail enrichment on large result pages
  ([`5ecbc7d`](https://github.com/scaratec/imap-mcp/commit/5ecbc7d501eeacbfc0dde417a0de21bf8c79db46))

### Code Style

- Apply ruff format to server.py
  ([`69dd9cc`](https://github.com/scaratec/imap-mcp/commit/69dd9cc44937753d067f4a5a238f2242fe690c3d))


## v0.4.1 (2026-05-11)

### Bug Fixes

- **search**: Map newer_than/older_than to IMAP SINCE/BEFORE
  ([`0049297`](https://github.com/scaratec/imap-mcp/commit/00492979874bb6a9621fa0f96e6c815471e9d985))


## v0.4.0 (2026-05-10)

### Features

- Expose package version in serverInfo and add
  ([`8486f8a`](https://github.com/scaratec/imap-mcp/commit/8486f8a699f299b78f157fa156a7aae8b6fe1803))


## v0.3.0 (2026-05-10)

### Features

- **search**: Skip per-message envelope fetch on
  ([`8a60e0d`](https://github.com/scaratec/imap-mcp/commit/8a60e0d1f3df43e364b6f0de50aceed29ded600c))


## v0.2.1 (2026-05-10)

### Bug Fixes

- Reopen stale read-only audit files on server restart
  ([`69a3e05`](https://github.com/scaratec/imap-mcp/commit/69a3e05098c2632da9a7ed38005fa5a1543b1f60))


## v0.2.0 (2026-05-10)

### Bug Fixes

- Add IMAPS (port 993) support and client_secret to OAuth flows
  ([`e6941f3`](https://github.com/scaratec/imap-mcp/commit/e6941f341e56475eadf2e0f965d41d1b50aec696))

- Resolve ruff lint and mypy strict errors for CI
  ([`280024f`](https://github.com/scaratec/imap-mcp/commit/280024f6c4decd6d61841e85d2371bfcafd0b094))

- **ci**: Install docker compose plugin in BDD runner, simplify pip install
  ([`b0300a8`](https://github.com/scaratec/imap-mcp/commit/b0300a811e91b4a5d0d452d699371075d56efa69))

- **ci**: Make typecheck non-blocking until type migration is complete
  ([`1425a9a`](https://github.com/scaratec/imap-mcp/commit/1425a9a5de4394530c08c920063b1f7f4c318a9c))

- **ci**: Relax mypy to check-untyped-defs, tolerate no-op releases
  ([`26f5ed2`](https://github.com/scaratec/imap-mcp/commit/26f5ed23ed24b285511eb1a319005b988fbc59c9))

- **ci**: Repair release pipeline and lint errors
  ([`cb90227`](https://github.com/scaratec/imap-mcp/commit/cb90227636719bf8655cedcb5f6e68ec3f4a2b35))

- **ci**: Simplify release workflow, defer PyPI publish
  ([`d805482`](https://github.com/scaratec/imap-mcp/commit/d805482728405506de4421734209178fbace04fc))

- **ci**: Skip release workflow when no new version is needed
  ([`2696637`](https://github.com/scaratec/imap-mcp/commit/2696637e3800b61e1ae65fe1e12494d752dc4278))

- **ci**: Use ARC scale set name as runs-on label for BDD workflow
  ([`5276d61`](https://github.com/scaratec/imap-mcp/commit/5276d61d214323871cfc57044ba6f9d45804131f))

- **ci**: Use RELEASE_TOKEN PAT to bypass branch protection
  ([`d01f33e`](https://github.com/scaratec/imap-mcp/commit/d01f33ebcd045cd19d5bd62d2baf8f66ef673756))

### Chores

- Relicense under GPL-3.0 and rewrite README for V1 scope
  ([`7248cdd`](https://github.com/scaratec/imap-mcp/commit/7248cdd13f228694534769c7e8409715c9f8d38f))

### Code Style

- Apply ruff format to all server source files
  ([`8a5eeea`](https://github.com/scaratec/imap-mcp/commit/8a5eeea6c7003a1b2e7b51b9eec9c918253cc97f))

- Apply ruff format to server.py
  ([`a829572`](https://github.com/scaratec/imap-mcp/commit/a82957251217ffd1bb5bc4b5d3fc85a5e97b25cf))

### Continuous Integration

- Add GitHub Actions pipelines and ARC runner setup
  ([`d72076a`](https://github.com/scaratec/imap-mcp/commit/d72076a64cd5a7d7412084e3f15f82e3b621dcff))

### Documentation

- Add CLAUDE.md and ROADMAP status snapshot for resumption
  ([`880d44f`](https://github.com/scaratec/imap-mcp/commit/880d44f29325fe5da9e3fbc32f3b628dca82b896))

- Add LIM-0010 (production IMAP BDD gaps) and LIM-0011 (search performance)
  ([`ae03535`](https://github.com/scaratec/imap-mcp/commit/ae03535c3167b2668c755db6017cceb050825311))

- Rewrite README for V1 release, rename package to sc-imap-mcp
  ([`a8f8398`](https://github.com/scaratec/imap-mcp/commit/a8f83984df1add40087f20a8236675dd951ca639))

- **adr**: Add 21 architecture decision records covering V1 scope
  ([`b4fbb16`](https://github.com/scaratec/imap-mcp/commit/b4fbb1647c09ba5fce8642b83bcb7eb5ea2c4954))

- **LIM-0011**: Sharpen search performance fix — IMAP pre-filter, pagination, default scope
  ([`abdb6fc`](https://github.com/scaratec/imap-mcp/commit/abdb6fc6680b4e45b297ec6c57c03c84d38ffe1d))

### Features

- Close all remaining LIMs — Gmail mock, OAuth scope reload, audit hooks, saga pause, spec-audit
  fixes
  ([`2c71770`](https://github.com/scaratec/imap-mcp/commit/2c717703b2679105bdaa197af0479b9f862587be))

- V1 implementation – BDD-grüne Suite, Saga, Audit, PDP
  ([`96c2b5c`](https://github.com/scaratec/imap-mcp/commit/96c2b5c758200d7356140b227f74ba8cbe2e7ec8))

- **audit**: Retention rotation, eof_day day-roll, fake-now (LIM-0009)
  ([`a95e86e`](https://github.com/scaratec/imap-mcp/commit/a95e86efa9aeb74f0063c8494701e4e3718b693a))

- **auth+mitm**: Close LIM-0004 and LIM-0007 — wire-level fault injection, env_var/gpg_file
  backends, stdio Initialize, identity_immutable
  ([`5ccfe91`](https://github.com/scaratec/imap-mcp/commit/5ccfe9190d39aef1827429e153d8b3e21e96374f))

- **ci**: Enable PyPI publishing via Trusted Publisher on release
  ([`c65bfe9`](https://github.com/scaratec/imap-mcp/commit/c65bfe9a800c15b179d4fe6cbc3a3c253b7c830b))

- **mitm**: IMAP MITM-Proxy für CAPABILITY-Strip + UIDVALIDITY-Inject (LIM-0005)
  ([`a28f609`](https://github.com/scaratec/imap-mcp/commit/a28f60937b6698c1d702e95bb484e803a72d5e06))

- **mitm**: Lift IMAP fault injection from env-var registry onto the wire proxy (LIM-0004)
  ([`7d64926`](https://github.com/scaratec/imap-mcp/commit/7d649266d35e309398bca336ee5a4aae42617881))

- **reload**: SIGHUP policy reload mit atomarem Live-State-Swap (LIM-0008)
  ([`8e7e128`](https://github.com/scaratec/imap-mcp/commit/8e7e128c8aa40837570babfb900ed9868a14425d))

- **saga**: 5-Tupel-Fallback-Idempotenz für Message-ID-lose Recoveries (LIM-0006)
  ([`9855d45`](https://github.com/scaratec/imap-mcp/commit/9855d45e671a980bce8fa9b91561472c6f19f844))

- **search**: Add criteria pre-filtering, pagination,
  ([`7738dc1`](https://github.com/scaratec/imap-mcp/commit/7738dc1a3a50385e8465de0e53d2ebbd91127101))

- **transport**: Streamable HTTP + shared_token bearer auth (LIM-0007)
  ([`44c4967`](https://github.com/scaratec/imap-mcp/commit/44c4967bc23aff2d5b5ac5f972127f5255a05dd2))


## v0.1.0 (2026-04-20)

- Initial Release
