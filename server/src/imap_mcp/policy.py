"""Policy Decision Point — ADR 0040, ADR 0041, ADR 0043, ADR 0044, ADR 0045, ADR 0056, ADR 0062.

Pure-function message-centric policy evaluation under default-deny hierarchy:
AccountPolicy -> FolderPolicy -> MessageRule[] with account-wide blacklist
cap-overrides and primary-label precedence on Google accounts.
"""

from __future__ import annotations

import email.utils
import hashlib
import hmac
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import IntEnum
from typing import Any

from .config import (
    Configuration,
    FolderPolicy,
    VisibilityLevel,
    parse_duration_seconds,
)


class _LevelRank(IntEnum):
    NONE = 0
    COUNT = 1
    METADATA = 2
    ENVELOPE = 3
    HEADERS = 4
    BODY = 5
    FULL = 6


def level_rank(level: VisibilityLevel | str) -> int:
    try:
        return int(_LevelRank[level])
    except KeyError:
        return 0


def rank_to_level(rank: int) -> VisibilityLevel:
    for level in ("NONE", "COUNT", "METADATA", "ENVELOPE", "HEADERS", "BODY", "FULL"):
        if level_rank(level) == rank:
            return level
    return "NONE"


def normalize_address(raw: str) -> tuple[str, str]:
    """Parse RFC 5322 address into (addr-spec lowercase, domain IDNA ASCII lowercase)."""
    _, addr = email.utils.parseaddr(raw)
    addr = addr.strip().lower()
    if "@" not in addr:
        return addr, ""
    local, domain = addr.rsplit("@", 1)
    domain = domain.rstrip(".").lower()
    try:
        domain_ascii = domain.encode("idna").decode("ascii")
    except Exception:  # noqa: BLE001
        domain_ascii = domain
    return f"{local}@{domain_ascii}", domain_ascii


def normalize_domain(raw: str) -> str:
    """Normalize a domain string: strip trailing dot, lowercase, IDNA ASCII."""
    clean = raw.strip().rstrip(".").lower()
    clean = clean.removeprefix("@")
    try:
        return clean.encode("idna").decode("ascii")
    except Exception:  # noqa: BLE001
        return clean


@dataclass(frozen=True)
class MessageFacts:
    from_address: str
    to_addresses: tuple[str, ...] = ()
    subject: str = ""
    has_attachment: bool = False
    flagged: bool = False
    size_bytes: int = 0
    date_iso: str | None = None
    internaldate_utc: datetime | None = None
    from_domain: str = ""
    to_domains: tuple[str, ...] = ()
    cc_addresses: tuple[str, ...] = ()
    cc_domains: tuple[str, ...] = ()
    recipient_addresses: tuple[str, ...] = ()
    recipient_domains: tuple[str, ...] = ()
    provenance_own: bool = False
    labels: tuple[str, ...] = ()
    x_gm_msgid: int | None = None

    def __post_init__(self) -> None:
        from_addr, from_dom = normalize_address(self.from_address)
        object.__setattr__(self, "from_address", from_addr)
        if not self.from_domain:
            object.__setattr__(self, "from_domain", from_dom)

        if not self.to_domains and self.to_addresses:
            to_doms = tuple(
                normalize_address(a)[1] for a in self.to_addresses if normalize_address(a)[1]
            )
            object.__setattr__(self, "to_domains", to_doms)

        if not self.cc_domains and self.cc_addresses:
            cc_doms = tuple(
                normalize_address(a)[1] for a in self.cc_addresses if normalize_address(a)[1]
            )
            object.__setattr__(self, "cc_domains", cc_doms)

        if not self.recipient_addresses:
            rec_addrs = tuple(dict.fromkeys(self.to_addresses + self.cc_addresses))
            object.__setattr__(self, "recipient_addresses", rec_addrs)

        if not self.recipient_domains:
            rec_doms = tuple(dict.fromkeys(self.to_domains + self.cc_domains))
            object.__setattr__(self, "recipient_domains", rec_doms)

        if self.internaldate_utc is None and self.date_iso:
            try:
                from email.utils import parsedate_to_datetime

                dt = parsedate_to_datetime(self.date_iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                else:
                    dt = dt.astimezone(UTC)
                object.__setattr__(self, "internaldate_utc", dt)
            except Exception:  # noqa: BLE001
                try:
                    dt = datetime.fromisoformat(self.date_iso)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    else:
                        dt = dt.astimezone(UTC)
                    object.__setattr__(self, "internaldate_utc", dt)
                except Exception:  # noqa: BLE001, S110
                    pass

    @classmethod
    def from_envelope(
        cls,
        *,
        from_raw: str,
        to_raws: list[str] | tuple[str, ...],
        cc_raws: list[str] | tuple[str, ...] = (),
        bcc_raws: list[str] | tuple[str, ...] = (),
        subject_raw: str,
        has_attachment: bool = False,
        flagged: bool = False,
        provenance_own: bool = False,
        size_bytes: int = 0,
        internaldate_utc: datetime | None = None,
        labels: tuple[str, ...] = (),
        x_gm_msgid: int | None = None,
    ) -> MessageFacts:
        from_addr, from_dom = normalize_address(from_raw)

        to_addrs, to_doms = [], []
        for r in to_raws:
            a, d = normalize_address(r)
            if a:
                to_addrs.append(a)
            if d:
                to_doms.append(d)

        cc_addrs, cc_doms = [], []
        for r in cc_raws:
            a, d = normalize_address(r)
            if a:
                cc_addrs.append(a)
            if d:
                cc_doms.append(d)

        bcc_addrs, bcc_doms = [], []
        for r in bcc_raws:
            a, d = normalize_address(r)
            if a:
                bcc_addrs.append(a)
            if d:
                bcc_doms.append(d)

        all_addrs = tuple(dict.fromkeys(to_addrs + cc_addrs + bcc_addrs))
        all_doms = tuple(dict.fromkeys(to_doms + cc_doms + bcc_doms))

        # Unicode NFC case-folded subject
        norm_subject = unicodedata.normalize("NFC", subject_raw or "").casefold()

        return cls(
            from_address=from_addr,
            from_domain=from_dom,
            to_addresses=tuple(to_addrs),
            to_domains=tuple(to_doms),
            cc_addresses=tuple(cc_addrs),
            cc_domains=tuple(cc_doms),
            recipient_addresses=all_addrs,
            recipient_domains=all_doms,
            subject=norm_subject,
            has_attachment=has_attachment,
            flagged=flagged,
            provenance_own=provenance_own,
            size_bytes=size_bytes,
            internaldate_utc=internaldate_utc,
            labels=labels,
            x_gm_msgid=x_gm_msgid,
        )


PREDICATE_FLOORS: dict[str, str] = {
    "flagged": "METADATA",
    "provenance": "METADATA",
    "newer_than": "METADATA",
    "older_than": "METADATA",
    "since": "METADATA",
    "before": "METADATA",
    "size_gt": "METADATA",
    "size_lt": "METADATA",
    "from_address": "ENVELOPE",
    "from_domain": "ENVELOPE",
    "to_address": "ENVELOPE",
    "to_domain": "ENVELOPE",
    "cc_address": "ENVELOPE",
    "cc_domain": "ENVELOPE",
    "recipient_address": "ENVELOPE",
    "recipient_domain": "ENVELOPE",
    "all_recipients_in_domains": "ENVELOPE",
    "subject_contains": "ENVELOPE",
    "has_attachment": "BODY",
}


def req_vis(criteria: Mapping[str, Any] | None) -> VisibilityLevel:
    """Highest visibility floor required to evaluate the criteria (ADR 0041, ADR 0043)."""
    if not criteria:
        return "METADATA"
    max_rank = level_rank("METADATA")
    for key in criteria:
        floor = PREDICATE_FLOORS.get(key, "METADATA")
        max_rank = max(max_rank, level_rank(floor))
    return rank_to_level(max_rank)


def match_single_predicate(
    key: str, expected: object, *, facts: MessageFacts, now_utc: datetime | None = None
) -> bool:
    """Evaluate one 3.0.0 grammar predicate against MessageFacts (ADR 0041)."""
    if key == "from_address":
        return isinstance(expected, str) and facts.from_address == expected.strip().lower()
    if key == "from_domain":
        return isinstance(expected, str) and facts.from_domain == normalize_domain(expected)
    if key == "to_address":
        return isinstance(expected, str) and expected.strip().lower() in facts.to_addresses
    if key == "to_domain":
        return isinstance(expected, str) and normalize_domain(expected) in facts.to_domains
    if key == "cc_address":
        return isinstance(expected, str) and expected.strip().lower() in facts.cc_addresses
    if key == "cc_domain":
        return isinstance(expected, str) and normalize_domain(expected) in facts.cc_domains
    if key == "recipient_address":
        return isinstance(expected, str) and expected.strip().lower() in facts.recipient_addresses
    if key == "recipient_domain":
        return isinstance(expected, str) and normalize_domain(expected) in facts.recipient_domains
    if key == "all_recipients_in_domains":
        if not isinstance(expected, list) or not expected or not facts.recipient_domains:
            return False
        allowed_domains = {normalize_domain(d) for d in expected if isinstance(d, str)}
        return all(dom in allowed_domains for dom in facts.recipient_domains)
    if key == "subject_contains":
        if not isinstance(expected, str):
            return False
        needle = unicodedata.normalize("NFC", expected).casefold()
        return needle in facts.subject
    if key == "has_attachment":
        return bool(expected) == facts.has_attachment
    if key == "flagged":
        return bool(expected) == facts.flagged
    if key == "provenance":
        return expected == "own" and facts.provenance_own
    if key in ("newer_than", "older_than"):
        if not isinstance(expected, str) or facts.internaldate_utc is None:
            return False
        duration_sec = parse_duration_seconds(expected)
        if now_utc is None:
            from .audit import _now_utc

            now_utc = _now_utc()
        delta = (now_utc - facts.internaldate_utc).total_seconds()
        if key == "newer_than":
            return delta <= duration_sec
        return delta > duration_sec
    if key == "since":
        if not isinstance(expected, str) or facts.internaldate_utc is None:
            return False
        d_expected = date.fromisoformat(expected)
        return facts.internaldate_utc.date() >= d_expected
    if key == "before":
        if not isinstance(expected, str) or facts.internaldate_utc is None:
            return False
        d_expected = date.fromisoformat(expected)
        return facts.internaldate_utc.date() < d_expected
    if key == "size_gt":
        return isinstance(expected, int) and facts.size_bytes > expected
    if key == "size_lt":
        return isinstance(expected, int) and facts.size_bytes < expected
    return False


def _match_single_predicate(key: str, expected: object, *, facts: MessageFacts) -> bool:
    """Backward-compatible alias for existing imports."""
    return match_single_predicate(key, expected, facts=facts)


def parse_duration(value: str) -> int:
    """Backward-compatible duration parser."""
    return parse_duration_seconds(value)


_parse_duration = parse_duration


@dataclass(frozen=True)
class AccountVisibility:
    visible_account_ids: list[str]
    hidden_account_count: int = 0


@dataclass(frozen=True)
class FolderVisibility:
    visible_folder_paths: list[str]
    hidden_folder_count: int = 0


@dataclass(frozen=True)
class FolderDecision:
    allowed: bool
    reason: str
    visibility: VisibilityLevel
    folder_policy: FolderPolicy | None


@dataclass(frozen=True)
class MessageDecision:
    allowed: bool
    reason: str
    visibility: VisibilityLevel


def evaluate_folder_base_visibility(
    fp: FolderPolicy, facts: MessageFacts, now_utc: datetime | None = None
) -> tuple[int, bool]:
    """Evaluate message against single folder policy. Returns (effective_rank, rule_matched)."""
    default_rank = level_rank(fp.default)
    if fp.mode == "whitelist":
        matching_grants: list[int] = []
        for rule in fp.rules:
            if (
                all(
                    match_single_predicate(k, v, facts=facts, now_utc=now_utc)
                    for k, v in rule.match.items()
                )
                and rule.grant is not None
            ):
                matching_grants.append(level_rank(rule.grant))
        if matching_grants:
            return max(default_rank, max(matching_grants)), True
        return default_rank, False
    else:  # blacklist
        matching_caps: list[int] = []
        for rule in fp.rules:
            if (
                all(
                    match_single_predicate(k, v, facts=facts, now_utc=now_utc)
                    for k, v in rule.match.items()
                )
                and rule.cap is not None
            ):
                matching_caps.append(level_rank(rule.cap))
        if matching_caps:
            return min(default_rank, min(matching_caps)), True
        return default_rank, False


def evaluate_message_against_folder(
    folder_policy: FolderPolicy, *, facts: MessageFacts, now_utc: datetime | None = None
) -> MessageDecision:
    rank, rule_matched = evaluate_folder_base_visibility(folder_policy, facts, now_utc=now_utc)
    vis = rank_to_level(rank)
    reason = "rule_matched" if rule_matched else "folder_default_applied"
    return MessageDecision(
        allowed=True,
        reason=reason,
        visibility=vis,
    )


class PolicyDecisionPoint:
    """Pure-function wrapper over the configured policy tree."""

    def __init__(self, configuration: Configuration) -> None:
        self._configuration = configuration
        self._folder_aliases: dict[str, dict[str, str]] = {}

    def folder_aliases(self, account_id: str) -> dict[str, str] | None:
        return self._folder_aliases.get(account_id)

    def set_folder_aliases(self, account_id: str, aliases: Mapping[str, str]) -> None:
        self._folder_aliases[account_id] = dict(aliases)

    def canonical_folder(self, account_id: str, folder_path: str) -> str:
        if folder_path.upper() == "INBOX":
            return "INBOX"
        for canonical, wire in self._folder_aliases.get(account_id, {}).items():
            if wire == folder_path:
                return canonical
        return folder_path

    def _policy_for(self, caller_id: str) -> tuple[set[str], dict[str, list[FolderPolicy]]]:
        caller = self._configuration.caller_by_id(caller_id)
        if caller is None:
            return set(), {}
        policy = self._configuration.policy_by_name(caller.policy)
        if policy is None:
            return set(), {}
        return set(policy.accounts.keys()), policy.accounts

    def _normalized_folder_policies(
        self, account_id: str, folder_policies: list[FolderPolicy]
    ) -> dict[str, FolderPolicy | None]:
        normalized: dict[str, FolderPolicy | None] = {}
        first: dict[str, FolderPolicy] = {}
        for fp in folder_policies:
            key = self.canonical_folder(account_id, fp.path)
            if key not in first:
                first[key] = fp
                normalized[key] = fp
                continue
            winner = first[key]
            if fp.path == winner.path:
                continue
            if fp.model_dump(exclude={"path"}) != winner.model_dump(exclude={"path"}):
                normalized[key] = None
        return normalized

    def visible_accounts_for(self, caller_id: str) -> AccountVisibility:
        granted_ids, _ = self._policy_for(caller_id)
        configured_ids = [account.id for account in self._configuration.accounts_file.accounts]
        visible = [aid for aid in configured_ids if aid in granted_ids]
        hidden = len(configured_ids) - len(visible)
        return AccountVisibility(visible_account_ids=visible, hidden_account_count=hidden)

    def visible_folders_for(
        self, caller_id: str, account_id: str, known_folders: list[str]
    ) -> FolderVisibility:
        granted_ids, by_account = self._policy_for(caller_id)
        if account_id not in granted_ids:
            return FolderVisibility(visible_folder_paths=[], hidden_folder_count=0)
        policies = by_account.get(account_id, [])
        declared_paths = self._normalized_folder_policies(account_id, policies)
        visible: list[str] = []
        for path in known_folders:
            fp = declared_paths.get(self.canonical_folder(account_id, path))
            if fp is not None:
                visible.append(path)
        hidden = len(known_folders) - len(visible)
        return FolderVisibility(visible_folder_paths=visible, hidden_folder_count=hidden)

    def folder_policy(
        self, caller_id: str, account_id: str, folder_path: str
    ) -> FolderPolicy | None:
        _, by_account = self._policy_for(caller_id)
        policies = by_account.get(account_id, [])
        declared_paths = self._normalized_folder_policies(account_id, policies)
        key = self.canonical_folder(account_id, folder_path)
        return declared_paths.get(key)

    def folder_max_visibility(self, fp: FolderPolicy) -> VisibilityLevel:
        best = level_rank(fp.default)
        if fp.mode == "whitelist":
            for rule in fp.rules:
                if rule.grant is not None:
                    best = max(best, level_rank(rule.grant))
        return rank_to_level(best)

    def folder_may_hide(self, account_id: str, folder_path: str, fp: FolderPolicy) -> bool:
        """Derive static completeness may_hide boolean (ADR 0043 §2)."""
        account_obj = next(
            (a for a in self._configuration.accounts_file.accounts if a.id == account_id), None
        )
        is_google = account_obj is not None and account_obj.provider in ("google", "google-mock")
        canonical = self.canonical_folder(account_id, folder_path)

        # Standard check for the folder itself
        if fp.mode == "whitelist":
            folder_hides = True
        else:
            folder_hides = any(
                level_rank(r.cap) < level_rank(fp.default) for r in fp.rules if r.cap is not None
            )

        if not is_google:
            return folder_hides

        # On Google accounts:
        if canonical in ("[Gmail]/All Mail", "[Gmail]/Starred"):
            return True

        # Check account-wide cap overrides
        caller = self._configuration.callers_file.callers[0]
        _, by_account = self._policy_for(caller.id)
        account_policies = by_account.get(account_id, [])
        for other_fp in account_policies:
            if other_fp.mode == "blacklist" and any(
                level_rank(r.cap) < level_rank(other_fp.default)
                for r in other_fp.rules
                if r.cap is not None
            ):
                return True

        return folder_hides

    def decide_folder_access(
        self, caller_id: str, account_id: str, folder_path: str
    ) -> FolderDecision:
        granted_ids, by_account = self._policy_for(caller_id)
        if account_id not in granted_ids:
            return FolderDecision(
                allowed=False,
                reason="account_hidden",
                visibility="NONE",
                folder_policy=None,
            )
        folder_policies = self._normalized_folder_policies(
            account_id, by_account.get(account_id, [])
        )
        key = self.canonical_folder(account_id, folder_path)
        if key in folder_policies:
            fp = folder_policies[key]
            if fp is None:
                return FolderDecision(
                    allowed=False,
                    reason="policy_alias_conflict",
                    visibility="NONE",
                    folder_policy=None,
                )
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

    def evaluate_message_effective(
        self,
        caller_id: str,
        account_id: str,
        folder_path: str,
        facts: MessageFacts,
        now_utc: datetime | None = None,
    ) -> tuple[VisibilityLevel, str]:
        """Compute message-centric effective visibility with Gmail composition (ADR 0040)."""
        fp = self.folder_policy(caller_id, account_id, folder_path)
        if fp is None:
            return "NONE", "folder_hidden"

        account_obj = next(
            (a for a in self._configuration.accounts_file.accounts if a.id == account_id), None
        )
        is_google = account_obj is not None and account_obj.provider in ("google", "google-mock")
        canonical_target = self.canonical_folder(account_id, folder_path)

        if not is_google:
            rank, rule_matched = evaluate_folder_base_visibility(fp, facts, now_utc=now_utc)
            return rank_to_level(rank), (
                "rule_matched" if rule_matched else "folder_default_applied"
            )

        # Google Account-Wide Cap Overrides (ADR 0040 §2.1)
        _, by_account = self._policy_for(caller_id)
        all_account_fps = by_account.get(account_id, [])
        account_caps: list[int] = []
        for other_fp in all_account_fps:
            if other_fp.mode == "blacklist":
                for rule in other_fp.rules:
                    if rule.cap is not None and all(
                        match_single_predicate(k, v, facts=facts, now_utc=now_utc)
                        for k, v in rule.match.items()
                    ):
                        account_caps.append(level_rank(rule.cap))

        min_account_cap = min(account_caps) if account_caps else level_rank("FULL")

        # Primary-Label Precedence over [Gmail]/All Mail and [Gmail]/Starred (ADR 0040 §2.2)
        norm_labels = [self.canonical_folder(account_id, lbl) for lbl in facts.labels]
        l_specific = [
            lbl
            for lbl in norm_labels
            if lbl not in ("[Gmail]/All Mail", "[Gmail]/Starred", "[Gmail]/Important")
        ]

        if canonical_target in ("[Gmail]/All Mail", "[Gmail]/Starred"):
            if l_specific:
                # Capped to maximum granted across caller's configured policies for L_specific(m)
                granted_specific_ranks: list[int] = []
                for spec_lbl in l_specific:
                    spec_fp = self.folder_policy(caller_id, account_id, spec_lbl)
                    if spec_fp is not None:
                        r, _ = evaluate_folder_base_visibility(spec_fp, facts, now_utc=now_utc)
                        granted_specific_ranks.append(r)
                base_rank = max(granted_specific_ranks) if granted_specific_ranks else 0
                rule_matched = bool(granted_specific_ranks and base_rank > 0)
            else:
                # Archived mail: uses All Mail / Starred own policy
                base_rank, rule_matched = evaluate_folder_base_visibility(
                    fp, facts, now_utc=now_utc
                )
        else:
            base_rank, rule_matched = evaluate_folder_base_visibility(fp, facts, now_utc=now_utc)

        effective_rank = min(base_rank, min_account_cap)
        reason = "rule_matched" if (rule_matched or account_caps) else "folder_default_applied"
        return rank_to_level(effective_rank), reason

    def make_google_object_id(
        self, caller_id: str, account_id: str, x_gm_msgid: int | None, hmac_key: bytes
    ) -> str | None:
        """Derive opaque object_id gm_<24hex> via HMAC-SHA256 (ADR 0040 §2.3)."""
        if x_gm_msgid is None:
            return None
        token = f"{caller_id}:{account_id}:{x_gm_msgid}".encode()
        h = hmac.new(hmac_key, token, hashlib.sha256).hexdigest()[:24]
        return f"gm_{h}"

    def filter_labels_for_caller(
        self, caller_id: str, account_id: str, labels: list[str] | tuple[str, ...]
    ) -> list[str]:
        """Disclose only labels with caller max_visibility >= COUNT, sorted (ADR 0040 §2.4)."""
        visible: list[str] = []
        for lbl in labels:
            canonical = self.canonical_folder(account_id, lbl)
            fp = self.folder_policy(caller_id, account_id, canonical)
            if fp is not None:
                max_v = self.folder_max_visibility(fp)
                if level_rank(max_v) >= level_rank("COUNT"):
                    visible.append(canonical)
        return sorted(set(visible))

    def can_move_to_gmail_trash_or_spam(
        self, caller_id: str, account_id: str, facts: MessageFacts
    ) -> bool:
        """Moving to [Gmail]/Trash or [Gmail]/Spam requires move_out on ALL L_specific(m) (ADR 0040 §2.5)."""
        norm_labels = [self.canonical_folder(account_id, lbl) for lbl in facts.labels]
        l_specific = [
            lbl
            for lbl in norm_labels
            if lbl not in ("[Gmail]/All Mail", "[Gmail]/Starred", "[Gmail]/Important")
        ]
        for lbl in l_specific:
            fp = self.folder_policy(caller_id, account_id, lbl)
            if fp is None or not fp.move_out:
                return False
        return True
