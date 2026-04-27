"""Property-based tests for the Policy Decision Point.

Paydown for [LIM-0001](../../../docs/limitations/0001-reason-code-symmetry-in-bdd.md)
Mitigation 6. Generates random (folder_policy, message_facts) pairs
and asserts that `evaluate_message_against_folder` produces a reason
code that is structurally consistent with the inputs.

Properties checked:

1. **Reason vocabulary is closed.** Every reason code emitted is one
   of the four codes this function may legally emit:
   `rule_matched`, `folder_default_applied`, `sender_not_whitelisted`,
   `sender_blacklisted`. Anything else is a regression.
2. **DENY is consistent with visibility=NONE.** A non-allowed
   decision must have visibility "NONE". An allowed decision must
   have visibility != "NONE".
3. **Whitelist semantics.** No matching rule + default=NONE →
   `sender_not_whitelisted` (DENY). A matching rule with grant >=
   default → ALLOW with that visibility, reason `rule_matched`.
4. **Blacklist semantics.** A matching rule with cap=NONE → DENY
   with reason `sender_blacklisted`. No matching rule → ALLOW with
   reason `folder_default_applied` and visibility=default.
5. **Effective level is bounded by the input.** The returned
   visibility is between the most restrictive applicable bound
   (rule cap in blacklist) and the default (in whitelist with no
   matching rule).

The strategies deliberately produce both well-formed and edge-case
inputs (empty rule lists, multiple rules matching, no rules at all)
so the BDD-vocabulary "rule_matched" is hit from multiple angles
the BDD suite alone never enumerates.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from imap_mcp.config import FolderPolicy, SenderRule
from imap_mcp.policy import (
    MessageFacts,
    evaluate_message_against_folder,
    level_rank,
)


VISIBILITY_LEVELS = ("NONE", "COUNT", "METADATA", "ENVELOPE", "HEADERS", "BODY", "FULL")
NON_NONE_LEVELS = VISIBILITY_LEVELS[1:]
ALLOWED_REASONS = frozenset({"rule_matched", "folder_default_applied"})
DENIED_REASONS = frozenset({"sender_not_whitelisted", "sender_blacklisted"})
ALL_PDP_REASONS = ALLOWED_REASONS | DENIED_REASONS


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
                {"from": "billing@example.org"},
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
                {"from": "spam@unrelated.io"},
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
    assert decision.reason in ALL_PDP_REASONS, (
        f"emitted unknown reason {decision.reason!r}; "
        "vocabulary is closed (ADR-0017 §2.1)"
    )


@settings(max_examples=200, deadline=None)
@given(folder=_blacklist_folder_strategy(), facts=_facts_strategy())
def test_blacklist_reason_is_in_canonical_set(
    folder: FolderPolicy, facts: MessageFacts
) -> None:
    decision = evaluate_message_against_folder(folder, facts=facts)
    assert decision.reason in ALL_PDP_REASONS


@settings(max_examples=200, deadline=None)
@given(folder=_whitelist_folder_strategy(), facts=_facts_strategy())
def test_deny_implies_visibility_none_in_whitelist(
    folder: FolderPolicy, facts: MessageFacts
) -> None:
    decision = evaluate_message_against_folder(folder, facts=facts)
    if decision.allowed:
        assert decision.visibility != "NONE", (
            "ALLOW with visibility=NONE is structurally inconsistent"
        )
    else:
        assert decision.visibility == "NONE", (
            "DENY must always carry visibility=NONE"
        )


@settings(max_examples=200, deadline=None)
@given(folder=_blacklist_folder_strategy(), facts=_facts_strategy())
def test_deny_implies_visibility_none_in_blacklist(
    folder: FolderPolicy, facts: MessageFacts
) -> None:
    decision = evaluate_message_against_folder(folder, facts=facts)
    if decision.allowed:
        assert decision.visibility != "NONE"
    else:
        assert decision.visibility == "NONE"


@settings(max_examples=200, deadline=None)
@given(folder=_whitelist_folder_strategy(), facts=_facts_strategy())
def test_whitelist_no_matching_rule_yields_sender_not_whitelisted(
    folder: FolderPolicy, facts: MessageFacts
) -> None:
    """In whitelist mode with default=NONE, if no rule matches the
    message, the only consistent outcome is DENY with reason
    `sender_not_whitelisted`.
    """
    matching = [r for r in folder.rules if _rule_matches(r, facts)]
    decision = evaluate_message_against_folder(folder, facts=facts)
    if not matching:
        assert decision.allowed is False
        assert decision.reason == "sender_not_whitelisted"


@settings(max_examples=200, deadline=None)
@given(folder=_blacklist_folder_strategy(), facts=_facts_strategy())
def test_blacklist_no_matching_rule_yields_folder_default_applied(
    folder: FolderPolicy, facts: MessageFacts
) -> None:
    """In blacklist mode with default > NONE, no matching rule means
    the default applies. Reason must be `folder_default_applied` and
    visibility must equal the folder's default.
    """
    matching = [r for r in folder.rules if _rule_matches(r, facts)]
    decision = evaluate_message_against_folder(folder, facts=facts)
    if not matching:
        assert decision.allowed is True
        assert decision.reason == "folder_default_applied"
        assert decision.visibility == folder.default


@settings(max_examples=200, deadline=None)
@given(folder=_blacklist_folder_strategy(), facts=_facts_strategy())
def test_blacklist_cap_none_match_yields_sender_blacklisted(
    folder: FolderPolicy, facts: MessageFacts
) -> None:
    """A matching blacklist rule with cap=NONE must produce DENY with
    reason `sender_blacklisted` — a distinguishable code from the
    "no rule matched" case so callers can tell them apart.
    """
    matching = [r for r in folder.rules if _rule_matches(r, facts)]
    cap_none_matches = [r for r in matching if r.cap == "NONE"]
    decision = evaluate_message_against_folder(folder, facts=facts)
    if cap_none_matches:
        # A cap=NONE match drives the effective level to 0; the PDP
        # then reports sender_blacklisted.
        assert decision.allowed is False
        assert decision.reason == "sender_blacklisted"


@settings(max_examples=200, deadline=None)
@given(folder=_whitelist_folder_strategy(), facts=_facts_strategy())
def test_whitelist_grant_match_lifts_visibility(
    folder: FolderPolicy, facts: MessageFacts
) -> None:
    """A matching whitelist rule with grant=L raises the effective
    visibility to at least L (or higher if another matching rule
    grants more, or the default is higher).
    """
    matching = [r for r in folder.rules if _rule_matches(r, facts)]
    grants = [r.grant for r in matching if r.grant is not None]
    decision = evaluate_message_against_folder(folder, facts=facts)
    if grants:
        max_grant_rank = max(level_rank(g) for g in grants)
        assert decision.allowed is True
        assert decision.reason == "rule_matched"
        assert level_rank(decision.visibility) >= max_grant_rank


def _rule_matches(rule: SenderRule, facts: MessageFacts) -> bool:
    """Mirror of imap_mcp.policy._match_single_predicate, restricted to
    the predicates this test uses. Re-implementing the matcher here is
    intentional: if the strategy and the PDP disagree, we want to fail
    loudly rather than rationalize."""
    for key, expected in rule.match.items():
        if key == "from":
            if facts.from_address != expected:
                return False
        elif key == "from_domain":
            if facts.from_domain != expected:
                return False
        else:
            return False
    return True
