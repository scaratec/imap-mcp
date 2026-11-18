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

```text
tool.list_messages (root: mcp.tool, mcp.caller_id, mcp.decision, mcp.result.visible, mcp.result.returned)
├── imap.connect        (command, status)
├── imap.authenticate   (command, status)
├── imap.select         (command, status)
├── imap.uid_search     (command, status)
└── imap.uid_fetch      (command, status)
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
