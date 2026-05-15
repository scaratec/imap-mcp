"""Generator for the server's YAML configuration tree.

Scenarios describe their desired policy in Gherkin tables and doc strings;
step files collect that data into a PolicyBuilder and emit the YAML files
the server then reads. Keeping the generator here (rather than inlining
YAML literals in step code) means scenarios stay readable and the mapping
from Gherkin to YAML is in one place.

No business logic lives here — the builder only serializes what the
scenario provided. This upholds BDD Guidelines §1.3 and §13.2 Prüfung 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Account:
    id: str
    provider: str = "imap-standard"
    host: str = "127.0.0.1"
    port: int = 11143
    user: str | None = None
    auth_type: str = "password"
    secret_ref: str | None = None
    oauth_scope: str | None = None
    token_cache: str = "memory_only"
    password_literal: str | None = None
    """Password the builder will deposit in the secret store at `write()` time.

    Explicit on-the-record value — the tests store `test123` for every
    fixture account, and that fact belongs in the feature-file-adjacent
    configuration, not in a silent helper."""


@dataclass
class SenderRule:
    match: dict[str, Any]
    grant: str | None = None
    cap: str | None = None


@dataclass
class FolderPolicy:
    path: str
    mode: str  # "whitelist" | "blacklist"
    default: str  # visibility level, one of NONE..FULL
    rules: list[SenderRule] = field(default_factory=list)
    mark_seen: bool = False
    mark_tagged: bool = False
    move_out: bool = False
    accept_incoming: bool = False
    draft_append: bool = False
    modify_message: bool = False


@dataclass
class Policy:
    name: str
    accounts: dict[str, list[FolderPolicy]] = field(default_factory=dict)


@dataclass
class Caller:
    id: str
    policy: str
    auth_type: str = "stdio_trusted"  # or "shared_token"
    token_secret_ref: str | None = None


@dataclass
class PolicyBuilder:
    """Collects accounts, callers, and policies; emits to a config dir."""

    config_dir: Path
    accounts: list[Account] = field(default_factory=list)
    callers: list[Caller] = field(default_factory=list)
    policies: list[Policy] = field(default_factory=list)

    secret_store_backend: str = "file_dir"
    secret_store_path: Path | None = None
    secret_store_recipient: str | None = None
    secret_store_gnupghome: Path | None = None
    audit_directory: Path | None = None
    audit_hot_days: int | None = None
    audit_warm_days: int | None = None
    audit_delete_after_days: int | None = None
    audit_external_root_hook: str | None = None
    wal_path: Path | None = None

    # ---------------------------------------------------------- collectors

    def add_account(self, **kwargs: Any) -> Account:
        account = Account(**kwargs)
        self.accounts.append(account)
        return account

    def add_caller(self, **kwargs: Any) -> Caller:
        caller = Caller(**kwargs)
        self.callers.append(caller)
        return caller

    def add_policy(self, name: str) -> Policy:
        policy = Policy(name=name)
        self.policies.append(policy)
        return policy

    def folder(
        self,
        policy_name: str,
        account_id: str,
        path: str,
        mode: str,
        default: str,
        **caps: Any,
    ) -> FolderPolicy:
        policy = next(p for p in self.policies if p.name == policy_name)
        folder_list = policy.accounts.setdefault(account_id, [])
        folder = FolderPolicy(path=path, mode=mode, default=default, **caps)
        folder_list.append(folder)
        return folder

    # ------------------------------------------------------------ emitters

    def write(self) -> None:
        """Write accounts.yaml, callers.yaml, and policies/*.yaml."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "policies").mkdir(exist_ok=True)

        # Persist every account's password into the secret store at the
        # ref the account config points to. This is the wiring that
        # lets the server authenticate to Dovecot when a tool actually
        # needs to talk to IMAP. Plain files; git-crypt / LUKS would
        # provide confidentiality in a real deployment.
        if self.secret_store_path is not None:
            for account in self.accounts:
                if account.password_literal is None or account.secret_ref is None:
                    continue
                if not account.secret_ref.startswith("secret://"):
                    raise ValueError(
                        f"Account {account.id!r} secret_ref must start "
                        f"with 'secret://'; got {account.secret_ref!r}"
                    )
                rel = account.secret_ref[len("secret://") :].lstrip("/")
                target = self.secret_store_path / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(account.password_literal, encoding="utf-8")

        (self.config_dir / "accounts.yaml").write_text(
            yaml.safe_dump(
                {
                    "accounts": [
                        _clean(
                            {
                                "id": a.id,
                                "provider": a.provider,
                                "host": a.host,
                                "port": a.port,
                                "user": a.user,
                                "auth": _clean(
                                    {
                                        "type": a.auth_type,
                                        "secret_ref": a.secret_ref,
                                        "oauth_scope": a.oauth_scope,
                                    }
                                ),
                                "token_cache": a.token_cache,
                            }
                        )
                        for a in self.accounts
                    ],
                    "secret_store": _clean(
                        {
                            "backend": self.secret_store_backend,
                            "path": str(self.secret_store_path)
                            if self.secret_store_path
                            else None,
                            "recipient": self.secret_store_recipient,
                            "gnupghome": str(self.secret_store_gnupghome)
                            if self.secret_store_gnupghome
                            else None,
                        }
                    ),
                    "audit": _clean(
                        {
                            "directory": str(self.audit_directory)
                            if self.audit_directory
                            else None,
                            "hot_days": self.audit_hot_days,
                            "warm_days": self.audit_warm_days,
                            "delete_after_days": self.audit_delete_after_days,
                            "external_root_hook": self.audit_external_root_hook,
                        }
                    ),
                    "wal": {"path": str(self.wal_path) if self.wal_path else None},
                },
                sort_keys=False,
            )
        )

        (self.config_dir / "callers.yaml").write_text(
            yaml.safe_dump(
                {
                    "callers": [
                        _clean(
                            {
                                "id": c.id,
                                "policy": c.policy,
                                "auth": _clean(
                                    {
                                        "type": c.auth_type,
                                        "token_secret_ref": c.token_secret_ref,
                                    }
                                ),
                            }
                        )
                        for c in self.callers
                    ]
                },
                sort_keys=False,
            )
        )

        for policy in self.policies:
            (self.config_dir / "policies" / f"{policy.name}.yaml").write_text(
                yaml.safe_dump(
                    {
                        "name": policy.name,
                        "accounts": {
                            account_id: [_folder_to_dict(f) for f in folder_list]
                            for account_id, folder_list in policy.accounts.items()
                        },
                    },
                    sort_keys=False,
                )
            )


def _folder_to_dict(folder: FolderPolicy) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": folder.path,
        "mode": folder.mode,
        "default": folder.default,
        "mark_seen": folder.mark_seen,
        "mark_tagged": folder.mark_tagged,
        "move_out": folder.move_out,
        "accept_incoming": folder.accept_incoming,
        "draft_append": folder.draft_append,
        "modify_message": folder.modify_message,
        "rules": [_rule_to_dict(r) for r in folder.rules],
    }
    return payload


def _rule_to_dict(rule: SenderRule) -> dict[str, Any]:
    payload: dict[str, Any] = {"match": rule.match}
    if rule.grant is not None:
        payload["grant"] = rule.grant
    if rule.cap is not None:
        payload["cap"] = rule.cap
    return payload


def _clean(mapping: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in mapping.items() if v is not None}
