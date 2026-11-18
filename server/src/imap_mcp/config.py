"""Configuration layer for sc-imap-mcp Version 3.0.0.

Loads and validates the server's YAML configuration tree into strongly
typed pydantic models. Validation is deterministic and rejects malformed
or semantically inconsistent input at load time, not at request time
(ADR 0040, ADR 0041, ADR 0045, ADR 0049, ADR 0050, ADR 0059, ADR 0064).
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypeVar

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
    identity: str | None = None
    auth: AccountAuth | None = None
    token_cache: Literal["memory_only", "persist_all"] = "memory_only"

    def wire_user(self) -> str:
        """User identity sent on wire for LOGIN / XOAUTH2 (ADR 0050)."""
        return self.user if self.user is not None else self.id


class SecretStore(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    backend: Literal["file_dir", "env_var", "gpg_file"] = "file_dir"
    path: str | None = None
    recipient: str | None = None
    gnupghome: str | None = None
    passphrase: str | None = None
    acknowledge_plaintext: bool = False

    @model_validator(mode="after")
    def _validate_secret_store(self) -> SecretStore:
        if self.path is not None:
            self.path = os.path.expanduser(self.path)
        if self.gnupghome is not None:
            self.gnupghome = os.path.expanduser(self.gnupghome)
        if self.backend == "file_dir" and not self.acknowledge_plaintext:
            raise ValueError(
                "file_dir backend requires explicit 'acknowledge_plaintext: true' in secret_store configuration"
            )
        return self


class AuditConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    directory: str | None = None
    hot_days: int = 90
    warm_days: int | None = None
    delete_after_days: int = 365
    external_root_hook: str | list[str] | None = None

    @model_validator(mode="after")
    def _validate_retention(self) -> AuditConfig:
        if self.directory is not None:
            self.directory = os.path.expanduser(self.directory)
        if self.warm_days is not None and self.hot_days + self.warm_days != self.delete_after_days:
            raise ValueError(
                f"legacy warm_days ({self.warm_days}) must satisfy hot_days ({self.hot_days}) + "
                f"warm_days == delete_after_days ({self.delete_after_days})"
            )
        if self.delete_after_days <= self.hot_days:
            raise ValueError(
                f"delete_after_days ({self.delete_after_days}) must be strictly greater than hot_days ({self.hot_days})"
            )
        return self


class WALConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str | None = None

    @model_validator(mode="after")
    def _validate_path(self) -> WALConfig:
        if self.path is not None:
            self.path = os.path.expanduser(self.path)
        return self


class AttachmentSinkConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    directory: str | None = None

    @model_validator(mode="after")
    def _validate_directory(self) -> AttachmentSinkConfig:
        if self.directory is not None:
            self.directory = os.path.expanduser(self.directory)
        return self


class AccountsFile(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    accounts: list[Account] = Field(default_factory=list)
    secret_store: SecretStore | None = None
    audit: AuditConfig | None = None
    wal: WALConfig | None = None
    attachment_sink: AttachmentSinkConfig | None = None

    @model_validator(mode="after")
    def _validate_env_var_persist_all(self) -> AccountsFile:
        if self.secret_store is not None and self.secret_store.backend == "env_var":
            for acc in self.accounts:
                if acc.token_cache == "persist_all":
                    raise ValueError(
                        f"account '{acc.id}': env_var secret store cannot be used with token_cache: persist_all"
                    )
        return self


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

    @model_validator(mode="after")
    def _validate_single_caller(self) -> CallersFile:
        if len(self.callers) != 1:
            raise ValueError("callers.yaml must define exactly one caller")
        caller = self.callers[0]
        if caller.auth is None:
            raise ValueError(f"caller '{caller.id}': missing auth block (must specify caller auth)")
        return self


VisibilityLevel = Literal["NONE", "COUNT", "METADATA", "ENVELOPE", "HEADERS", "BODY", "FULL"]

_DURATION_RE = re.compile(r"^[1-9][0-9]{0,5}[smhdwy]$")

# ADR 0041: Closed 18-predicate grammar
_CLOSED_PREDICATES: frozenset[str] = frozenset(
    {
        "from_address",
        "from_domain",
        "to_address",
        "to_domain",
        "cc_address",
        "cc_domain",
        "recipient_address",
        "recipient_domain",
        "all_recipients_in_domains",
        "subject_contains",
        "has_attachment",
        "flagged",
        "provenance",
        "newer_than",
        "older_than",
        "since",
        "before",
        "size_gt",
        "size_lt",
    }
)

_RETIRED_PREDICATES: frozenset[str] = frozenset({"from", "to", "to_contains"})


def parse_duration_seconds(value: str) -> int:
    """Parse ^[1-9][0-9]{0,5}[smhdwy]$ into seconds, bounded by <= 36500d (ADR 0041)."""
    if not isinstance(value, str) or not _DURATION_RE.fullmatch(value):
        raise ValueError(f"Invalid duration {value!r}")
    unit = value[-1]
    amount = int(value[:-1])
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
    seconds = amount * units[unit]
    if seconds > 36500 * 86400:
        raise ValueError(f"Duration {value!r} exceeds maximum 36500d")
    return seconds


class SenderRule(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    match: dict[str, object] = Field(default_factory=dict)
    grant: VisibilityLevel | None = None
    cap: VisibilityLevel | None = None

    @model_validator(mode="after")
    def _validate_rule(self) -> SenderRule:
        for key, val in self.match.items():
            if key in _RETIRED_PREDICATES or key not in _CLOSED_PREDICATES:
                raise ValueError(f'rule predicate "{key}": not in 3.0 core grammar')

            # Validate predicate value types & ranges
            if key in (
                "from_address",
                "from_domain",
                "to_address",
                "to_domain",
                "cc_address",
                "cc_domain",
                "recipient_address",
                "recipient_domain",
                "subject_contains",
            ):
                if not isinstance(val, str) or not val.strip():
                    raise ValueError(f'predicate "{key}" must be a non-empty string')
            elif key == "all_recipients_in_domains":
                if (
                    not isinstance(val, list)
                    or not val
                    or not all(isinstance(x, str) and x.strip() for x in val)
                ):
                    raise ValueError(
                        f'predicate "{key}" must be a non-empty list of domain strings'
                    )
            elif key in ("has_attachment", "flagged"):
                if not isinstance(val, bool):
                    raise ValueError(f'predicate "{key}" must be a boolean')
            elif key == "provenance":
                if val != "own":
                    raise ValueError('predicate "provenance" must equal "own"')
            elif key in ("newer_than", "older_than"):
                if not isinstance(val, str):
                    raise ValueError(f'predicate "{key}" must be a duration string')
                parse_duration_seconds(val)
            elif key in ("since", "before"):
                if not isinstance(val, str):
                    raise ValueError(f'predicate "{key}" must be an ISO calendar date string')
                try:
                    date.fromisoformat(val)
                except ValueError as exc:
                    raise ValueError(
                        f'predicate "{key}" must be an ISO date YYYY-MM-DD: {val}'
                    ) from exc
            elif key in ("size_gt", "size_lt") and (
                not isinstance(val, int) or isinstance(val, bool) or not (1 <= val <= 1073741824)
            ):
                raise ValueError(f'predicate "{key}" must be an integer between 1 and 1073741824')

        has_duration = any(k in self.match for k in ("newer_than", "older_than"))
        has_calendar = any(k in self.match for k in ("since", "before"))
        if has_duration and has_calendar:
            raise ValueError("cannot mix duration and calendar predicates in one rule")

        if "since" in self.match and "before" in self.match:
            d_since = date.fromisoformat(str(self.match["since"]))
            d_before = date.fromisoformat(str(self.match["before"]))
            if d_since >= d_before:
                raise ValueError(
                    f"rule 'since' ({d_since}) must be strictly earlier than 'before' ({d_before})"
                )

        return self


class DraftRecipientsConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    allowed_domains: list[str] = Field(default_factory=list)
    allowed_addresses: list[str] = Field(default_factory=list)


class FolderPolicy(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str
    mode: Literal["whitelist", "blacklist"]
    default: VisibilityLevel
    rules: list[SenderRule] = Field(default_factory=list)
    mark_seen: bool = False
    mark_tagged: bool = False
    move_out: bool = False
    copy_out: bool = False
    accept_incoming: bool = False
    draft_append: bool = False
    draft_edit: bool = False
    draft_recipients: DraftRecipientsConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_modify_message(cls, data: Any) -> Any:
        if isinstance(data, dict) and "modify_message" in data:
            raise ValueError(
                "modify_message capability is retired and forbidden in 3.0.0 (ADR 0057)"
            )
        return data

    @model_validator(mode="after")
    def _check_mode_invariants(self) -> FolderPolicy:
        if self.mode == "whitelist":
            if self.default != "NONE":
                raise ValueError(
                    f'folder "{self.path}": whitelist mode requires default=NONE (got default={self.default})'
                )
        else:  # blacklist
            if self.default == "NONE":
                raise ValueError(
                    f'folder "{self.path}": blacklist mode requires default > NONE (got default=NONE)'
                )
        for rule in self.rules:
            if self.mode == "whitelist":
                if rule.cap is not None:
                    raise ValueError(
                        f"folder \"{self.path}\": whitelist mode forbids 'cap'; use 'grant'"
                    )
                if rule.grant == "NONE":
                    raise ValueError(
                        f'folder "{self.path}": grant: NONE in whitelist is unreachable (equals default)'
                    )
            else:
                if rule.grant is not None:
                    raise ValueError(
                        f"folder \"{self.path}\": blacklist mode forbids 'grant'; use 'cap'"
                    )
        return self


class PolicyFile(BaseModel):
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
        return {**data, "accounts": flattened}


class Configuration(BaseModel):
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

    Enforces Kubernetes ..data symlink stability (up to 3 retries) and
    cross-file referential integrity (ADR 0049).
    """
    data_symlink = config_dir / "..data"

    for attempt in range(3):
        target_before = os.readlink(data_symlink) if data_symlink.is_symlink() else None

        accounts_file = _load_yaml(config_dir / "accounts.yaml", AccountsFile)
        callers_file = _load_yaml(config_dir / "callers.yaml", CallersFile)

        policies: dict[str, PolicyFile] = {}
        policies_dir = config_dir / "policies"
        if policies_dir.is_dir():
            for path in sorted(policies_dir.glob("*.yaml")):
                policy = _load_yaml(path, PolicyFile)
                if path.stem != policy.name:
                    raise ValueError(
                        f"policy file '{path.name}' defines policy name '{policy.name}' (must match filename)"
                    )
                policies[policy.name] = policy

        target_after = os.readlink(data_symlink) if data_symlink.is_symlink() else None
        if target_before == target_after:
            break
        if attempt == 2:
            raise RuntimeError(
                f"Kubernetes ..data symlink changed during configuration load across 3 attempts ({target_before} != {target_after})"
            )

    # Cross-file validation
    configured_account_ids = {a.id for a in accounts_file.accounts}

    # Verify caller policy exists
    caller = callers_file.callers[0]
    if caller.policy not in policies:
        raise ValueError(
            f"caller '{caller.id}' references policy '{caller.policy}' which does not exist in policies/"
        )

    # Verify policy accounts exist in accounts.yaml
    for pol_name, pol in policies.items():
        for acc_id, folder_policies in pol.accounts.items():
            if acc_id not in configured_account_ids:
                raise ValueError(
                    f"policy '{pol_name}' references account '{acc_id}' which does not exist in accounts.yaml"
                )

            # Check oauth_scope vs write capabilities
            account_obj = next((a for a in accounts_file.accounts if a.id == acc_id), None)
            if account_obj and account_obj.auth and account_obj.auth.type == "xoauth2":
                scope = account_obj.auth.oauth_scope or ""
                if "readonly" in scope:
                    for fp in folder_policies:
                        if (
                            fp.mark_seen
                            or fp.mark_tagged
                            or fp.move_out
                            or fp.copy_out
                            or fp.accept_incoming
                            or fp.draft_append
                            or fp.draft_edit
                        ):
                            raise ValueError(
                                f"account '{acc_id}': oauth_scope '{scope}' is readonly but folder '{fp.path}' grants write capabilities"
                            )

    return Configuration(
        accounts_file=accounts_file,
        callers_file=callers_file,
        policies=policies,
    )


_T = TypeVar("_T", bound=BaseModel)


def _load_yaml(path: Path, model: type[_T]) -> _T:
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML syntax error in configuration file '{path}': {exc}") from exc
    return model.model_validate(raw)
