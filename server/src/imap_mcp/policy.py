"""Policy Decision Point — Walking-Skeleton slice.

Implements exactly the subset of ADR 0001 needed to answer
`list_accounts` for a given caller: which accounts from the server's
configured inventory does this caller's policy grant, and how many
are therefore hidden.

Everything beyond `visible_accounts_for(caller_id)` will be added as
further scenarios pull it in. Keeping this module intentionally small
means it is also trivial to reason about and unit-test.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .config import Configuration, FolderPolicy, VisibilityLevel


class _LevelRank(IntEnum):
    NONE = 0
    COUNT = 1
    METADATA = 2
    ENVELOPE = 3
    HEADERS = 4
    BODY = 5
    FULL = 6


def level_rank(level: VisibilityLevel) -> int:
    return int(_LevelRank[level])


@dataclass(frozen=True)
class AccountVisibility:
    """What the caller sees at the AccountPolicy level."""

    visible_account_ids: list[str]
    hidden_account_count: int


@dataclass(frozen=True)
class FolderVisibility:
    """What the caller sees at the FolderPolicy level for one account."""

    visible_folder_paths: list[str]
    hidden_folder_count: int


@dataclass(frozen=True)
class FolderDecision:
    """Resolved access for a specific (account, folder) tuple."""

    allowed: bool
    reason: str
    visibility: VisibilityLevel
    folder_policy: FolderPolicy | None


@dataclass(frozen=True)
class MessageDecision:
    """Resolved access for a specific message in a granted folder."""

    allowed: bool
    reason: str
    visibility: VisibilityLevel
    matched_rule_index: int | None = None


@dataclass(frozen=True)
class MessageFacts:
    """Everything a rule may read about a message.

    Assembled by the tool dispatcher from envelope + structural
    metadata *before* any policy evaluation runs. The PDP then reads
    only from this record — it never reaches back into IMAP.
    """

    from_address: str
    to_addresses: tuple[str, ...]
    subject: str
    has_attachment: bool
    flagged: bool
    size_bytes: int
    date_iso: str | None  # RFC 3339 datetime string if parseable

    @property
    def from_domain(self) -> str:
        return self.from_address.rsplit("@", 1)[-1].rstrip(".").lower()


# V1 core grammar from ADR 0004. A predicate not in this set is a
# load-time error, surfaced by the policy loader; at evaluation time,
# any lookup miss is treated as "no match" (fail closed).
_CORE_PREDICATES: frozenset[str] = frozenset(
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


def parse_duration(value: str) -> int:
    """Parse `"30d"`, `"7d"`, `"1h"`, `"90m"`, `"60s"`, `"2w"`, `"1y"`
    into seconds. Single source for the duration grammar per ADR 0024.
    """
    if not value or len(value) < 2:
        raise ValueError(f"Cannot parse duration {value!r}")
    unit = value[-1]
    amount = int(value[:-1])
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
    if unit not in units:
        raise ValueError(f"Unknown duration unit {unit!r} in {value!r}")
    return amount * units[unit]


# Back-compat alias for any in-repo caller that still uses the private
# spelling. Removable once Phase 5 cleanup confirms nothing else imports it.
_parse_duration = parse_duration


def _match_single_predicate(key: str, expected: object, *, facts: MessageFacts) -> bool:
    """Evaluate one core-grammar predicate against a message.

    A predicate outside the core grammar returns False — the policy
    loader should already have refused such rules, so this is a
    defence in depth.
    """
    from datetime import datetime, timezone

    if key == "from":
        return isinstance(expected, str) and facts.from_address.lower() == expected.lower()
    if key == "from_domain":
        if not isinstance(expected, str):
            return False
        return facts.from_domain == expected.rstrip(".").lower()
    if key == "to":
        if not isinstance(expected, str):
            return False
        target = expected.lower()
        return any(addr.lower() == target for addr in facts.to_addresses)
    if key == "to_contains":
        if not isinstance(expected, str):
            return False
        substring = expected.lower()
        return any(substring in addr.lower() for addr in facts.to_addresses)
    if key == "subject_contains":
        if not isinstance(expected, str):
            return False
        return expected.lower() in facts.subject.lower()
    if key == "has_attachment":
        return bool(expected) == facts.has_attachment
    if key == "flagged":
        return bool(expected) == facts.flagged
    if key == "newer_than" or key == "older_than":
        if not isinstance(expected, str) or facts.date_iso is None:
            return False
        seconds = parse_duration(expected)
        try:
            parsed = datetime.fromisoformat(facts.date_iso.replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        from .audit import _now_utc

        now = _now_utc()
        delta = (now - parsed).total_seconds()
        if key == "newer_than":
            return delta <= seconds
        return delta > seconds
    if key == "size_gt":
        return isinstance(expected, int) and facts.size_bytes > expected
    if key == "size_lt":
        return isinstance(expected, int) and facts.size_bytes < expected
    return False


def evaluate_message_against_folder(
    folder_policy: FolderPolicy, *, facts: MessageFacts
) -> MessageDecision:
    """Resolve a folder-policy for one specific message.

    Whitelist: effective = max(default, max(grant of matching rules)).
    Blacklist: effective = min(default, min(cap of matching rules)).
    Matched without grant/cap? -> folder default applies.
    No rule matches in whitelist + default NONE -> sender_not_whitelisted.
    Cap to NONE in blacklist -> sender_blacklisted.
    """
    default_rank = level_rank(folder_policy.default)
    matching_grants: list[tuple[int, int]] = []  # (rank, index)
    matching_caps: list[tuple[int, int]] = []
    any_rule_matched_in_blacklist = False
    for idx, rule in enumerate(folder_policy.rules):
        matched = all(
            _match_single_predicate(key, val, facts=facts) for key, val in rule.match.items()
        )
        if not matched:
            continue
        if folder_policy.mode == "whitelist" and rule.grant is not None:
            matching_grants.append((level_rank(rule.grant), idx))
        elif folder_policy.mode == "blacklist" and rule.cap is not None:
            any_rule_matched_in_blacklist = True
            matching_caps.append((level_rank(rule.cap), idx))

    matched_rule_idx: int | None = None
    if folder_policy.mode == "whitelist":
        if not matching_grants and default_rank == 0:
            return MessageDecision(
                allowed=False,
                reason="sender_not_whitelisted",
                visibility="NONE",
            )
        if matching_grants:
            best = max(matching_grants, key=lambda pair: pair[0])
            matched_rule_idx = best[1]
            effective = max(default_rank, best[0])
        else:
            effective = default_rank
    else:
        if matching_caps:
            worst = min(matching_caps, key=lambda pair: pair[0])
            matched_rule_idx = worst[1]
            effective = min(default_rank, worst[0])
        else:
            effective = default_rank
        if effective == 0 and any_rule_matched_in_blacklist:
            return MessageDecision(
                allowed=False,
                reason="sender_blacklisted",
                visibility="NONE",
                matched_rule_index=matched_rule_idx,
            )

    for level in ("NONE", "COUNT", "METADATA", "ENVELOPE", "HEADERS", "BODY", "FULL"):
        if level_rank(level) == effective:  # type: ignore[arg-type]
            visibility: VisibilityLevel = level  # type: ignore[assignment]
            break
    else:
        raise AssertionError(f"Unknown rank {effective!r}")
    reason = "rule_matched" if matched_rule_idx is not None else "folder_default_applied"
    return MessageDecision(
        allowed=True,
        reason=reason,
        visibility=visibility,
        matched_rule_index=matched_rule_idx,
    )


class PolicyDecisionPoint:
    """Pure-function wrapper over the configured policy tree.

    No I/O, no time, no randomness — the same inputs always yield
    the same outputs. This is what makes the PDP independently
    unit-testable and what keeps the tool-dispatch layer a thin
    adapter (ADR 0001).
    """

    def __init__(self, configuration: Configuration) -> None:
        self._configuration = configuration

    def _policy_for(self, caller_id: str) -> tuple[set[str], dict[str, list[FolderPolicy]]]:
        caller = self._configuration.caller_by_id(caller_id)
        if caller is None:
            return set(), {}
        policy = self._configuration.policy_by_name(caller.policy)
        if policy is None:
            return set(), {}
        return set(policy.accounts.keys()), policy.accounts

    def visible_accounts_for(self, caller_id: str) -> AccountVisibility:
        granted_ids, _ = self._policy_for(caller_id)
        configured_ids = [account.id for account in self._configuration.accounts_file.accounts]
        visible = [aid for aid in configured_ids if aid in granted_ids]
        hidden = len(configured_ids) - len(visible)
        return AccountVisibility(
            visible_account_ids=visible,
            hidden_account_count=hidden,
        )

    def visible_folders_for(
        self, caller_id: str, account_id: str, known_folders: list[str]
    ) -> FolderVisibility:
        """Return the subset of `known_folders` the caller may see on this account.

        `known_folders` is the full set the IMAP server reports for this
        account. The PDP filters it against the FolderPolicy entries:
        a folder only appears if its path is listed in the policy and
        its default + any matching rules resolve to visibility > NONE.
        Sender-rule-level filtering happens per message, not per folder.
        """
        granted_ids, by_account = self._policy_for(caller_id)
        if account_id not in granted_ids:
            return FolderVisibility(visible_folder_paths=[], hidden_folder_count=0)

        policies = by_account.get(account_id, [])
        declared_paths = {fp.path: fp for fp in policies}
        visible: list[str] = []
        for path in known_folders:
            fp = declared_paths.get(path)
            if fp is None:
                continue
            if fp.mode == "whitelist":
                # In whitelist mode a folder has default=NONE; its own
                # existence as a policy entry is what makes it visible.
                # Sender rules inside determine what the caller sees
                # of individual messages, not whether the folder itself
                # is listed.
                visible.append(path)
            else:
                # Blacklist mode: the folder default is > NONE by
                # construction (the loader enforces this in ADR 0003).
                visible.append(path)
        hidden = len(known_folders) - len(visible)
        return FolderVisibility(
            visible_folder_paths=visible,
            hidden_folder_count=hidden,
        )

    def decide_folder_access(
        self, caller_id: str, account_id: str, folder_path: str
    ) -> FolderDecision:
        """Evaluate whether a caller may reach a specific (account, folder) tuple.

        This is the step before any message-level sender-rule evaluation.
        It answers the classic DENY cases:
          - `account_hidden`: the account is not in the caller's policy.
          - `folder_hidden`: the account is, but the folder is not.
        If both layers pass, the caller is permitted to proceed to
        message-level checks. The returned `visibility` is the folder's
        declared default; sender rules may raise it on a per-message
        basis.
        """
        granted_ids, by_account = self._policy_for(caller_id)
        if account_id not in granted_ids:
            return FolderDecision(
                allowed=False,
                reason="account_hidden",
                visibility="NONE",
                folder_policy=None,
            )
        folder_policies = by_account.get(account_id, [])
        for fp in folder_policies:
            if fp.path == folder_path:
                return FolderDecision(
                    allowed=True,
                    reason="folder_default_applied",
                    visibility=fp.default,
                    folder_policy=fp,
                )
        return FolderDecision(
            allowed=False,
            reason="folder_hidden",
            visibility="NONE",
            folder_policy=None,
        )
