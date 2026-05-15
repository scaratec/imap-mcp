"""Configuration layer.

Loads and validates the server's YAML configuration tree into strongly
typed pydantic models. No I/O beyond file reading; validation is
deterministic and rejects malformed or semantically inconsistent
input at load time, not at request time (ADR 0014).

Only the slice used by the Walking-Skeleton scenario is implemented
here. Additional fields (folder policies, sender rules, capabilities,
OAuth details, audit settings, WAL path) will be added as further
scenarios pull them in. Each addition keeps the same strict-mode
discipline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AccountAuth(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["password", "xoauth2"]
    secret_ref: str | None = None
    oauth_scope: str | None = None

    def password_secret_ref(self) -> str:
        if self.type != "password":
            raise ValueError(f"Cannot derive a password secret ref from auth.type={self.type!r}")
        if self.secret_ref is None:
            raise ValueError("password auth requires secret_ref to be set")
        return self.secret_ref


class Account(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    id: str
    provider: Literal["imap-standard", "google", "google-mock"] = "imap-standard"
    host: str = "127.0.0.1"
    port: int = 143
    user: str | None = None
    auth: AccountAuth | None = None
    token_cache: Literal["memory_only", "persist_all"] = "memory_only"


class SecretStore(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    backend: Literal["file_dir", "env_var", "gpg_file"] = "file_dir"
    path: str | None = None
    recipient: str | None = None
    gnupghome: str | None = None


class AuditConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    directory: str | None = None
    hot_days: int = 90
    warm_days: int = 275
    delete_after_days: int = 365
    external_root_hook: str | None = None


class WALConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str | None = None


class AccountsFile(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    accounts: list[Account] = Field(default_factory=list)
    secret_store: SecretStore | None = None
    audit: AuditConfig | None = None
    wal: WALConfig | None = None


class CallerAuth(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["stdio_trusted", "shared_token"]
    token_secret_ref: str | None = None


class Caller(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    id: str
    policy: str
    auth: CallerAuth | None = None


class CallersFile(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    callers: list[Caller] = Field(default_factory=list)


VisibilityLevel = Literal["NONE", "COUNT", "METADATA", "ENVELOPE", "HEADERS", "BODY", "FULL"]


# V1 matcher grammar (ADR 0004). Any key outside this set is a
# load-time error, surfaced with a diagnostic that names the key and
# the fact that it is not in V1 core grammar.
_CORE_MATCHER_KEYS: frozenset[str] = frozenset(
    {
        "from",
        "from_domain",
        "to",
        "to_contains",
        "subject_contains",
        "has_attachment",
        "flagged",
        "newer_than",
        "older_than",
        "size_gt",
        "size_lt",
    }
)


class SenderRule(BaseModel):
    """A single sender-rule inside a folder policy.

    The `match` map keys are drawn from the V1 core matcher grammar
    (ADR 0004). Unknown keys are rejected at load time so a typo or a
    reference to a deferred predicate cannot silently render the rule
    inert.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    match: dict[str, object] = Field(default_factory=dict)
    grant: VisibilityLevel | None = None
    cap: VisibilityLevel | None = None

    @model_validator(mode="after")
    def _check_matcher_keys(self) -> "SenderRule":
        unknown = set(self.match.keys()) - _CORE_MATCHER_KEYS
        if unknown:
            for key in sorted(unknown):
                raise ValueError(f'rule predicate "{key}": not in V1 core grammar')
        return self


class FolderPolicy(BaseModel):
    """Per-folder access declaration within a policy (ADR 0001, 0003, 0005).

    `mode` decides how `rules` are combined with `default`:
      - whitelist: effective = max(default, max(grant of matching rules))
      - blacklist: effective = min(default, min(cap of matching rules))
    The five capability booleans gate the corresponding write tools
    independently of the read-side visibility.

    Validation (ADR 0003) is enforced here so that a bad policy fails
    at load time with a clear error, never at request time with a
    silent wrong answer.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    path: str
    mode: Literal["whitelist", "blacklist"]
    default: VisibilityLevel
    rules: list[SenderRule] = Field(default_factory=list)
    mark_seen: bool = False
    mark_tagged: bool = False
    move_out: bool = False
    accept_incoming: bool = False
    draft_append: bool = False
    modify_message: bool = False

    @model_validator(mode="after")
    def _check_mode_invariants(self) -> "FolderPolicy":
        if self.mode == "whitelist":
            if self.default != "NONE":
                raise ValueError(
                    f'folder "{self.path}": whitelist mode requires '
                    f"default=NONE (got default={self.default})"
                )
        else:  # blacklist
            if self.default == "NONE":
                raise ValueError(
                    f'folder "{self.path}": blacklist mode requires '
                    f"default > NONE (got default=NONE)"
                )
        for rule in self.rules:
            if self.mode == "whitelist":
                if rule.cap is not None:
                    raise ValueError(
                        f"folder \"{self.path}\": whitelist mode forbids 'cap'; use 'grant'"
                    )
                if rule.grant == "NONE":
                    raise ValueError(
                        f'folder "{self.path}": grant: NONE in whitelist '
                        "is unreachable (equals default)"
                    )
            else:
                if rule.grant is not None:
                    raise ValueError(
                        f"folder \"{self.path}\": blacklist mode forbids 'grant'; use 'cap'"
                    )
        return self


class PolicyFile(BaseModel):
    """One named policy tree bound to a caller (ADR 0001, 0014).

    `accounts` maps an account-id to the list of folder-policies the
    caller may interact with on that account. An account-id present
    here — even with an empty list — counts as granted at the
    Account level. Absent account-ids are default-denied.

    Two YAML shapes are accepted, both equivalent after normalisation:
      accounts: { "<id>": [ {folder...}, {folder...} ] }
      accounts: { "<id>": { "folders": [ {folder...}, {folder...} ] } }
    The nested form is used by operator-written files that prefer a
    named `folders:` key for clarity; the flat form is what the BDD
    harness's PolicyBuilder emits. Everything downstream sees the
    flat form.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    accounts: dict[str, list[FolderPolicy]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _flatten_nested_folders(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw = data.get("accounts")
        if not isinstance(raw, dict):
            return data
        flattened: dict[str, object] = {}
        for account_id, body in raw.items():
            if isinstance(body, dict) and "folders" in body:
                extra_keys = set(body.keys()) - {"folders"}
                if extra_keys:
                    raise ValueError(
                        f'policy accounts["{account_id}"] nested form may '
                        f"only contain a `folders` key; got {sorted(extra_keys)!r}"
                    )
                flattened[account_id] = body["folders"]
            else:
                flattened[account_id] = body
        # Return a copy so we don't mutate pydantic's input
        return {**data, "accounts": flattened}


class Configuration(BaseModel):
    """The loaded, validated, in-memory configuration tree."""

    model_config = ConfigDict(strict=True, extra="forbid", arbitrary_types_allowed=True)

    accounts_file: AccountsFile
    callers_file: CallersFile
    policies: dict[str, PolicyFile]

    def caller_by_id(self, caller_id: str) -> Caller | None:
        for caller in self.callers_file.callers:
            if caller.id == caller_id:
                return caller
        return None

    def policy_by_name(self, name: str) -> PolicyFile | None:
        return self.policies.get(name)


def load_configuration(config_dir: Path) -> Configuration:
    """Parse accounts.yaml, callers.yaml, and every policies/*.yaml file.

    Validation is all-or-nothing: any schema or semantic error raises
    `pydantic.ValidationError` (or `FileNotFoundError` if a referenced
    file is missing). The caller decides how to react — for the MCP
    server this means: fail closed at startup; for SIGHUP reload,
    keep the previous tree (ADR 0014).
    """
    accounts_file = _load_yaml(config_dir / "accounts.yaml", AccountsFile)
    callers_file = _load_yaml(config_dir / "callers.yaml", CallersFile)

    policies: dict[str, PolicyFile] = {}
    policies_dir = config_dir / "policies"
    if policies_dir.is_dir():
        for path in sorted(policies_dir.glob("*.yaml")):
            policy = _load_yaml(path, PolicyFile)
            policies[policy.name] = policy

    return Configuration(
        accounts_file=accounts_file,
        callers_file=callers_file,
        policies=policies,
    )


_T = TypeVar("_T", bound=BaseModel)


def _load_yaml(path: Path, model: type[_T]) -> _T:
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return model.model_validate(raw)
