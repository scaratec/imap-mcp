# Server unit tests

Property-based and unit tests for the server's pure-function surface.
Distinct from the BDD suite under `../bdd/`, which exercises the server
end-to-end via the MCP protocol.

The tests in this tree do **not** spin up Dovecot, the MCP transport, or
the audit writer's filesystem; they import the server modules directly
and assert on their pure outputs. This is intentional: BDD covers the
black-box contract, these tests cover the algebraic invariants the
PDP must satisfy across many synthesized inputs.

## Layout

- `policy/` — property-based tests for `imap_mcp.policy`. These are
  the paydown for [LIM-0001](../../docs/limitations/0001-reason-code-symmetry-in-bdd.md)
  Mitigation 6: synthesized (policy, caller, message) triples must
  yield reason codes that are structurally consistent with the inputs,
  catching regressions a BDD scenario could not enumerate.

## Running

```sh
cd server
. .venv/bin/activate
pytest tests/
```

Hypothesis controls deadline and example count via its CLI flags;
default deadline is generous because property-based tests can take
seconds when shrinking failures.
