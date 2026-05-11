# Tracing with Jaeger

Local tracing backend for imap-mcp development and debugging.

## Quick start

```bash
# Start Jaeger
docker compose up -d

# Run the server with tracing enabled
pip install sc-imap-mcp[tracing]
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
  imap-mcp --transport stdio

# Open the Jaeger UI
open http://localhost:16686
```

## What you see

Every MCP tool call creates a trace with nested spans:

```
tool.list_messages (root)
├── imap.connect        (host, port, latency)
├── imap.authenticate   (auth_type, success/failure)
├── imap.search         (folder, criteria, result_count)
├── pdp.evaluate        (mode, decision, matched_rule)
└── imap.fetch_envelope (uid, latency)  ×N
```

Cross-account moves show the full saga:

```
tool.move (root)
└── saga.cross_account_move (tx_id)
    ├── saga.fetch    (source account/folder)
    ├── saga.verify   (idempotency check)
    ├── saga.append   (target account/folder)
    ├── saga.delete   (source cleanup)
    └── saga.commit
```

## Ports

| Port  | Service |
|-------|---------|
| 16686 | Jaeger UI |
| 4317  | OTLP gRPC receiver |
| 4318  | OTLP HTTP receiver |

## Tear down

```bash
docker compose down
```
