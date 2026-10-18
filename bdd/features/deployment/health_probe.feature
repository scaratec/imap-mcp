@pending
Feature: HTTP health probe for orchestrated deployment
  As a cluster operator running imap-mcp as a sidecar (ADR-0029),
  I want an unauthenticated GET /healthz that reports whether the
  server has loaded its configuration and is serving,
  so that a liveness probe can restart a wedged container without
  holding a caller credential and without widening the tool surface.

  # @pending: this whole feature is a red-first specification for
  # ADR-0029 §7. `/healthz` does not exist yet — runtime/http.py
  # answers 404 for every path except the MCP endpoint. The tag comes
  # off in the change that implements the endpoint; it is NOT a
  # fixture gap. See docs/ROADMAP.md entry 23.

  The probe is the only path besides the MCP endpoint that the HTTP
  transport answers. Everything else — including `/admin` and
  `/admin/reload-policy` (ADR-0018, non_goal_rejection.feature) —
  stays 404. The probe carries no authentication because a kubelet
  cannot hold a caller token, and it therefore must disclose nothing
  a caller has not already been granted.

  Design note recorded during specification (ADR-0029 §7): the
  distinction between "ready" and "unready" is narrow in this server.
  A configuration error is fatal at startup (SystemExit), so the
  process either serves or does not exist. The 503 state is reachable
  only in the window between the listener binding and configuration
  finishing to load. If the implementation binds the socket only
  after the application object is fully constructed, that window is
  empty and `/healthz` is a liveness signal, not a readiness signal.
  Scenario "reports unavailable before configuration has loaded"
  pins the required behaviour either way; whether it is observable
  is an implementation property that the scenario will reveal.

  Covered error layers (per BDD Guidelines §4.5):
    - Ready       : configuration loaded          : 1
    - Unready     : configuration not yet loaded  : 1
    - Auth        : no credentials presented      : 1
    - Auth        : wrong credentials presented   : 1
    - Disclosure  : body reveals nothing further  : 1
    - Method      : non-GET on /healthz           : 1
    - Surface     : other paths remain 404        : 1
    - Audit       : probe traffic is not recorded : 1
    Total enumerated                              : 8   covered by this feature: 8

  Background:
    Given the server is configured with callers:
      | caller_id     | auth_type    | token_secret_ref                     |
      | invoice-agent | shared_token | secret://callers/invoice-agent/token |
    And the secret store contains value "correct-horse-battery" under "callers/invoice-agent/token"

  Scenario: A started server reports itself healthy
    Given the server is started with transport "http" on a random port
    When an HTTP client makes GET /healthz against the server
    Then the HTTP response status code is 200
    And the HTTP response body is the JSON object:
      """
      {"status": "ok"}
      """

  Scenario: The probe requires no credentials
    Given the server is started with transport "http" on a random port
    When an HTTP client makes GET /healthz against the server without an Authorization header
    Then the HTTP response status code is 200
    And the HTTP response body is the JSON object:
      """
      {"status": "ok"}
      """

  Scenario: A wrong bearer token does not turn the probe into an auth failure
    Given the server is started with transport "http" on a random port
    When an HTTP client makes GET /healthz against the server with bearer token "wrong-token"
    Then the HTTP response status code is 200
    And the HTTP response body is the JSON object:
      """
      {"status": "ok"}
      """
    And the audit log contains no entry with reason "auth_failed"

  Scenario: The probe body discloses nothing beyond the status
    Given the server is started with transport "http" on a random port
    When an HTTP client makes GET /healthz against the server
    Then the HTTP response body has exactly the keys:
      | key    |
      | status |
    And the HTTP response body does not mention any configured account id
    And the HTTP response body does not mention any configured caller id
    And the HTTP response body does not mention any configured filesystem path
    And the HTTP response body does not mention the package version

  Scenario: The probe reports unavailable before configuration has loaded
    # Requires a startup hold-point so the listener is observable while
    # the configuration load is still in flight. If no such window
    # exists in the implementation, this scenario documents that
    # `/healthz` is a liveness signal only — see the design note above.
    Given the server is started with transport "http" on a random port with configuration loading held
    When an HTTP client makes GET /healthz against the server
    Then the HTTP response status code is 503
    And the HTTP response body is the JSON object:
      """
      {"status": "unavailable"}
      """
    When configuration loading is released
    And an HTTP client makes GET /healthz against the server
    Then the HTTP response status code is 200

  Scenario Outline: Only GET reaches the probe
    Given the server is started with transport "http" on a random port
    When an HTTP client makes <method> /healthz against the server
    Then the HTTP response status code is 404

    Examples:
      | method |
      | POST   |
      | PUT    |
      | DELETE |

  Scenario Outline: Adding the probe does not widen the HTTP surface
    Given the server is started with transport "http" on a random port
    When an HTTP client makes GET <path> against the server
    Then the HTTP response status code is 404

    Examples:
      | path             |
      | /health          |
      | /healthz/detail  |
      | /readyz          |
      | /metrics         |
      | /admin           |

  Scenario: Probe traffic does not enter the audit chain
    # A liveness probe fires every few seconds for the life of the pod.
    # Auditing it would bury every real decision under probe records
    # and inflate the hash chain without adding evidence (ADR-0021).
    Given the server is started with transport "http" on a random port
    And the audit log is empty
    When an HTTP client makes GET /healthz against the server 5 times
    Then the audit log contains no entries
