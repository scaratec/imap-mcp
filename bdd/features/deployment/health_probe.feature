Feature: Loopback HTTP /healthz liveness probe for sidecar deployment

  ADR 0051 §2 exposes a single unauthenticated HTTP endpoint GET /healthz
  on the loopback HTTP server:
    - Returns HTTP 200 with {"status": "ok"} when configuration is loaded
      and the audit directory is writable.
    - Returns HTTP 503 with {"status": "unavailable"} when the audit
      directory becomes unwritable.
    - Discloses zero account, caller, path, or version details, writes no
      audit entries, and returns HTTP 404 for non-GET methods and all
      other non-MCP routes.

  Covered error layers (per BDD Guidelines §4.5):
    - Healthy server returns 200 {"status": "ok"}                    : 1
    - Unauthenticated GET /healthz succeeds                          : 1
    - Wrong Authorization header on /healthz still returns 200       : 1
    - Response body contains only key "status", zero metadata leaks  : 1
    - Unwritable audit directory flips /healthz to 503 unavailable   : 1
    - Non-GET methods on /healthz return 404                         : 3
    - Other HTTP paths (/health, /healthz/detail, /readyz, /metrics, /admin) : 5
    - Probe requests write zero records to the audit chain           : 1
    Total enumerated                                                 : 14  covered by this feature: 14

  Background:
    Given the audit log directory is a fresh $TMPDIR/audit
    And secret_store configuration is:
      """
      backend: file_dir
      path: $SCRATCH/secrets
      acknowledge_plaintext: true
      """
    And the file "$SCRATCH/secrets/callers/invoice-agent/token" contains the exact bytes "correct-horse-battery"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
    And the server is configured with single caller:
      | caller_id     | auth_type    | token_secret_ref                     | policy         |
      | invoice-agent | shared_token | secret://callers/invoice-agent/token | invoice-policy |
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |

  Scenario: A started server reports HTTP 200 {"status": "ok"}
    Given the server process is started with transport "http" on 127.0.0.1 and a random port
    When an HTTP client makes GET /healthz against the server
    Then the HTTP response status code is 200
    And the HTTP response body is the JSON object:
      """
      {"status": "ok"}
      """

  Scenario: The probe requires no Authorization header
    Given the server process is started with transport "http" on 127.0.0.1 and a random port
    When an HTTP client makes GET /healthz against the server without an Authorization header
    Then the HTTP response status code is 200
    And the HTTP response body is the JSON object:
      """
      {"status": "ok"}
      """

  Scenario: A wrong bearer token on /healthz returns 200 without logging auth_failed
    Given the server process is started with transport "http" on 127.0.0.1 and a random port
    When an HTTP client makes GET /healthz against the server with bearer token "wrong-token"
    Then the HTTP response status code is 200
    And the HTTP response body is the JSON object:
      """
      {"status": "ok"}
      """
    And the audit log contains 0 records with reason "auth_failed"

  Scenario: The probe body contains only "status" and discloses no accounts, callers, paths, or versions
    Given the server process is started with transport "http" on 127.0.0.1 and a random port
    When an HTTP client makes GET /healthz against the server
    Then the HTTP response body has the exact keys ["status"]
    And the HTTP response text does NOT contain any of the literal strings:
      | forbidden_string |
      | gupta-scaratec   |
      | invoice-agent    |
      | secrets          |
      | 3.0.0            |

  Scenario: An unwritable audit directory switches GET /healthz to 503 {"status": "unavailable"} and recovers when writable
    Given the test runner is not executing as root
    And the server process is started with transport "http" on 127.0.0.1 and a random port
    And the audit directory and current day file are made read-only
    When an HTTP client makes GET /healthz against the server
    Then the HTTP response status code is 503
    And the HTTP response body is the JSON object:
      """
      {"status": "unavailable"}
      """
    When writability of the audit directory and current day file is restored
    And an HTTP client makes GET /healthz against the server
    Then the HTTP response status code is 200
    And the HTTP response body is the JSON object:
      """
      {"status": "ok"}
      """

  Scenario Outline: Non-GET HTTP methods on /healthz return 404
    Given the server process is started with transport "http" on 127.0.0.1 and a random port
    When an HTTP client makes <method> /healthz against the server
    Then the HTTP response status code is 404

    Examples:
      | method |
      | POST   |
      | PUT    |
      | DELETE |

  Scenario Outline: Other HTTP paths besides /mcp and /healthz return 404
    Given the server process is started with transport "http" on 127.0.0.1 and a random port
    When an HTTP client makes GET <path> against the server
    Then the HTTP response status code is 404

    Examples:
      | path            |
      | /health         |
      | /healthz/detail |
      | /readyz         |
      | /metrics        |
      | /admin          |

  Scenario: Probe traffic writes zero records into the audit chain
    Given the server process is started with transport "http" on 127.0.0.1 and a random port
    When an HTTP client makes GET /healthz against the server 5 times
    Then the audit log contains 0 records
