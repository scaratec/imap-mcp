"""Property-based tests for the Policy Decision Point.

Generates random (folder_policy, message_facts) pairs
and asserts that `evaluate_message_against_folder` produces a reason
code that is structurally consistent with the inputs under 3.0.0 (ADR 0040, ADR 0044, ADR 0062).
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from imap_mcp.config import FolderPolicy, SenderRule
from imap_mcp.policy import (
    MessageFacts,
    evaluate_message_against_folder,
    level_rank,
)

VISIBILITY_LEVELS = ("NONE", "COUNT", "METADATA", "ENVELOPE", "HEADERS", "BODY", "FULL")
NON_NONE_LEVELS = VISIBILITY_LEVELS[1:]
ALLOWED_REASONS = frozenset({"rule_matched", "folder_default_applied"})


def _facts_strategy() -> st.SearchStrategy[MessageFacts]:
    return st.builds(
        MessageFacts,
        from_address=st.sampled_from(
            [
                "rechnung@hornbach.de",
                "spam@unrelated.io",
                "billing@example.org",
                "noreply@bank.de",
                "invoice@obi.de",
            ]
        ),
        to_addresses=st.just(()),
        subject=st.sampled_from(["Rechnung 1", "Newsletter", "Subject", ""]),
        has_attachment=st.booleans(),
        size_bytes=st.integers(min_value=0, max_value=1_000_000),
        date_iso=st.just(None),
    )


def _whitelist_folder_strategy() -> st.SearchStrategy[FolderPolicy]:
    rule = st.builds(
        SenderRule,
        match=st.sampled_from(
            [
                {"from_domain": "hornbach.de"},
                {"from_domain": "obi.de"},
                {"from_address": "billing@example.org"},
                {},  # match-all
            ]
        ),
        grant=st.sampled_from(NON_NONE_LEVELS),
    )
    return st.builds(
        FolderPolicy,
        path=st.just("INBOX/Test"),
        mode=st.just("whitelist"),
        default=st.just("NONE"),
        rules=st.lists(rule, min_size=0, max_size=4),
    )


def _blacklist_folder_strategy() -> st.SearchStrategy[FolderPolicy]:
    rule = st.builds(
        SenderRule,
        match=st.sampled_from(
            [
                {"from_domain": "bank.de"},
                {"from_domain": "spam.io"},
                {"from_address": "spam@unrelated.io"},
                {},
            ]
        ),
        cap=st.sampled_from(VISIBILITY_LEVELS),
    )
    return st.builds(
        FolderPolicy,
        path=st.just("INBOX/Test"),
        mode=st.just("blacklist"),
        default=st.sampled_from(NON_NONE_LEVELS),
        rules=st.lists(rule, min_size=0, max_size=4),
    )


@settings(max_examples=200, deadline=None)
@given(folder=_whitelist_folder_strategy(), facts=_facts_strategy())
def test_whitelist_reason_is_in_canonical_set(
    folder: FolderPolicy, facts: MessageFacts
) -> None:
    decision = evaluate_message_against_folder(folder, facts=facts)
    assert decision.reason in ALLOWED_REASONS, (
        f"emitted unknown reason {decision.reason!r}; vocabulary is closed (ADR 0062)"
    )


@settings(max_examples=200, deadline=None)
@given(folder=_blacklist_folder_strategy(), facts=_facts_strategy())
def test_blacklist_reason_is_in_canonical_set(
    folder: FolderPolicy, facts: MessageFacts
) -> None:
    decision = evaluate_message_against_folder(folder, facts=facts)
    assert decision.reason in ALLOWED_REASONS


@settings(max_examples=200, deadline=None)
@given(folder=_whitelist_folder_strategy(), facts=_facts_strategy())
def test_whitelist_no_matching_rule_yields_folder_default_applied(
    folder: FolderPolicy, facts: MessageFacts
) -> None:
    matching = [r for r in folder.rules if _rule_matches(r, facts)]
    decision = evaluate_message_against_folder(folder, facts=facts)
    if not matching:
        assert decision.reason == "folder_default_applied"
        assert decision.visibility == "NONE"


@settings(max_examples=200, deadline=None)
@given(folder=_blacklist_folder_strategy(), facts=_facts_strategy())
def test_blacklist_no_matching_rule_yields_folder_default_applied(
    folder: FolderPolicy, facts: MessageFacts
) -> None:
    matching = [r for r in folder.rules if _rule_matches(r, facts)]
    decision = evaluate_message_against_folder(folder, facts=facts)
    if not matching:
        assert decision.reason == "folder_default_applied"
        assert decision.visibility == folder.default


@settings(max_examples=200, deadline=None)
@given(folder=_blacklist_folder_strategy(), facts=_facts_strategy())
def test_blacklist_cap_match_yields_rule_matched(
    folder: FolderPolicy, facts: MessageFacts
) -> None:
    matching = [r for r in folder.rules if _rule_matches(r, facts)]
    decision = evaluate_message_against_folder(folder, facts=facts)
    if matching:
        assert decision.reason == "rule_matched"


@settings(max_examples=200, deadline=None)
@given(folder=_whitelist_folder_strategy(), facts=_facts_strategy())
def test_whitelist_grant_match_lifts_visibility(
    folder: FolderPolicy, facts: MessageFacts
) -> None:
    matching = [r for r in folder.rules if _rule_matches(r, facts)]
    grants = [r.grant for r in matching if r.grant is not None]
    decision = evaluate_message_against_folder(folder, facts=facts)
    if grants:
        max_grant_rank = max(level_rank(g) for g in grants)
        assert decision.reason == "rule_matched"
        assert level_rank(decision.visibility) >= max_grant_rank


def _rule_matches(rule: SenderRule, facts: MessageFacts) -> bool:
    for key, expected in rule.match.items():
        if key == "from_address":
            if facts.from_address != expected:
                return False
        elif key == "from_domain":
            if facts.from_domain != expected:
                return False
        else:
            return False
    return True
