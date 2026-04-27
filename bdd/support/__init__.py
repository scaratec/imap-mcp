"""Support code for the imap-mcp BDD harness.

This package is test infrastructure. It contains:

- MCPClient         — subprocess + stdio wrapper for the server
- IMAPFixture       — seeds and inspects the dovecot test instances
- PolicyBuilder     — constructs YAML config trees for the server
- AuditReader       — parses and verifies the JSONL audit log
- WALReader         — inspects the SQLite WAL as a second channel

None of these modules import from ../server/. The server is exercised
exclusively as a subprocess speaking MCP, and via direct filesystem
reads of its audit log and WAL. This enforces the anti-circular-test
discipline called out by BDD Guidelines §4.1.
"""
