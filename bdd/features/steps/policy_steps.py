"""Steps that prepare the server's configuration tree.

These steps read YAML-like structures from the feature file and
hand them unchanged to `PolicyBuilder`, which serialises them to
accounts.yaml / callers.yaml / policies/*.yaml under the scenario's
scratch directory (see `before_scenario` in environment.py).

Per BDD Guidelines §1.3 and §5.1 these steps must not:
- invent fields that the scenario did not state,
- silently normalise or defaulting business values,
- derive policy rules from the step text beyond what behave's
  step matcher already exposes.

The only non-trivial translation done here is from feature-file
account ids to the Dovecot fixture's (instance, user) tuple, and
even that is a lookup in a fixed table, not a heuristic.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import UTC
from pathlib import Path
from typing import Any

import yaml
from behave import given, then, use_step_matcher, when
from behave.runner import Context

from support.imap_fixture import resolve_account
from support.policy_builder import PolicyBuilder
from support.server_bootstrap import BootstrapResult, try_bootstrap_server


def _ensure_builder(context: Context) -> PolicyBuilder:
    builder = getattr(context, "policy_builder", None)
    if builder is None:
        builder = PolicyBuilder(
            config_dir=context.config_dir,
            secret_store_path=context.secrets_dir,
            secret_store_acknowledge_plaintext=True,
            audit_directory=context.audit_dir,
            wal_path=context.wal_path,
        )
        context.policy_builder = builder
    return builder


# Every Dovecot test user's password (bdd/docker/dovecot/users/README.md).
FIXTURE_PASSWORD = "test123"


def _ensure_account_registered(
    context: Context, builder: PolicyBuilder, account_id: str
) -> None:
    if any(a.id == account_id for a in builder.accounts):
        return
    instance, user = resolve_account(account_id)
    host, port = context.imap_instances[instance]
    pwd = "test" if instance == "mock-gmail" else FIXTURE_PASSWORD
    builder.add_account(
        id=account_id,
        host=host,
        port=port,
        user=user,
        auth_type="password",
        secret_ref=f"secret://accounts/{account_id}/password",
        password_literal=pwd,
    )


@given('the secret store holds the password "{password}" for account "{account_id}"')
def step_secret_store_holds_password(
    context: Context, password: str, account_id: str
) -> None:
    """Put the scenario's password into the server's secret store.

    Dovecot knows one fixed password per test user, so the MITM proxy
    in front of the account translates the scenario password in LOGIN
    to the fixture's. The server itself only ever holds and sends the
    scenario password.
    """
    builder = _ensure_builder(context)
    _ensure_account_registered(context, builder, account_id)
    account = next(a for a in builder.accounts if a.id == account_id)
    account.password_literal = password
    builder.write()
    _start_imap_proxy(
        context, account_id, login_credentials={password: FIXTURE_PASSWORD}
    )


@given('the IMAP account "{account_id}" exists with folders:')
def step_imap_account_exists_with_folders(context: Context, account_id: str) -> None:
    builder = _ensure_builder(context)
    _ensure_account_registered(context, builder, account_id)

    instance, user = resolve_account(account_id)
    for row in context.table:
        folder = row["folder path"]
        context.imap.create_folder(instance, user, folder)
    if "special use" in context.table.headings:
        _assert_special_use_as_declared(context, instance, user)

    builder.write()


def _assert_special_use_as_declared(context: Context, instance: str, user: str) -> None:
    """Check that IMAP LIST reports each folder's special-use flags as
    the table declares them, an empty cell meaning none.

    Dovecot takes special-use flags from its configuration
    (docker/dovecot/conf/dovecot.conf), not from the client that
    creates the folder, so the harness cannot set them. A row the
    fixture does not reproduce is a harness gap and fails here, before
    the scenario runs against a mailbox other than the one specified.
    """
    reported = context.imap.folder_special_use(instance, user)
    for row in context.table:
        folder = row["folder path"]
        declared = sorted(flag.lower() for flag in row["special use"].split())
        if folder not in reported:
            raise AssertionError(
                f"Folder {folder!r} is not listed by the IMAP fixture; "
                f"listed: {sorted(reported)!r}"
            )
        actual = sorted(flag.lower() for flag in reported[folder])
        if actual != declared:
            raise AssertionError(
                f"Harness gap: IMAP LIST reports special use {reported[folder]!r} "
                f"for {folder!r}, the scenario declares {row['special use']!r}. "
                "Special-use flags come from docker/dovecot/conf/dovecot.conf."
            )






@given('the server clock is pinned to "{ts}"')
def step_server_clock_pinned(context: Context, ts: str) -> None:
    if "T" not in ts:
        raise AssertionError(f"server clock must be pinned to full ISO timestamp (ADR 0023), got: {ts!r}")
    env = getattr(context, "mcp_extra_env", None) or {}
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    env["IMAP_MCP_FAKE_NOW_UTC"] = ts
    context.mcp_extra_env = env
    context.is_test_hooks = True




@given("the server is configured with single caller:")
@given("the server is configured with caller:")
def step_server_configured_with_caller(context: Context) -> None:
    """Single-row caller setup. Tolerates table schemas with or
    without `policy` and `auth_type` columns.

    A missing `policy` column derives `<caller_id>-policy` so the
    loader's "policy must exist" invariant is satisfied without the
    feature having to spell it out for auth-only scenarios. An
    `auth_type` of `(none)` omits the caller's auth block entirely.
    """
    builder = _ensure_builder(context)
    builder.callers = []
    for row in context.table:
        caller_id = row["caller_id"]
        if "policy" not in row.headings or not row["policy"]:
            raise AssertionError(f"Callers table missing mandatory column 'policy' in {row.headings}")
        policy = row["policy"]
        auth_type: str | None = (
            row["auth_type"] if "auth_type" in row.headings else "stdio_trusted"
        )
        if auth_type == "(none)":
            auth_type = None
        token_secret_ref = (
            row["token_secret_ref"] if "token_secret_ref" in row.headings else None
        )
        if token_secret_ref == "(n/a)":
            token_secret_ref = None
        builder.add_caller(
            id=caller_id,
            policy=policy,
            auth_type=auth_type,
            token_secret_ref=token_secret_ref,
        )
        builder.add_policy(policy)
    builder.write()






@given('policy "{policy_name}" grants the following folder policies:')
def step_policy_grants_folder_policies(
    context: Context, policy_name: str
) -> None:
    """Rich folder-policy rows: mode, default, five capability booleans, inline rules."""
    from support.policy_builder import SenderRule

    builder = _ensure_builder(context)
    for row in context.table:
        account_id = row["account"] if "account" in row.headings else None
        if account_id is None:
            if len(builder.accounts) != 1:
                raise AssertionError(
                    "Omitting the account column requires exactly one "
                    f"registered account; found {len(builder.accounts)}"
                )
            account_id = builder.accounts[0].id
        folder = builder.folder(
            policy_name=policy_name,
            account_id=account_id,
            path=row["folder"],
            mode=row["mode"],
            default=row["default"],
            mark_seen=_parse_bool(row["mark_seen"]) if "mark_seen" in row.headings else False,
            mark_tagged=_parse_bool(row["mark_tagged"]) if "mark_tagged" in row.headings else False,
            move_out=_parse_bool(row["move_out"]) if "move_out" in row.headings else False,
            copy_out=_parse_bool(row["copy_out"]) if "copy_out" in row.headings else False,
            accept_incoming=_parse_bool(row["accept_incoming"]) if "accept_incoming" in row.headings else False,
            draft_append=_parse_bool(row["draft_append"]) if "draft_append" in row.headings else False,
            draft_edit=_parse_bool(row["draft_edit"]) if "draft_edit" in row.headings else False,
        )
        if "draft_recipient_domains" in row.headings and row["draft_recipient_domains"].strip():
            import json
            raw_domains = row["draft_recipient_domains"].strip()
            domains = json.loads(raw_domains) if raw_domains.startswith("[") else [d.strip() for d in raw_domains.split(",") if d.strip()]
            folder.draft_recipients = {
                "allowed_domains": domains,
                "allowed_addresses": (folder.draft_recipients or {}).get("allowed_addresses", []),
            }
        if "draft_recipient_addresses" in row.headings and row["draft_recipient_addresses"].strip():
            import json
            raw_addrs = row["draft_recipient_addresses"].strip()
            addrs = json.loads(raw_addrs) if raw_addrs.startswith("[") else [a.strip() for a in raw_addrs.split(",") if a.strip()]
            folder.draft_recipients = {
                "allowed_domains": (folder.draft_recipients or {}).get("allowed_domains", []),
                "allowed_addresses": addrs,
            }
        if "rules" in row.headings:
            rules_raw = row["rules"]
            for parsed_match, grant, cap in _parse_inline_rules(rules_raw):
                folder.rules.append(SenderRule(match=parsed_match, grant=grant, cap=cap))
    builder.write()






@given('policy "{policy_name}" grants folder:')
def step_policy_grants_folder_inline_rules(
    context: Context, policy_name: str
) -> None:
    """Variant of the above where the `rules` column is an inline mini-DSL.

    Feature-file shape:
        | folder | mode | default | rules                                  |
        | ...    | ...  | ...     | [{from_domain=hornbach.de -> FULL}]    |
        | ...    | ...  | ...     | [{from_domain=bank.de -> cap NONE}]    |
        | ...    | ...  | ...     | []                                     |

    The grammar is:
        rules       ::= '[' rule (',' rule)* ']'
        rule        ::= '{' match_term (' AND ' match_term)* ' -> ' grant_or_cap '}'
        match_term  ::= key '=' value
        grant_or_cap::= level | 'cap' level

    The step remains a thin adapter — the parser above only splits
    strings, it does not evaluate policy semantics.
    """
    _grant_folders_from_table(context, policy_name, account_id=None)


@given('policy "{policy_name}" grants folder on account "{account_id}":')
def step_policy_grants_folder_on_account(
    context: Context, policy_name: str, account_id: str
) -> None:
    """Same table shape as `grants folder:`, for the named account."""
    _grant_folders_from_table(context, policy_name, account_id=account_id)


def _grant_folders_from_table(
    context: Context, policy_name: str, account_id: str | None
) -> None:
    builder = _ensure_builder(context)
    for row in context.table:
        row_account = row.get("account") or account_id
        if row_account is None:
            policy = next((p for p in builder.policies if p.name == policy_name), None)
            if policy and len(policy.accounts) == 1:
                row_account = next(iter(policy.accounts.keys()))
            elif len(builder.accounts) == 1:
                row_account = builder.accounts[0].id
            else:
                raise AssertionError(
                    "policy grants folder: without an `account` column "
                    f"requires exactly one registered account or one granted account on {policy_name!r}; found "
                    f"{len(builder.accounts)} accounts."
                )
        caps: dict[str, bool] = {}
        for cap_key in ("mark_seen", "mark_tagged", "move_out", "copy_out", "accept_incoming", "draft_append", "modify_message", "draft_edit"):
            if cap_key in row.headings:
                caps[cap_key] = _parse_bool(row[cap_key])
        folder = builder.folder(
            policy_name=policy_name,
            account_id=row_account,
            path=row["folder"],
            mode=row["mode"],
            default=row["default"],
            **caps,
        )
        if "draft_recipient_domains" in row.headings and row["draft_recipient_domains"].strip():
            import json
            raw_domains = row["draft_recipient_domains"].strip()
            domains = json.loads(raw_domains) if raw_domains.startswith("[") else [d.strip() for d in raw_domains.split(",") if d.strip()]
            folder.draft_recipients = {
                "allowed_domains": domains,
                "allowed_addresses": (folder.draft_recipients or {}).get("allowed_addresses", []),
            }
        if "draft_recipient_addresses" in row.headings and row["draft_recipient_addresses"].strip():
            import json
            raw_addrs = row["draft_recipient_addresses"].strip()
            addrs = json.loads(raw_addrs) if raw_addrs.startswith("[") else [a.strip() for a in raw_addrs.split(",") if a.strip()]
            folder.draft_recipients = {
                "allowed_domains": (folder.draft_recipients or {}).get("allowed_domains", []),
                "allowed_addresses": addrs,
            }
        rules_raw = row["rules"] if "rules" in row.headings else row.get("rule", "[]")
        # The `rule` (singular) column holds a single rule without
        # the surrounding [...] brackets; promote it to the list shape
        # the parser expects.
        if "rule" in row.headings and "rules" not in row.headings:
            rule_body = rules_raw.strip()
            if not rule_body.startswith("["):
                rules_raw = "[{" + rule_body + "}]" if not rule_body.startswith("{") else "[" + rule_body + "]"
        mode = row.get("mode")
        for parsed_match, grant, cap in _parse_inline_rules(rules_raw, mode=mode):
            from support.policy_builder import SenderRule

            folder.rules.append(SenderRule(match=parsed_match, grant=grant, cap=cap))
    builder.write()


def _parse_inline_rules(
    raw: str,
    mode: str | None = None,
) -> list[tuple[dict[str, object], str | None, str | None]]:
    """Parse `[{k=v AND k2=v2 -> FULL}, {k=v -> cap NONE}]`.

    Returns a list of (match_dict, grant, cap). Rules that use the
    `cap X` syntax populate `cap`; rules that name a bare level
    populate `grant` (or `cap` if mode is blacklist). Empty-list input yields an empty result.
    """
    text = raw.strip()
    if text in ("", "[]"):
        return []
    if text.startswith('[{"'):
        return _parse_json_rules(raw)
    if not (text.startswith("[") and text.endswith("]")):
        raise AssertionError(f"Inline rules must be wrapped in []: {raw!r}")
    inner = text[1:-1].strip()
    results: list[tuple[dict[str, object], str | None, str | None]] = []
    for piece in _split_top_level(inner, ","):
        piece = piece.strip()
        if not (piece.startswith("{") and piece.endswith("}")):
            raise AssertionError(f"Rule must be wrapped in {{}}: {piece!r}")
        rule_body = piece[1:-1].strip()
        match_part, sep, rhs_part = rule_body.partition(" -> ")
        if not sep:
            raise AssertionError(f"Rule must contain ' -> ': {piece!r}")
        match_dict: dict[str, object] = {}
        for term in match_part.split(" AND "):
            term = term.strip()
            key, _, value = term.partition("=")
            if not key or not value:
                raise AssertionError(
                    f"Match term must be key=value: {term!r} in {piece!r}"
                )
            match_dict[key.strip()] = _coerce(value.strip())
        rhs_part = rhs_part.strip()
        if rhs_part.startswith("cap "):
            results.append((match_dict, None, rhs_part[4:].strip()))
        elif rhs_part.startswith("grant "):
            results.append((match_dict, rhs_part[6:].strip(), None))
        elif mode == "blacklist":
            results.append((match_dict, None, rhs_part))
        else:
            results.append((match_dict, rhs_part, None))
    return results


def _parse_json_rules(
    raw: str,
) -> list[tuple[dict[str, object], str | None, str | None]]:
    """Parse the rules column written as the policy file writes rules:
    `[{"match": {"from_domain": "x.org"}, "grant": "ENVELOPE"}]`, each
    rule with `match` and one of `grant` / `cap`."""
    import json as _json

    try:
        parsed = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise AssertionError(f"Rules column is not valid JSON: {raw!r} ({exc})") from exc
    if not isinstance(parsed, list):
        raise AssertionError(f"Rules column must be a JSON list: {raw!r}")
    results: list[tuple[dict[str, object], str | None, str | None]] = []
    for rule in parsed:
        if (
            not isinstance(rule, dict)
            or not isinstance(rule.get("match"), dict)
            or set(rule) - {"match", "grant", "cap"}
            or ("grant" in rule) == ("cap" in rule)
        ):
            raise AssertionError(
                f"Rule must be an object with `match` and either `grant` or `cap`: {rule!r}"
            )
        results.append((dict(rule["match"]), rule.get("grant"), rule.get("cap")))
    return results


def _split_top_level(text: str, sep: str) -> list[str]:
    depth = 0
    parts: list[str] = []
    current: list[str] = []
    for char in text:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if char == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    tail = "".join(current)
    if tail:
        parts.append(tail)
    return parts


def _parse_bool(value: str) -> bool:
    if value.strip().lower() in ("true", "yes", "1"):
        return True
    if value.strip().lower() in ("false", "no", "0"):
        return False
    raise ValueError(f"Cannot parse boolean: {value!r}")


def _parse_flags(raw: str) -> list[str]:
    """Parse a flag list like ``[\\Flagged]`` or ``[\\Seen, \\Flagged]``."""
    raw = raw.strip()
    if raw in ("[]", ""):
        return []
    inner = raw.strip("[]")
    return [f.strip() for f in inner.split(",") if f.strip()]


def _coerce(value: str) -> object:
    import json
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        try:
            return json.loads(value)
        except Exception:
            pass
    try:
        return int(value)
    except ValueError:
        pass
    return value


# The two message-seeding steps differ only by the optional
# `of "<account>"` qualifier, and parse's default placeholder matches
# are greedy enough that the two conflict. Switch to explicit regex
# for this block so the two shapes are unambiguous.
use_step_matcher("re")


@given(r'the folder "(?P<folder>[^"]+)" holds a message with:')
@given(r'the folder "(?P<folder>[^"]+)" also holds a message with:')
def step_folder_holds_message_implicit(context: Context, folder: str) -> None:
    """Seed a message into `folder`. Accepts the `account:folder`
    prefix form used by cross-account scenarios."""
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _find_account_for_folder(context, folder)
    _seed_message(context, account_id, folder)


@given(
    r'the folder "(?P<folder>[^"]+)" of "(?P<account_id>[^"]+)" '
    r"holds a message with:"
)
def step_folder_holds_message_explicit(
    context: Context, folder: str, account_id: str
) -> None:
    _seed_message(context, account_id, folder)


# Switch back so later steps in this file use the default parse matcher.
use_step_matcher("parse")


use_step_matcher("re")


@given(r'the folder "(?P<folder>[^"]+)" is reset to hold messages:')
@when(r'the folder "(?P<folder>[^"]+)" is reset to hold messages:')
def step_folder_reset_hold_messages(context: Context, folder: str) -> None:
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _find_account_for_folder(context, folder)
    instance, user = resolve_account(account_id)

    msg_uids = getattr(context, "message_uids", {})
    context.staged_messages = [
        s for s in getattr(context, "staged_messages", [])
        if not (s.get("_account_id") == account_id and s.get("_folder") == folder)
    ]
    context.message_uids = {
        k: v for k, v in msg_uids.items()
        if not (isinstance(k, tuple) and len(k) == 3 and k[0] == account_id and k[1] == folder)
    }

    if instance == "mock-gmail":
        state = getattr(context, "gmail_state", None)
        if state is not None:
            for _, msg in list(state.messages_in_folder(folder)):
                state.remove_message_from_folder(folder, msg)
    else:
        context.imap.empty_folder(instance, user, folder)

    _seed_message(context, account_id, folder)
    flush_staged_messages(context)


@given(r'additional messages are seeded into folder "(?P<folder>[^"]+)":')
@when(r'additional messages are seeded into folder "(?P<folder>[^"]+)":')
def step_additional_messages_seeded(context: Context, folder: str) -> None:
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _find_account_for_folder(context, folder)
    _seed_message(context, account_id, folder)
    flush_staged_messages(context)


@when(r'the trace sink is cleared and additional messages are seeded into folder "(?P<folder>[^"]+)":')
def step_trace_sink_cleared_and_additional_messages_seeded(context: Context, folder: str) -> None:
    collector = getattr(context, "trace_collector", None)
    if collector is not None:
        collector.sink.clear()
    step_additional_messages_seeded(context, folder)


@when(r'the trace sink is cleared and the folder "(?P<folder>[^"]+)" is reset to hold messages:')
def step_trace_sink_cleared_and_folder_reset(context: Context, folder: str) -> None:
    collector = getattr(context, "trace_collector", None)
    if collector is not None:
        collector.sink.clear()
    step_folder_reset_hold_messages(context, folder)


@given(r'the folder "(?P<folder>[^"]+)" holds messages:')
def step_folder_holds_messages_plural(context: Context, folder: str) -> None:
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _find_account_for_folder(context, folder)
    _seed_message(context, account_id, folder)










use_step_matcher("parse")


@given('the folder "{folder}" holds a verbatim message:')
def step_folder_holds_verbatim_message(context: Context, folder: str) -> None:
    """APPEND the DocString exactly as written, byte for byte.

    Unlike the column-driven seeding steps this one synthesises
    nothing, so a scenario about a message that lacks a particular
    header really gets a message without it.
    """
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _find_account_for_folder(context, folder)
    instance, user = resolve_account(account_id)
    context.imap.create_folder(instance, user, folder)
    context.imap.append_verbatim(
        instance, user, folder, (context.text or "").encode("utf-8")
    )


@given('the folder "{folder}" is reset to hold a verbatim message:')
def step_folder_reset_holds_verbatim_message(context: Context, folder: str) -> None:
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _find_account_for_folder(context, folder)
    instance, user = resolve_account(account_id)
    context.imap.create_folder(instance, user, folder)
    context.imap.empty_folder(instance, user, folder)
    context.imap.append_verbatim(
        instance, user, folder, (context.text or "").encode("utf-8")
    )


def _split_account_folder(context: Context, folder_expr: str) -> tuple[str, str]:
    if ":" in folder_expr:
        acct, _, f = folder_expr.partition(":")
        builder = getattr(context, "policy_builder", None)
        if builder and any(a.id == acct for a in builder.accounts):
            return acct, f
        from support.imap_fixture import _ACCOUNT_ID_TO_INSTANCE_USER
        if acct in _ACCOUNT_ID_TO_INSTANCE_USER:
            return acct, f
    return _find_account_for_folder(context, folder_expr), folder_expr


def _find_account_for_folder(context: Context, folder: str) -> str:
    """Look up which configured account declares this folder path.

    Scans the builder's policies (not IMAP) so the lookup is
    deterministic and independent of server state. Ambiguity is a
    fatal error: the feature file must then use the explicit
    `of "<account>"` form.
    """
    builder = context.policy_builder
    if ":" in folder:
        prefix, _, _suffix = folder.partition(":")
        if any(a.id == prefix for a in builder.accounts):
            return prefix
        from support.imap_fixture import _ACCOUNT_ID_TO_INSTANCE_USER
        if prefix in _ACCOUNT_ID_TO_INSTANCE_USER:
            return prefix
    hits: list[str] = []
    for policy in builder.policies:
        for account_id, folder_list in policy.accounts.items():
            if any(fp.path == folder for fp in folder_list) and account_id not in hits:
                hits.append(account_id)
    if not hits:
        # Also check accounts that were registered without any policy
        # folder declaration (i.e. accounts that exist in the fixture but
        # intentionally carry no policy — scenario 4 deals with that).
        for account in builder.accounts:
            if account.id in hits:
                continue
            instance, user = resolve_account(account.id)
            if instance == "mock-gmail":
                gmail_state = getattr(context, "gmail_state", None)
                folders = gmail_state.all_folders() if gmail_state else []
            else:
                folders = context.imap.list_folders(instance, user)
            if folder in folders and account.id not in hits:
                hits.append(account.id)
    if not hits:
        raise AssertionError(
            f"No configured account declares folder {folder!r}. Use the "
            f"explicit form: `Given the folder \"{folder}\" of \"...\" "
            "holds a message with:`"
        )
    if len(hits) > 1:
        raise AssertionError(
            f"Folder {folder!r} is declared on multiple accounts "
            f"{hits}; the scenario must use the explicit "
            f"`of \"<account>\"` form to disambiguate."
        )
    return hits[0]


def _seed_message(context: Context, account_id: str, folder: str) -> None:
    """Stage one or more messages for deferred seeding.

    Seeding is deferred so follow-up steps such as "And the message has
    attachment X" can modify the staged record before it reaches IMAP.
    The actual seed happens on first `When` step via
    `flush_staged_messages(context)`.

    Recognised optional columns (each is set verbatim as the corresponding
    RFC 5322 header on the seeded message):

      reply_to           -> Reply-To
      cc_header          -> Cc
      to_header          -> overrides `to` (allows multi-recipient seeds)
      references_header  -> References

    Empty cells in `message_id` and `date` mean "do not set this header" —
    used by reply-builder scenarios that exercise the missing_message_id
    error path and the dateless-attribution tolerant path.
    """
    context.staged_messages = getattr(context, "staged_messages", [])
    for row in context.table:
        headings = row.headings
        extra_headers: list[tuple[str, str]] = []
        if "reply_to" in headings and row["reply_to"]:
            extra_headers.append(("Reply-To", row["reply_to"]))
        if "cc_header" in headings and row["cc_header"]:
            extra_headers.append(("Cc", row["cc_header"]))
        if "references_header" in headings and row["references_header"]:
            extra_headers.append(("References", row["references_header"]))
        if "from_address" in headings and row["from_address"]:
            from_value = row["from_address"]
        elif "from" in headings and row["from"]:
            from_value = row["from"]
        else:
            raise AssertionError(
                f"Seeding table missing mandatory column 'from_address' or 'from' in {headings}"
            )

        if "to_header" in headings and row["to_header"]:
            to_value = row["to_header"]
        elif "to_address" in headings and row["to_address"]:
            to_value = row["to_address"]
        elif "to" in headings and row["to"]:
            to_value = row["to"]
        else:
            raise AssertionError(
                f"Seeding table missing mandatory column 'to_address' or 'to' in {headings}"
            )

        if "subject" in headings and row["subject"]:
            subject_val = row["subject"]
        elif "subject_nfd_escaped" in headings and row["subject_nfd_escaped"]:
            raw_subj = row["subject_nfd_escaped"]
            subject_val = raw_subj.encode("utf-8").decode("unicode_escape")
        else:
            raise AssertionError(
                f"Seeding table missing mandatory column 'subject' in {headings}"
            )

        if "header_date" in headings:
            date_val = row["header_date"]
        elif "date" in headings and row["date"]:
            date_val = row["date"]
        elif "internal_date" in headings and row["internal_date"]:
            date_val = row["internal_date"]
        else:
            raise AssertionError(
                f"Seeding table missing mandatory column 'date' in {headings}"
            )
        internal_date_val = row["internal_date"] if "internal_date" in headings else None

        staged: dict[str, object] = {
            "_account_id": account_id,
            "_folder": folder,
            "uid_hint": int(row["uid"]),
            "from": from_value,
            "to": to_value,
            "subject": subject_val,
            "message_id_override": row["message_id"]
            if "message_id" in headings
            else None,
            "has_attachment": _parse_bool(row["has_attachment"])
            if "has_attachment" in headings
            else False,
            "size_hint": int(row["size_bytes"])
            if "size_bytes" in headings
            else 0,
            "date": date_val,
            "internal_date": internal_date_val,
            "flags": (
                _parse_flags(row["flags"])
                if "flags" in headings
                else (["\\Flagged"] if "flagged" in headings and _parse_bool(row["flagged"]) else [])
            ),
            "extra_attachments": [],
            "extra_headers": extra_headers,
            "body_override": None,
            "html_body": None,
            "html_only": False,
        }
        context.staged_messages.append(staged)


def flush_staged_messages(context: Context) -> None:
    """Turn the staged message list into real IMAP APPENDs."""
    import email.utils as _email_utils
    from datetime import datetime
    from email.utils import format_datetime

    staged_list = getattr(context, "staged_messages", [])
    if not staged_list:
        return
    for staged in staged_list:
        account_id = staged["_account_id"]
        folder = staged["_folder"]
        if ":" in folder and folder.startswith(f"{account_id}:"):
            folder = folder.split(":", 1)[1]
        uid_hint = staged["uid_hint"]
        instance, user = resolve_account(account_id)
        sender = staged.get("from")
        if not sender:
            raise AssertionError(f"Staged message {uid_hint} is missing mandatory 'from'")
        to_addr = staged.get("to")
        if not to_addr:
            raise AssertionError(f"Staged message {uid_hint} is missing mandatory 'to'")
        subject = staged.get("subject")
        if not subject:
            raise AssertionError(f"Staged message {uid_hint} is missing mandatory 'subject'")
        size_hint = staged["size_hint"]
        date_header: str | None = None
        omit_date = False
        date_str = staged["date"]
        if date_str == "":
            omit_date = True
        elif date_str:
            try:
                parsed = datetime.fromisoformat(date_str)
            except ValueError:
                parsed = _email_utils.parsedate_to_datetime(date_str)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            date_header = format_datetime(parsed)
        body_override = staged["body_override"]
        html_body = staged.get("html_body")
        html_only = bool(staged.get("html_only"))
        if html_body and (html_only or body_override is None):
            body = ""
        elif body_override is not None:
            body = body_override
        else:
            body = f"Body for {subject}"
        attachments: list[tuple[str, str, bytes]] = list(staged["extra_attachments"])
        extra_headers = {
            name: value for name, value in staged["extra_headers"]
        }
        omit_msgid = staged["message_id_override"] in ("(absent)", "")
        message_id = (
            None if omit_msgid
            else (staged["message_id_override"] or f"<scenario-{uid_hint}@bdd.local>")
        )

        if instance == "mock-gmail":
            from email.message import EmailMessage

            from mock_gmail.state import FOLDER_TO_LABEL, Message
            state = getattr(context, "gmail_state", None)
            if state is not None:
                state.create_folder(folder)
                email_msg = EmailMessage()
                email_msg["From"] = sender
                email_msg["To"] = to_addr
                email_msg["Subject"] = subject
                if not omit_msgid and message_id:
                    email_msg["Message-ID"] = message_id
                if not omit_date and date_header:
                    email_msg["Date"] = date_header
                for h_name, h_val in extra_headers.items():
                    email_msg[h_name] = h_val
                if html_body and not body:
                    email_msg.set_content(html_body, subtype="html")
                else:
                    email_msg.set_content(body)
                    if html_body:
                        email_msg.add_alternative(html_body, subtype="html")
                for att in attachments:
                    if isinstance(att, dict) and "payload" in att:
                        payload = att["payload"]
                        mtype, _, stype = att.get("mime_type", "application/octet-stream").partition("/")
                        fn = att.get("raw_wire_filename") or att.get("raw_8bit_filename") or "att.bin"
                        email_msg.add_attachment(payload, maintype=mtype, subtype=stype, filename=fn)
                    elif isinstance(att, tuple) and len(att) == 3:
                        fn, mime_type, payload = att
                        mtype, _, stype = mime_type.partition("/")
                        email_msg.add_attachment(payload, maintype=mtype, subtype=stype, filename=fn)
                raw_bytes = email_msg.as_bytes()
                gm_id = int(uid_hint) if (uid_hint and str(uid_hint).isdigit()) else state.new_msgid()
                labels = set()
                if folder != "[Gmail]/All Mail":
                    label = FOLDER_TO_LABEL.get(folder, folder)
                    if label != "__ALL_MAIL__":
                        labels.add(label)
                gmsg = Message(
                    gm_msgid=gm_id,
                    gm_thrid=gm_id,
                    rfc822=raw_bytes,
                    labels=labels,
                    flags=set(staged.get("flags", [])),
                    message_id=message_id,
                    from_addr=sender,
                    to_addr=to_addr,
                    subject=subject,
                    date=date_header or "",
                )
                if staged.get("internal_date"):
                    int_str = str(staged["internal_date"])
                    try:
                        parsed_int = datetime.fromisoformat(int_str)
                    except Exception:
                        parsed_int = _email_utils.parsedate_to_datetime(int_str)
                    if parsed_int.tzinfo is None:
                        parsed_int = parsed_int.replace(tzinfo=UTC)
                    gmsg.internal_date = parsed_int
                state.add_message(gmsg)
                actual_uid = int(uid_hint) if (uid_hint and str(uid_hint).isdigit()) else state._uid_maps.get(folder, {}).get(gmsg.gm_msgid, 1)
                if uid_hint and str(uid_hint).isdigit():
                    actual_uid = int(uid_hint)
                    uid_map = state._uid_maps.setdefault(folder, {})
                    uid_map[gmsg.gm_msgid] = actual_uid
                    state._uid_counters[folder] = max(state._uid_counters.get(folder, 0), actual_uid)
                context.message_uids = getattr(context, "message_uids", {})
                context.message_uids[(account_id, folder, uid_hint)] = actual_uid
                context.message_uids[(account_id, folder, actual_uid)] = actual_uid
                continue

        context.imap.create_folder(instance, user, folder)
        seeded = context.imap.seed_message(
            instance,
            user,
            folder,
            omit_message_id=omit_msgid,
            omit_date=omit_date,
            sender=sender,
            to=to_addr,
            subject=subject,
            body=body,
            html_body=html_body,
            message_id=message_id,
            date=date_header,
            flags=staged.get("flags", []),
            exact_size_bytes=size_hint if size_hint > 0 else None,
            attachments=attachments,
            inline_attachments=staged.get("inline_attachments", []),
            extra_headers=extra_headers,
            internal_date=staged.get("internal_date"),
        )
        context.message_uids = getattr(context, "message_uids", {})
        context.message_uids[(account_id, folder, uid_hint)] = seeded.uid
        context.message_uids[(account_id, folder, seeded.uid)] = seeded.uid
    context.staged_messages = []


@given('the folder "{folder}" is empty')
@given('the IMAP folder "{folder}" is empty')
def step_folder_is_empty(context: Context, folder: str) -> None:
    """Ensure `folder` exists but holds no messages.

    Accounts and folder creation are implicit via the prior IMAP
    account/folder setup; we only assert (and enforce) emptiness."""
    builder = _ensure_builder(context)
    instance = None
    user = None
    for account in builder.accounts:
        inst, usr = resolve_account(account.id)
        folders = context.imap.list_folders(inst, usr)
        if folder in folders:
            instance, user = inst, usr
            break
    if instance is None:
        # Create it under the single registered account if unambiguous.
        if len(builder.accounts) != 1:
            raise AssertionError(
                f"Folder {folder!r} not found; cannot disambiguate account."
            )
        instance, user = resolve_account(builder.accounts[0].id)
        context.imap.create_folder(instance, user, folder)
    uids = context.imap.folder_uids(instance, user, folder)
    if uids:
        raise AssertionError(
            f"IMAP folder {folder!r} on {instance}/{user} is not empty: contains UIDs {uids}"
        )




@given(
    'the folder "{folder}" holds a message with uid {uid:d} whose plain-text body '
    'is the letter "{letter}" repeated {count:d} times'
)
def step_folder_holds_message_repeated_letter_body(
    context: Context, folder: str, uid: int, letter: str, count: int
) -> None:
    """Seed one message whose text/plain body is exactly `letter * count`."""
    if len(letter) != 1:
        raise AssertionError(f"Expected a single letter, got {letter!r}")
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _find_account_for_folder(context, folder)
    _instance, user = resolve_account(account_id)
    to_addr = f"{user}@{account_id}.example" if "@" not in user else user
    context.staged_messages = getattr(context, "staged_messages", [])
    context.staged_messages.append(
        {
            "_account_id": account_id,
            "_folder": folder,
            "uid_hint": uid,
            "from": "sender@example.com",
            "to": to_addr,
            "subject": f"Message {uid}",
            "message_id_override": f"<m-{uid}@{account_id}.example>",
            "has_attachment": False,
            "size_hint": 0,
            "date": "2026-10-30T07:00:00Z",
            "flags": [],
            "extra_attachments": [],
            "extra_headers": [],
            "body_override": letter * count,
            "html_body": None,
            "html_only": False,
        }
    )




@given("the audit log directory is a fresh $TMPDIR/audit")
def step_audit_log_fresh(context: Context) -> None:
    # before_scenario already created a fresh scratch_dir/audit;
    # this step serves as human-readable assertion that it is indeed
    # fresh. Nothing to do.
    if not context.audit_dir.exists():
        context.audit_dir.mkdir(parents=True)








# Use parse matcher for the bare form; behave resolves the longer
# `... using policy "..."` step first due to registration order below.
# To avoid the ambiguity, re-declare the longer one *after* this bare
# form below.


@given("the WAL is empty")
def step_wal_is_empty(context: Context) -> None:
    path = context.wal_path
    if path.exists():
        path.unlink()


@given("the WAL retry_limit is configured to {limit:d}")
def step_wal_retry_limit(context: Context, limit: int) -> None:
    """Retry limit is a server-startup parameter; for tests we set it
    via an environment variable the server reads."""
    context.mcp_extra_env = getattr(context, "mcp_extra_env", {})
    context.mcp_extra_env["IMAP_MCP_RETRY_LIMIT"] = str(limit)
    if getattr(context, "is_test_hooks", False) and not getattr(context, "without_test_mode", False):
        context.mcp_extra_env["IMAP_MCP_TEST_MODE"] = "1"






















@given('the folder "{folder}" contains no message with uid {uid:d}')
def step_folder_has_no_uid(context: Context, folder: str, uid: int) -> None:
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _find_account_for_folder(context, folder)
    canonical_folder = folder.rstrip("/")
    if not hasattr(context, "explicit_unseeded_uids"):
        context.explicit_unseeded_uids = set()
    context.explicit_unseeded_uids.add((account_id, canonical_folder, uid))

    instance, user = resolve_account(account_id)
    if instance == "mock-gmail":
        state = getattr(context, "gmail_state", None)
        if state is not None:
            msg = state.message_by_uid(folder, uid)
            if msg is not None:
                raise AssertionError(
                    f"Folder {folder!r} on {account_id} unexpectedly contains UID {uid}"
                )
    else:
        uids = context.imap.folder_uids(instance, user, folder)
        if uid in uids:
            raise AssertionError(
                f"Folder {folder!r} on {account_id} unexpectedly contains UID {uid}"
            )














@given("the server is configured with audit:")
def step_server_with_audit_table(context: Context) -> None:
    builder = _ensure_builder(context)
    row = context.table[0]
    if "directory" in row.headings and row["directory"] != "$TMPDIR/audit":
        builder.audit_directory = Path(row["directory"])
    # else: keep the per-scenario scratch dir from before_scenario.
    if "hot_days" in row.headings:
        builder.audit_hot_days = int(row["hot_days"])
    if "warm_days" in row.headings:
        builder.audit_warm_days = int(row["warm_days"])
    if "delete_after_days" in row.headings:
        builder.audit_delete_after_days = int(row["delete_after_days"])
    builder.write()
    if not builder.callers:
        raise AssertionError("Audit configuration requires caller to be configured explicitly in scenario")
    builder.write()


@given(
    "the server is configured with audit hot_days={hot:d}, "
    "warm_days={warm:d}, delete_after_days={delete:d}"
)
def step_server_audit_inline_retention(
    context: Context, hot: int, warm: int, delete: int
) -> None:
    builder = _ensure_builder(context)
    builder.audit_hot_days = hot
    builder.audit_warm_days = warm
    builder.audit_delete_after_days = delete
    if not builder.callers:
        raise AssertionError("Audit configuration requires caller to be configured explicitly in scenario")
    builder.write()














def _now_utc_for_test(context: Context):
    """Mirror of audit._now_utc but reading the BDD-side env var.

    The harness shares `IMAP_MCP_FAKE_NOW_UTC` with the server via
    `mcp_extra_env`; for staging files we honour it locally too so
    file mtimes line up with what the server will see.
    """
    import os as _os
    from datetime import datetime as _dt

    raw = (getattr(context, "mcp_extra_env", None) or {}).get(
        "IMAP_MCP_FAKE_NOW_UTC"
    ) or _os.environ.get("IMAP_MCP_FAKE_NOW_UTC")
    if raw:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            return _dt.fromisoformat(raw).astimezone(UTC)
        except ValueError:
            pass
    return _dt.now(tz=UTC)




@when('the operator updates "{folder_path}" rules to:')
def step_operator_updates_rules(context: Context, folder_path: str) -> None:
    """Replace the rule list for the named folder, then write the
    updated YAML to disk. The next SIGHUP step makes it effective."""
    builder = _ensure_builder(context)
    from support.policy_builder import SenderRule

    target_fp = None
    for policy in builder.policies:
        for fp_list in policy.accounts.values():
            for fp in fp_list:
                if fp.path == folder_path:
                    target_fp = fp
                    break
    if target_fp is None:
        raise AssertionError(f"No folder {folder_path!r} declared in any policy")
    target_fp.rules = []
    for row in context.table:
        match_str = row["match"]
        key, _, value = match_str.partition("=")
        match = {key.strip(): value.strip()}
        grant = row.get("grant") if "grant" in row.headings else None
        cap = row.get("cap") if "cap" in row.headings else None
        target_fp.rules.append(SenderRule(match=match, grant=grant, cap=cap))
    builder.write()


@when('the operator updates folder "{folder}" in policy "{policy_name}" to:')
def step_operator_updates_folder_policy(
    context: Context, folder: str, policy_name: str
) -> None:
    builder = _ensure_builder(context)
    policy = next((p for p in builder.policies if p.name == policy_name), None)
    if policy is None:
        raise AssertionError(f"Policy {policy_name!r} not found in builder")
    target_fp = None
    for fp_list in policy.accounts.values():
        for fp in fp_list:
            if fp.path == folder:
                target_fp = fp
                break
        if target_fp is not None:
            break
    if target_fp is None:
        raise AssertionError(f"Folder {folder!r} not found in policy {policy_name!r}")
    row = context.table[0]
    if "mode" in row.headings:
        target_fp.mode = row["mode"]
    if "default" in row.headings:
        target_fp.default = row["default"]
    for cap_key in (
        "mark_seen", "mark_tagged", "move_out", "copy_out",
        "accept_incoming", "draft_append", "draft_edit"
    ):
        if cap_key in row.headings:
            setattr(target_fp, cap_key, _parse_bool(row[cap_key]))
    builder.write()


def _fetch_message_by_subject(context: Context, qualified: str, subject: str):
    import email
    import email.policy
    account_id, folder = _split_account_folder(context, qualified)
    instance, user = resolve_account(account_id)
    if instance == "mock-gmail":
        state = getattr(context, "gmail_state", None)
        if state is None:
            raise AssertionError("mock-gmail state unavailable")
        for msg in state.messages:
            if msg.subject == subject:
                return email.message_from_bytes(msg.rfc822, policy=email.policy.default)
        raise AssertionError(f"No message with subject {subject!r} found in {qualified}")
    else:
        conn = context.imap.connect(instance, user)
        conn.select(context.imap.to_wire(instance, user, folder))
        status, data = conn.uid("SEARCH", None, f'SUBJECT "{subject}"')
        if status != "OK" or not data or not data[0]:
            raise AssertionError(f"No message with subject {subject!r} found in {qualified}")
        uids = [int(u) for u in data[0].split()]
        return context.imap.fetch_message(instance, user, folder, uids[-1])


def _get_plain_text_body(msg) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
    payload = msg.get_payload(decode=True)
    if isinstance(payload, bytes):
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    return str(msg.get_payload() or "")


@then('a direct IMAP FETCH of the message with subject "{subject}" in "{qualified}" shows decoded plain text "{expected_body}"')
def step_direct_imap_fetch_shows_plain_text(
    context: Context, subject: str, qualified: str, expected_body: str
) -> None:
    msg = _fetch_message_by_subject(context, qualified, subject)
    actual_text = _get_plain_text_body(msg).strip()
    if actual_text != expected_body.strip():
        raise AssertionError(
            f"Expected plain text {expected_body!r}, got {actual_text!r}"
        )




@when("the operator replaces the policy file with:")
@when(
    "the operator replaces the policy file to contain a whitelist folder "
    "with a non-NONE default:"
)
def step_operator_replaces_policy_file(context: Context) -> None:
    """Write a docstring-supplied YAML payload directly into the
    policy file the SIGHUP reload will re-parse. Bypasses the
    PolicyBuilder so that intentionally-broken YAML can be fed.

    Starts the server first if it isn't running yet — `_ensure_mcp_client`
    calls `builder.write()` which would otherwise overwrite the
    docstring with the clean PolicyBuilder state."""
    from features.steps.mcp_steps import _ensure_mcp_client

    _ensure_mcp_client(context, "invoice-agent")
    target = context.config_dir / "policies" / "invoice-policy.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(context.text, encoding="utf-8")




@when('the operator adds to policy "{policy_name}":')
def step_operator_adds_to_policy(
    context: Context, policy_name: str
) -> None:
    builder = _ensure_builder(context)
    from support.policy_builder import SenderRule

    for row in context.table:
        account_id = row["account"]
        builder.add_policy_account(policy_name, account_id) if hasattr(
            builder, "add_policy_account"
        ) else None
        # add_policy_account doesn't exist; ensure the account list is
        # set on the policy instead.
        policy = next(p for p in builder.policies if p.name == policy_name)
        policy.accounts.setdefault(account_id, [])
        fp = builder.folder(
            policy_name=policy_name,
            account_id=account_id,
            path=row["folder"],
            mode=row["mode"],
            default=row["default"],
        )
        if "rules" in row.headings:
            for match, grant, cap in _parse_inline_rules(row["rules"]):
                fp.rules.append(SenderRule(match=match, grant=grant, cap=cap))
    builder.write()


@given('the IMAP account "{account_id}" also has folder "{folder_path}" with special-use "{special_use}"')
def step_account_also_has_folder_special_use(
    context: Context, account_id: str, folder_path: str, special_use: str
) -> None:
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    context.imap.create_folder(instance, user, folder_path)


@given('the IMAP account "{account_id}" also has folder "{folder_path}"')
def step_account_also_has_folder(
    context: Context, account_id: str, folder_path: str
) -> None:
    """Create the folder on Dovecot but do not add it to any policy
    (the next operator step adds the policy entry)."""
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    context.imap.create_folder(instance, user, folder_path)


@given('an external mail client strips header "{header}" from the draft in "{location}" while keeping X-SC-Draft-Id and X-SC-Draft-Rev')
def step_external_client_strips_header(context: Context, header: str, location: str) -> None:
    account_id, folder = _split_account_folder(context, location)
    instance, user = resolve_account(account_id)
    uids = context.imap.folder_uids(instance, user, folder)
    target_uid = None
    target_msg = None
    for u in uids:
        msg = context.imap.fetch_message(instance, user, folder, u)
        if msg.get("X-SC-Draft-Id"):
            target_uid = u
            target_msg = msg
            break
    if target_uid is None or target_msg is None:
        raise AssertionError(f"No draft found in {location} to strip header from")

    del target_msg[header]
    raw_bytes = target_msg.as_bytes()
    conn = context.imap.connect(instance, user)
    conn.select(context.imap._q(folder))
    conn.uid("store", str(target_uid), "+FLAGS", "(\\Deleted)")
    conn.expunge()
    context.imap.append_verbatim(instance, user, folder, raw_bytes)


@given('an external IMAP client duplicates the draft message in "{location}" so two copies share the returned draft_id and revision {rev:d}')
def step_external_client_duplicates_draft(
    context: Context, location: str, rev: int
) -> None:
    account_id, folder = _split_account_folder(context, location)
    instance, user = resolve_account(account_id)
    uids = context.imap.folder_uids(instance, user, folder)
    target_uid = None
    for u in uids:
        msg = context.imap.fetch_message(instance, user, folder, u)
        if msg.get("X-SC-Draft-Id") and int(msg.get("X-SC-Draft-Rev", 0)) == rev:
            target_uid = u
            break
    if target_uid is None:
        raise AssertionError(f"No draft with revision {rev} found in {location}")

    raw_bytes = context.imap.fetch_raw(instance, user, folder, target_uid)
    context.imap.append_verbatim(instance, user, folder, raw_bytes)








def _scratch_substitute(context: Context, raw: str) -> str:
    """Replace `$SCRATCH` in feature-string arguments with the per-
    scenario scratch dir. Also replaces a fake GPG fingerprint with
    the real one if the GPG-keypair step has run (the feature uses a
    hardcoded all-A's fingerprint for readability; the actual keypair
    is generated at runtime)."""
    scratch = getattr(context, "scratch_dir", None)
    out = raw
    if scratch is not None:
        out = out.replace("$SCRATCH", str(scratch)).replace("$TMPDIR", str(scratch))
    fake_fp = getattr(context, "_gpg_fake_fp", None)
    real_fp = getattr(context, "_gpg_real_fp", None)
    if fake_fp and real_fp:
        out = out.replace(fake_fp, real_fp)
    return out


@given("secret_store configuration is")
@given("secret_store configuration is:")
def step_secret_store_config(context: Context) -> None:
    """Override the PolicyBuilder's secret_store-section from a YAML
    block in the feature file. Used by `secret_store_backends.feature`
    to switch backends per-scenario."""
    import yaml

    builder = _ensure_builder(context)
    raw = _scratch_substitute(context, context.text or "")
    parsed = yaml.safe_load(raw) or {}
    backend = parsed.get("backend") or "file_dir"
    builder.secret_store_backend = backend
    path = parsed.get("path")
    builder.secret_store_path = Path(path) if path else None
    recipient = parsed.get("recipient")
    builder.secret_store_recipient = (
        _scratch_substitute(context, recipient) if recipient else None
    )
    builder.secret_store_acknowledge_plaintext = parsed.get("acknowledge_plaintext", False)
    gh = parsed.get("gnupghome")
    if gh:
        builder.secret_store_gnupghome = Path(_scratch_substitute(context, gh))
    elif backend == "gpg_file":
        # Default to whichever gnupghome the prior gpg-keypair step
        # set up. Lets feature files keep gpg_file YAML minimal.
        prior = getattr(context, "_gpg_gnupghome", None)
        if prior is not None:
            builder.secret_store_gnupghome = prior


@given("callers.yaml contains an empty callers list")
def step_callers_yaml_empty(context: Context) -> None:
    builder = _ensure_builder(context)
    builder.write()
    context.custom_callers_yaml = True
    (context.config_dir / "callers.yaml").write_text("callers: []\n", encoding="utf-8")


@given('callers.yaml defines two callers "invoice-agent" and "overview-agent"')
def step_callers_yaml_two_callers(context: Context) -> None:
    builder = _ensure_builder(context)
    builder.write()
    context.custom_callers_yaml = True
    (context.config_dir / "callers.yaml").write_text(
        "callers:\n"
        "  - id: invoice-agent\n"
        "    policy: invoice-policy\n"
        "    auth:\n"
        "      type: stdio_trusted\n"
        "  - id: overview-agent\n"
        "    policy: overview-policy\n"
        "    auth:\n"
        "      type: stdio_trusted\n",
        encoding="utf-8",
    )


@given('callers.yaml defines caller "no-auth-agent" without an auth block')
def step_callers_yaml_no_auth_block(context: Context) -> None:
    builder = _ensure_builder(context)
    builder.write()
    context.custom_callers_yaml = True
    (context.config_dir / "callers.yaml").write_text(
        "callers:\n"
        "  - id: no-auth-agent\n"
        "    policy: no-auth-policy\n",
        encoding="utf-8",
    )


@given('the server process environment does NOT include "{var_name}"')
def step_server_env_does_not_include_var(context: Context, var_name: str) -> None:
    extra = getattr(context, "mcp_extra_env", None) or {}
    extra.pop(var_name, None)
    context.mcp_extra_env = extra
    unset = getattr(context, "mcp_unset_env", []) or []
    if var_name not in unset:
        unset.append(var_name)
    context.mcp_unset_env = unset


@given('the IMAP account "{account_id}" exists with explicit user "{user}" and folders:')
def step_imap_account_exists_explicit_user(
    context: Context, account_id: str, user: str
) -> None:
    builder = _ensure_builder(context)
    builder.add_account(
        id=account_id,
        provider="imap-standard",
        host="127.0.0.1",
        port=11143,
        user=user,
        auth_type="password",
        secret_ref=f"secret://accounts/{account_id}/password",
    )
    pw_file = context.secrets_dir / "accounts" / account_id / "password"
    pw_file.parent.mkdir(parents=True, exist_ok=True)
    pw_file.write_text("test123", encoding="utf-8")
    for row in context.table:
        folder_path = row["folder path"]
        context.imap.create_folder("imap-a", user, folder_path)
    builder.write()


@given('the secret store holds the wrong password "{password}" for account "{account_id}"')
def step_secret_store_wrong_password(
    context: Context, password: str, account_id: str
) -> None:
    pw_file = context.secrets_dir / "accounts" / account_id / "password"
    pw_file.parent.mkdir(parents=True, exist_ok=True)
    pw_file.write_text(password, encoding="utf-8")


@given('a passphrase-protected GPG keypair is generated in "{gnupghome_raw}" with passphrase "{passphrase}"')
def step_gpg_keypair_generated(context: Context, gnupghome_raw: str, passphrase: str) -> None:
    gnupghome = Path(_scratch_substitute(context, gnupghome_raw))
    gnupghome.mkdir(parents=True, exist_ok=True)
    gnupghome.chmod(0o700)
    (gnupghome / "gpg-agent.conf").write_text(
        "allow-loopback-pinentry\ndefault-cache-ttl 0\nmax-cache-ttl 0\n",
        encoding="utf-8",
    )
    batch = (
        "Key-Type: RSA\n"
        "Key-Length: 2048\n"
        "Subkey-Type: RSA\n"
        "Subkey-Length: 2048\n"
        "Name-Real: imap-mcp BDD test\n"
        "Name-Email: bdd@imap-mcp.invalid\n"
        "Expire-Date: 0\n"
        f"Passphrase: {passphrase}\n"
        "%commit\n"
    )
    env = dict(os.environ)
    env["GNUPGHOME"] = str(gnupghome)
    subprocess.run(
        ["gpg", "--batch", "--quiet", "--gen-key", "--pinentry-mode", "loopback"],
        input=batch.encode("utf-8"),
        check=True,
        env=env,
        capture_output=True,
        timeout=30,
    )
    list_keys = subprocess.run(
        ["gpg", "--list-keys", "--with-colons", "bdd@imap-mcp.invalid"],
        check=True,
        env=env,
        capture_output=True,
        timeout=10,
    )
    real_fp = ""
    for line in list_keys.stdout.decode("ascii", errors="replace").splitlines():
        parts = line.split(":")
        if parts[0] == "fpr" and len(parts) > 9:
            real_fp = parts[9]
            break
    if not real_fp:
        raise AssertionError("Could not extract GPG fingerprint")
    context.gpg_key_fingerprint = real_fp
    context._gpg_gnupghome = gnupghome


@given(
    'secret_store configuration uses backend "gpg_file" at "{path_raw}" with gnupghome "{gnupghome_raw}", '
    'the generated key fingerprint, and passphrase "{passphrase}"'
)
def step_secret_store_uses_backend_gpg(
    context: Context, path_raw: str, gnupghome_raw: str, passphrase: str
) -> None:
    builder = _ensure_builder(context)
    builder.secret_store_backend = "gpg_file"
    builder.secret_store_path = Path(_scratch_substitute(context, path_raw))
    builder.secret_store_gnupghome = Path(_scratch_substitute(context, gnupghome_raw))
    builder.secret_store_recipient = getattr(context, "gpg_key_fingerprint", "unknown")
    builder.secret_store_passphrase = passphrase
    builder.write()


@given('the plaintext "{plaintext}" is encrypted with GPG for the generated key into "{target_file_raw}"')
def step_encrypt_plaintext_gpg(context: Context, plaintext: str, target_file_raw: str) -> None:
    target = Path(_scratch_substitute(context, target_file_raw))
    target.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["GNUPGHOME"] = str(context._gpg_gnupghome)
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--yes",
            "--homedir", str(context._gpg_gnupghome),
            "--recipient", getattr(context, "gpg_key_fingerprint", "bdd@imap-mcp.invalid"),
            "--encrypt",
            "--output", str(target),
        ],
        input=plaintext.encode("utf-8"),
        env=env,
        check=True,
        capture_output=True,
    )


@given('the file "{path}" contains the exact bytes "{value}"')
def step_file_contains_bytes(context: Context, path: str, value: str) -> None:
    target = Path(_scratch_substitute(context, path))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(value.encode("utf-8"))
    if target.name == "refresh_token":
        oauth_client = getattr(context, "mock_oauth_client", None)
        if oauth_client is not None:
            oauth_client.register_refresh_token(value.strip())


@given('no file exists at "{path}"')
def step_no_file_exists(context: Context, path: str) -> None:
    target = Path(_scratch_substitute(context, path))
    if target.exists():
        target.unlink()


@given("the server process environment includes")
@given("the server process environment includes:")
def step_server_process_env_includes(context: Context) -> None:
    extra = getattr(context, "mcp_extra_env", None) or {}
    context.mcp_extra_env = extra
    for row in context.table:
        extra[row["name"]] = row["value"]














def _start_imap_proxy(
    context: Context,
    account_id: str,
    *,
    strip_capabilities: list[str] | None = None,
    uidvalidity_change_after: str | None = None,
    uidvalidity_new_value: str | None = None,
    inject_failure_on: list[dict] | None = None,
    delay_command_seconds: dict | None = None,
    login_credentials: dict[str, str] | None = None,
    refuse_connections: bool = False,
) -> int:
    """Spawn the BDD MITM proxy in front of the Dovecot instance for
    `account_id` and rewire the registered Account to point at it.

    Returns the proxy's listening port. Idempotent within a scenario:
    repeated calls for the SAME account_id merge new config keys into
    the existing config file and reuse the running proxy. Calls for
    DIFFERENT account_ids start additional proxy subprocesses (one per
    account) so multi-account fault scenarios work.

    `refuse_connections=True` is the special case: no proxy is started.
    A free port is picked and released, and the account is rewired to
    that port so the server's TCP connect fails with ECONNREFUSED."""
    import json as _json
    import os
    import socket
    import subprocess
    import sys
    import time

    from support.imap_fixture import resolve_account

    builder = _ensure_builder(context)
    if not any(a.id == account_id for a in builder.accounts):
        raise AssertionError(f"Account {account_id!r} has not been declared before proxy setup")

    instance, _user = resolve_account(account_id)
    upstream_host, upstream_port = context.imap_instances[instance]

    log_path = context.scratch_dir / f"imap-proxy-{account_id}.log"
    connection_log_path = context.scratch_dir / f"imap-proxy-{account_id}-connections.log"
    config_path = context.scratch_dir / f"imap-proxy-{account_id}.json"

    # Merge new keys into any existing config file for this account.
    if config_path.exists():
        try:
            config = _json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    else:
        config = {}
    config.setdefault("upstream_host", upstream_host)
    config.setdefault("upstream_port", upstream_port)
    config.setdefault("command_log_path", str(log_path))
    config.setdefault("connection_log_path", str(connection_log_path))
    if strip_capabilities is not None:
        config["strip_capabilities"] = list(strip_capabilities)
    config.setdefault("strip_capabilities", [])
    if uidvalidity_change_after is not None:
        config["uidvalidity_change_after"] = uidvalidity_change_after
    config.setdefault("uidvalidity_change_after", "")
    if uidvalidity_new_value is not None:
        config["uidvalidity_new_value"] = uidvalidity_new_value
    config.setdefault("uidvalidity_new_value", "")
    if inject_failure_on is not None:
        existing = list(config.get("inject_failure_on", []) or [])
        existing.extend(inject_failure_on)
        config["inject_failure_on"] = existing
    config.setdefault("inject_failure_on", [])
    if delay_command_seconds is not None:
        config["delay_command_seconds"] = delay_command_seconds
    if login_credentials is not None:
        config.setdefault("login_credentials", {}).update(login_credentials)
    config_path.write_text(_json.dumps(config), encoding="utf-8")

    procs = getattr(context, "imap_proxy_procs", None)
    if procs is None:
        procs = {}
        context.imap_proxy_procs = procs
    ports = getattr(context, "imap_proxy_ports", None)
    if ports is None:
        ports = {}
        context.imap_proxy_ports = ports
    if not hasattr(context, "imap_proxy_log_paths"):
        context.imap_proxy_log_paths = {}
    context.imap_proxy_log_paths[account_id] = log_path
    if not hasattr(context, "imap_proxy_connection_log_paths"):
        context.imap_proxy_connection_log_paths = {}
    context.imap_proxy_connection_log_paths[account_id] = Path(config["connection_log_path"])

    # Refuse-mode: pick a port, don't bind anything to it, rewire the
    # account. `connect()` to a port nothing listens on returns
    # ECONNREFUSED on Linux — exactly the wire-level signal the saga
    # maps to `target_unreachable`.
    if refuse_connections:
        if account_id not in ports:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                ports[account_id] = int(probe.getsockname()[1])
        for account in builder.accounts:
            if account.id == account_id:
                account.port = ports[account_id]
                break
        builder.write()
        return ports[account_id]

    proxy_proc = procs.get(account_id)
    if proxy_proc is None or proxy_proc.poll() is not None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            proxy_port = int(probe.getsockname()[1])
        cmd = [
            sys.executable, "-u", "-m", "support.imap_proxy",
            "--host", "127.0.0.1",
            "--port", str(proxy_port),
            "--config", str(config_path),
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(context.bdd_root)
        proc = subprocess.Popen(
            cmd, cwd=context.bdd_root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            line = proc.stdout.readline() if proc.stdout else b""
            if not line:
                if proc.poll() is not None:
                    err = b""
                    if proc.stderr is not None:
                        try:
                            err = proc.stderr.read()
                        except Exception:
                            err = b""
                    raise AssertionError(
                        f"imap_proxy exited prematurely (rc={proc.returncode}); "
                        f"stderr={err!r}"
                    )
                continue
            if line.startswith(b"LISTEN"):
                break
        else:
            proc.terminate()
            raise AssertionError("imap_proxy did not announce LISTEN within 5s")
        procs[account_id] = proc
        ports[account_id] = proxy_port

    proxy_port = ports[account_id]
    for account in builder.accounts:
        if account.id == account_id:
            account.port = proxy_port
            break
    builder.write()
    return proxy_port


_COMMAND_LOG_WITNESS = re.compile(
    r'the IMAP (?:command log for|connections opened for account) "([^"]+)"'
)


def capture_witnessed_command_logs(context: Context) -> None:
    """Put the pass-through proxy in front of every Dovecot-backed account
    whose IMAP command log or connection count the current scenario
    witnesses.

    Called once, just before the server process starts: the proxy can
    only record what passes through it, so it must be wired into the
    account before the server's first connection. Accounts already
    behind a proxy keep it; mock-gmail accounts are skipped, the mock
    records its own command log.
    """
    scenario = getattr(context, "scenario", None)
    if scenario is None:
        return
    for step in scenario.steps:
        match = _COMMAND_LOG_WITNESS.search(step.name)
        if match is None:
            continue
        account_id = match.group(1)
        if account_id in (getattr(context, "imap_proxy_ports", None) or {}):
            continue
        instance, _user = resolve_account(account_id)
        if instance == "mock-gmail":
            continue
        _start_imap_proxy(context, account_id)






def _arm_crash(context: Context, at: str) -> None:
    """Prime IMAP_MCP_CRASH_AT for the server that runs the next call.

    The hook is read once at process start (ADR 0023), so a server that
    an earlier Given already spawned would ignore it. Stopping it here
    makes the next tool call spawn a fresh one with the hook armed;
    mailbox and WAL live on disk and survive that restart.
    """
    context.mcp_extra_env = getattr(context, "mcp_extra_env", {})
    context.mcp_extra_env["IMAP_MCP_CRASH_AT"] = at
    if getattr(context, "is_test_hooks", False) and not getattr(context, "without_test_mode", False):
        context.mcp_extra_env["IMAP_MCP_TEST_MODE"] = "1"
    context.crash_expected = True
    client = getattr(context, "mcp", None)
    if client is not None:
        client.close()
        context.mcp = None
































@given("{caller_id} completes an Initialize handshake successfully")
def step_caller_completes_initialize(context: Context, caller_id: str) -> None:
    client = getattr(context, "mcp", None)
    if client is not None and not getattr(client, "_initialized", False):
        client._initialize()
        return
    from features.steps.mcp_steps import _ensure_mcp_client

    _ensure_mcp_client(context, caller_id)


use_step_matcher("re")


@given(
    r'the server is configured with caller "(?P<caller_id>[^"]+)" '
    r'using policy "(?P<policy_name>[^"]+)"'
)
def step_server_configured_with_caller_inline(
    context: Context, caller_id: str, policy_name: str
) -> None:
    builder = _ensure_builder(context)
    builder.add_caller(id=caller_id, policy=policy_name, auth_type="stdio_trusted")
    builder.add_policy(policy_name)
    builder.write()




use_step_matcher("parse")




@given('policy "{policy_name}" grants account "{account_id}"')
def step_policy_grants_account_inline(
    context: Context, policy_name: str, account_id: str
) -> None:
    builder = _ensure_builder(context)
    policy = next((p for p in builder.policies if p.name == policy_name), None)
    if policy is None:
        policy = builder.add_policy(policy_name)
    policy.accounts.setdefault(account_id, [])
    builder.write()


@given('policy "{policy_name}" folder defaults for "{folder}" are:')
def step_policy_folder_defaults(
    context: Context, policy_name: str, folder: str
) -> None:
    builder = _ensure_builder(context)
    row = context.table[0]
    if len(builder.accounts) != 1:
        raise AssertionError(
            "This step assumes exactly one registered account; got "
            f"{len(builder.accounts)}."
        )
    account_id = builder.accounts[0].id
    kwargs: dict[str, object] = {
        "policy_name": policy_name,
        "account_id": account_id,
        "path": folder,
        "mode": row["mode"],
        "default": row["default"],
    }
    for cap in ("mark_seen", "mark_tagged", "move_out", "accept_incoming", "draft_append"):
        if cap in row.headings:
            kwargs[cap] = row[cap].strip().lower() == "true"
    builder.folder(**kwargs)
    builder.write()


@given('policy "{policy_name}" sets folder "{folder}" rules to:')
def step_policy_sets_folder_rules(
    context: Context, policy_name: str, folder: str
) -> None:
    """Replace the sender rules of an already-declared folder."""
    from support.policy_builder import SenderRule

    builder = _ensure_builder(context)
    policy = next((p for p in builder.policies if p.name == policy_name), None)
    if policy is None:
        raise AssertionError(
            f"Policy {policy_name!r} not declared before setting folder rules."
        )
    target_folder = None
    for folder_list in policy.accounts.values():
        for fp in folder_list:
            if fp.path == folder:
                target_folder = fp
                break
        if target_folder is not None:
            break
    if target_folder is None:
        raise AssertionError(
            f"Folder {folder!r} not declared in policy {policy_name!r}."
        )
    target_folder.rules.clear()
    for row in context.table:
        match_expression = row["match"]
        grant = row.get("grant")
        cap = row.get("cap")
        if " AND " in match_expression:
            terms = match_expression.split(" AND ")
        else:
            terms = [match_expression]
        match: dict[str, object] = {}
        for term in terms:
            term = term.strip()
            key, _, value = term.partition("=")
            match[key.strip()] = _coerce(value.strip())
        target_folder.rules.append(
            SenderRule(
                match=match,
                grant=grant if grant else None,
                cap=cap if cap else None,
            )
        )
    builder.write()


def _find_staged_by_uid(
    context: Context, uid: int, account: str | None = None, folder: str | None = None
) -> dict[str, Any]:
    staged_list = getattr(context, "staged_messages", [])
    for staged in staged_list:
        if staged.get("uid_hint") == uid:
            if account and staged.get("_account_id") != account:
                continue
            if folder and staged.get("_folder") != folder:
                continue
            return staged
    if staged_list:
        return staged_list[-1]
    raise AssertionError(f"No staged message found for uid {uid}")


@given(
    'the message at uid {uid:d} has attachment "{filename}" of type "{mime_type}" '
    'with content byte "{byte}" repeated {count:d} times'
)
def step_msg_uid_attachment_repeated(
    context: Context, uid: int, filename: str, mime_type: str, byte: str, count: int
) -> None:
    staged = _find_staged_by_uid(context, uid)
    payload = byte.encode("latin1") * count
    staged["extra_attachments"].append((filename, mime_type, payload))


@given(
    'the message at uid {uid:d} has an attachment whose original filename is '
    '{count:d} "{char}" characters plus "{ext}", mime type "{mime_type}", '
    'content byte "{byte}" repeated {repeat:d} times'
)
def step_msg_uid_attachment_long_name(
    context: Context, uid: int, count: int, char: str, ext: str, mime_type: str, byte: str, repeat: int
) -> None:
    filename = (char * count) + ext
    staged = _find_staged_by_uid(context, uid)
    payload = byte.encode("latin1") * repeat
    staged["extra_attachments"].append((filename, mime_type, payload))


@given(
    'the message at uid {uid:d} has attachment with raw wire filename "{raw_wire_filename}" '
    'of type "{mime_type}" with content byte "{byte}" repeated {repeat:d} times'
)
def step_msg_uid_attachment_raw_wire(
    context: Context, uid: int, raw_wire_filename: str, mime_type: str, byte: str, repeat: int
) -> None:
    staged = _find_staged_by_uid(context, uid)
    payload = byte.encode("latin1") * repeat
    staged["extra_attachments"].append((raw_wire_filename, mime_type, payload))


@given(
    'the message at uid {uid:d} has attachment with raw 8-bit wire filename "{filename}" '
    'of type "{mime_type}" with content byte "{byte}" repeated {repeat:d} times'
)
def step_msg_uid_attachment_raw_8bit(
    context: Context, uid: int, filename: str, mime_type: str, byte: str, repeat: int
) -> None:
    staged = _find_staged_by_uid(context, uid)
    payload = byte.encode("latin1") * repeat
    staged["extra_attachments"].append((filename, mime_type, payload))


@given(
    'the message at uid {uid:d} on "{account}:{folder}" has attachment "{filename}" '
    'of type "{mime_type}" with content byte "{byte}" repeated {count:d} times'
)
def step_msg_uid_on_account_attachment(
    context: Context, uid: int, account: str, folder: str, filename: str, mime_type: str, byte: str, count: int
) -> None:
    try:
        staged = _find_staged_by_uid(context, uid, account, folder)
        staged["extra_attachments"].append((filename, mime_type, byte.encode("latin1") * count))
        return
    except AssertionError:
        pass
    gmail_state = getattr(context, "gmail_state", None)
    if gmail_state is not None:
        msg = gmail_state.message_by_uid(folder, uid)
        if msg is not None:
            import email
            payload = byte.encode("latin1") * count
            mime_msg = email.message_from_bytes(msg.rfc822)
            maintype, _, subtype = mime_type.partition("/")
            mime_msg.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)
            msg.rfc822 = mime_msg.as_bytes()
            return
    staged = _find_staged_by_uid(context, uid, account, folder)
    staged["extra_attachments"].append((filename, mime_type, byte.encode("latin1") * count))


@given('the message at uid {uid:d} has headers including "{header}"')
def step_msg_uid_headers(context: Context, uid: int, header: str) -> None:
    staged = _find_staged_by_uid(context, uid)
    k, _, v = header.partition(":")
    staged["extra_headers"].append((k.strip(), v.strip()))


@given('the message at uid {uid:d} has plain text body "{body}"')
def step_msg_uid_body(context: Context, uid: int, body: str) -> None:
    staged = _find_staged_by_uid(context, uid)
    staged["body_override"] = body
















def _staged_by_uid(context: Context, uid_hint: int) -> dict[str, object]:
    staged_list = getattr(context, "staged_messages", [])
    for staged in staged_list:
        if staged["uid_hint"] == uid_hint:
            return staged
    raise AssertionError(
        f"No staged message at uid {uid_hint}. Staged uids: "
        f"{[s['uid_hint'] for s in staged_list]!r}"
    )


@given('the message at uid {uid:d} has plain text body:')
def step_message_at_uid_has_plain_body_docstring(
    context: Context, uid: int
) -> None:
    """DocString form: the multi-line body content lives in `context.text`."""
    body = context.text or ""
    _staged_by_uid(context, uid)["body_override"] = body


@given('the message at uid {uid:d} has html body "{html}"')
def step_message_at_uid_has_html_body(
    context: Context, uid: int, html: str
) -> None:
    _staged_by_uid(context, uid)["html_body"] = html




@given("the server loads a policy file containing:")
def step_server_loads_policy_file_containing(context: Context) -> None:
    """Write the inline YAML to the config directory and run the server once.

    The DocString may contain a full config tree (callers+policies) or
    just a `policies:` subtree; the step discovers which is which and
    writes the corresponding files, then spawns the server in a
    bootstrap-probe mode so that load-time validation failures surface
    as a non-zero exit with a diagnostic on stderr.
    """
    raw = context.text
    if raw is None:
        raise AssertionError(
            'step "the server loads a policy file containing" requires a DocString'
        )
    _ensure_builder(context)
    try:
        parsed = yaml.safe_load(raw) or {}
    except Exception:
        # Malformed YAML: write it directly as a policy file so server startup rejects it
        policy_name = "overview-policy"
        m = re.search(r"^\s*name:\s*(\S+)", raw, re.MULTILINE)
        if m:
            policy_name = m.group(1).strip("\"'")
        target = context.config_dir / "policies" / f"{policy_name}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(raw, encoding="utf-8")
        return

    if isinstance(parsed, dict) and "name" in parsed:
        policy_name = parsed["name"]
        target = context.config_dir / "policies" / f"{policy_name}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(raw, encoding="utf-8")
    elif isinstance(parsed, dict) and "policies" in parsed:
        for policy_name, policy_body in (parsed["policies"] or {}).items():
            target = context.config_dir / "policies" / f"{policy_name}.yaml"
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {"name": policy_name, **(policy_body or {})}
            target.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    if isinstance(parsed, dict) and "callers" in parsed:
        (context.config_dir / "callers.yaml").write_text(
            yaml.safe_dump({"callers": parsed["callers"]}, sort_keys=False), encoding="utf-8"
        )

    # accounts.yaml must exist (the Background already wrote it via
    # PolicyBuilder.write()). We deliberately do not touch it here.


@then("the server refuses to start")
def step_server_refuses_to_start(context: Context) -> None:
    """Two paths reach this assertion:

    1. A prior `Given the server process is started with transport
       "http"` step has already launched the binary and captured the
       result in `context.startup_proc` — we just inspect its exit
       code (used by caller_authentication scenarios).
    2. No prior step did so — we run a bootstrap probe inline
       (used by config-validation scenarios).
    """
    proc = getattr(context, "startup_proc", None)
    if proc is not None:
        if getattr(proc, "timed_out", False):
            raise AssertionError("Server process timed out during startup")
        rc = getattr(proc, "returncode", getattr(proc, "exit_code", None))
        if rc == 0:
            raise AssertionError(
                "Expected the server to refuse to start, but it exited 0. "
                f"Stdout: {proc.stdout!r}, stderr: {proc.stderr!r}"
            )
        context.startup_error = (proc.stderr or "") + (proc.stdout or "")
        return
    result = _run_bootstrap_probe(context)
    if result.timed_out:
        raise AssertionError("Server process timed out during startup probe")
    context.startup_error = result.stderr
    if result.exit_code == 0:
        raise AssertionError(
            "Expected the server to refuse to start, but it exited 0. "
            f"Stderr:\n{result.stderr}"
        )


@then('the startup error indicates policy "{policy_name}" folder "{folder}" as "{expected}"')
def step_startup_error_indicates_folder(
    context: Context, policy_name: str, folder: str, expected: str
) -> None:
    _assert_startup_mentions(context, folder, expected)


@then('the startup error indicates the folder "{folder}" as "{expected}"')
def step_startup_error_indicates_folder_only(
    context: Context, folder: str, expected: str
) -> None:
    _assert_startup_mentions(context, folder, expected)


@then('the startup error indicates the rule as "{expected}"')
def step_startup_error_indicates_rule(context: Context, expected: str) -> None:
    stderr = getattr(context, "startup_error", "")
    if expected not in stderr:
        raise AssertionError(
            f"Startup error does not contain expected rule message "
            f"{expected!r}. Full stderr:\n{stderr}"
        )


@then('the startup error indicates the rule predicate "{predicate}" as "{expected}"')
def step_startup_error_indicates_predicate(
    context: Context, predicate: str, expected: str
) -> None:
    stderr = getattr(context, "startup_error", "")
    if predicate not in stderr:
        raise AssertionError(
            f"Startup error does not mention predicate {predicate!r}. "
            f"Full stderr:\n{stderr}"
        )
    if expected not in stderr:
        raise AssertionError(
            f"Startup error does not contain expected diagnostic "
            f"{expected!r}. Full stderr:\n{stderr}"
        )


def _assert_startup_mentions(
    context: Context, folder: str, expected: str
) -> None:
    stderr = getattr(context, "startup_error", "")
    if folder not in stderr:
        raise AssertionError(
            f"Startup error does not mention folder {folder!r}. "
            f"Full stderr:\n{stderr}"
        )
    if expected not in stderr:
        raise AssertionError(
            f"Startup error does not contain expected diagnostic "
            f"{expected!r}. Full stderr:\n{stderr}"
        )


def _run_bootstrap_probe(context: Context) -> BootstrapResult:
    server_binary = Path(
        os.environ.get(
            "IMAP_MCP_SERVER_BINARY",
            context.bdd_root.parent / "server" / ".venv" / "bin" / "imap-mcp",
        )
    )
    caller = next(iter(context.policy_builder.callers), None)
    caller_id = caller.id if caller else "bootstrap-probe"
    return try_bootstrap_server(server_binary, context.config_dir, caller_id)








@when('the operator changes the oauth_scope for "{account_id}" to "{new_scope}"')
@when('the operator changes the scope for "{account_id}" to "{new_scope}"')
def step_change_oauth_scope(context: Context, account_id: str, new_scope: str) -> None:
    builder = _ensure_builder(context)
    for a in builder.accounts:
        if a.id == account_id:
            a.oauth_scope = new_scope
            break
    builder.write()


@given('the IMAP account "{account_id}" is configured with provider "{provider}", oauth_scope "{scope}", and a valid refresh token')
def step_account_configured_oauth(
    context: Context, account_id: str, provider: str, scope: str
) -> None:
    builder = _ensure_builder(context)
    instance, user = resolve_account(account_id)
    host, port = context.imap_instances[instance]
    if not any(a.id == account_id for a in builder.accounts):
        builder.add_account(
            id=account_id,
            provider=provider,
            host=host,
            port=port,
            user=user if instance == "mock-gmail" else None,
            auth_type="xoauth2",
            secret_ref=f"secret://accounts/{account_id}/refresh_token",
            oauth_scope=scope,
        )
    path = context.secrets_dir / "accounts" / account_id / "refresh_token"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("mock_refresh_token", encoding="utf-8")
    builder.write()


@when('the operator removes account "{account_id}" from accounts.yaml and policy "{policy_name}"')
def step_operator_removes_account_and_policy(
    context: Context, account_id: str, policy_name: str
) -> None:
    builder = _ensure_builder(context)
    builder.accounts = [a for a in builder.accounts if a.id != account_id]
    policy = next((p for p in builder.policies if p.name == policy_name), None)
    if policy:
        policy.accounts.pop(account_id, None)
    builder.write()


@given('saga pause hook is armed at step "{step}"')
def step_arm_saga_pause(context: Context, step: str) -> None:
    marker = context.scratch_dir / "saga-pause-marker"
    context.mcp_extra_env = getattr(context, "mcp_extra_env", {}) or {}
    context.mcp_extra_env["IMAP_MCP_SAGA_PAUSE_AT"] = step
    context.mcp_extra_env["IMAP_MCP_SAGA_PAUSE_MARKER"] = str(marker)
    if getattr(context, "is_test_hooks", False) and not getattr(context, "without_test_mode", False):
        context.mcp_extra_env["IMAP_MCP_TEST_MODE"] = "1"
    context.saga_pause_marker = marker


@when('{caller_id} starts an asynchronous move of uid {uid:d} from "{source}" to "{target}" and waits until paused at "{step}"')
def step_start_async_move_and_wait(
    context: Context, caller_id: str, uid: int, source: str, target: str, step: str
) -> None:
    import json
    import threading
    import time

    from features.steps.mcp_steps import _ensure_mcp_client
    from support.imap_fixture import resolve_uid

    client = _ensure_mcp_client(context, caller_id)
    src_account, src_folder = _split_account_folder(context, source)
    dst_account, dst_folder = _split_account_folder(context, target)
    actual_uid = resolve_uid(context, src_account, src_folder, uid, allow_unseeded_probe=True)

    def _run_move() -> None:
        try:
            payload = client.call_tool(
                "move",
                {
                    "source": {"account": src_account, "folder": src_folder, "uid": actual_uid},
                    "target": {"account": dst_account, "folder": dst_folder},
                },
            )
            context.async_move_response = payload
            first = (payload.get("content") or [{}])[0]
            if isinstance(first, dict) and "text" in first:
                context.async_move_json = json.loads(first["text"])
                context.last_response = context.async_move_json
        except Exception as exc:
            context.async_move_error = exc

    t = threading.Thread(target=_run_move, daemon=True)
    context.async_move_thread = t
    t.start()

    marker = getattr(context, "saga_pause_marker", None)
    if marker is not None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if marker.exists():
                return
            if not t.is_alive():
                err = getattr(context, "async_move_error", None)
                resp = getattr(context, "async_move_json", None)
                stderr = getattr(context.mcp, "stderr_text", "")
                raise AssertionError(
                    f"Async move thread died before reaching {step!r}. Error: {err}, Response: {resp}\nStderr: {stderr}"
                )
            time.sleep(0.05)
        raise AssertionError(f"Saga did not reach pause point {step!r} within 15s")


@when('the paused saga is released and finishes')
def step_release_paused_saga_and_finish(context: Context) -> None:
    from pathlib import Path
    marker = getattr(context, "saga_pause_marker", None)
    if marker is not None:
        resume = Path(str(marker) + ".resume")
        resume.write_text("resume")
    t = getattr(context, "async_move_thread", None)
    if t is not None:
        t.join(timeout=15)
        if t.is_alive():
            raise AssertionError("Asynchronous move did not finish within 15s")


@then('the WAL entry for the returned tx_id has status "{status}"')
@then('the WAL entry for the asynchronous move has status "{status}"')
def step_wal_async_move_status(context: Context, status: str) -> None:
    from support.wal_reader import WALReader
    reader = WALReader(context.wal_path)
    txs = reader.all_transactions()
    tx_id = (getattr(context, "last_response", None) or {}).get("tx_id")
    if tx_id:
        tx = next((t for t in txs if t.tx_id == tx_id), None)
        if tx is None:
            raise AssertionError(f"No WAL transaction found with tx_id {tx_id!r}")
        if tx.status != status:
            raise AssertionError(f"Expected WAL tx {tx_id!r} status {status!r}, got {tx.status!r}")
        return
    matching = [t for t in txs if t.status == status]
    if not matching:
        statuses = [t.status for t in txs]
        raise AssertionError(
            f"Expected a WAL transaction with status {status!r}, found: {statuses}"
        )


@then('the asynchronous move response has decision "{decision}", result "{result}", and error.type "{error_type}"')
def step_async_move_response_assert(
    context: Context, decision: str, result: str, error_type: str
) -> None:
    resp = getattr(context, "async_move_json", None)
    if resp is None:
        raise AssertionError("No asynchronous move response captured")
    if resp.get("decision") != decision:
        raise AssertionError(f"Expected decision {decision!r}, got {resp.get('decision')!r}")
    if resp.get("result") != result:
        raise AssertionError(f"Expected result {result!r}, got {resp.get('result')!r}")
    err_t = (resp.get("error") or {}).get("type")
    if err_t != error_type:
        stderr = getattr(context.mcp, "stderr_text", "")
        raise AssertionError(
            f"Expected error.type {error_type!r}, got {err_t!r}. Response: {resp}\nStderr: {stderr}"
        )


@then('the audit log contains a record with tool "{tool}", reason "{reason}"')
def step_audit_log_contains_tool_reason(context: Context, tool: str, reason: str) -> None:
    from support.audit_reader import AuditReader
    reader = AuditReader(context.audit_dir)
    matches = reader.find(tool=tool, reason=reason)
    if not matches:
        present = [rec.record for rec in reader.records_today()]
        raise AssertionError(
            f"No audit record with tool={tool!r} reason={reason!r}. Present: {present!r}"
        )




@given('the account "{account_id}" has identity "{identity}"')
def step_account_has_identity(
    context: Context, account_id: str, identity: str
) -> None:
    """Set the account's owning mailbox; required by reply-builder tools
    so they can populate `From:` and dedup self from reply-all Cc lists."""
    builder = _ensure_builder(context)
    for account in builder.accounts:
        if account.id == account_id:
            account.identity = identity
            builder.write()
            return
    raise AssertionError(
        f"Account {account_id!r} is not registered yet; declare it via "
        f"'the IMAP account \"{account_id}\" exists with folder ...' first."
    )




# --------------------------------------------------------------------- bulk seeds


def _seed_n_messages(
    context: Context,
    account_id: str,
    folder: str,
    count: int,
    *,
    from_addr: str,
    date_str: str,
    flags: list[str] | None = None,
    starting_uid: int = 9000,
) -> None:
    instance, user = resolve_account(account_id)
    to_addr = f"{user}@{account_id}.example" if "@" not in user else user
    flag_list = flags or []

    if instance == "mock-gmail":
        from datetime import UTC, datetime
        from email.message import EmailMessage
        from email.policy import SMTP
        from email.utils import format_datetime

        from mock_gmail.state import FOLDER_TO_LABEL, Message

        state = getattr(context, "gmail_state", None)
        if state is None:
            raise AssertionError("Mock-gmail state not initialized")
        state.create_folder(folder)
        label = FOLDER_TO_LABEL.get(folder, folder)
        labels = {label} if label != "__ALL_MAIL__" else set()

        parsed_date = datetime.fromisoformat(date_str)
        if parsed_date.tzinfo is None:
            parsed_date = parsed_date.replace(tzinfo=UTC)
        formatted_date = format_datetime(parsed_date)

        for offset in range(count):
            uid = starting_uid + offset
            msg_id = f"<bulk-{uid}@{account_id}.example>"
            subj = f"Bulk message {offset + 1}"
            em = EmailMessage()
            em["From"] = from_addr
            em["To"] = to_addr
            em["Subject"] = subj
            em["Date"] = formatted_date
            em["Message-ID"] = msg_id
            em.set_content(f"Body for {subj}")
            raw = em.as_bytes(policy=SMTP)

            gmsg = Message(
                gm_msgid=uid,
                gm_thrid=uid,
                rfc822=raw,
                labels=set(labels),
                flags=set(flag_list),
                message_id=msg_id,
                from_addr=from_addr,
                to_addr=to_addr,
                subject=subj,
                date=formatted_date,
            )
            gmsg.internal_date = parsed_date
            state.add_message(gmsg)
            state.assign_uid(folder, gmsg.gm_msgid, uid)
            context.message_uids = getattr(context, "message_uids", {})
            context.message_uids[(account_id, folder, uid)] = uid
    else:
        context.staged_messages = getattr(context, "staged_messages", [])
        for offset in range(count):
            uid = starting_uid + offset
            context.staged_messages.append(
                {
                    "_account_id": account_id,
                    "_folder": folder,
                    "uid_hint": uid,
                    "from": from_addr,
                    "to": to_addr,
                    "subject": f"Bulk message {offset + 1}",
                    "message_id_override": f"<bulk-{uid}@{account_id}.example>",
                    "has_attachment": False,
                    "size_hint": 0,
                    "date": date_str,
                    "flags": flag_list,
                    "extra_attachments": [],
                    "extra_headers": [],
                    "body_override": None,
                    "html_body": None,
                    "html_only": False,
                }
            )


@given('the folder "{folder}" holds {count:d} messages from "{from_addr}" dated "{date_str}" pre-flagged "{tag}"')
def step_folder_holds_n_messages_from_dated_preflagged(
    context: Context, folder: str, count: int, from_addr: str, date_str: str, tag: str
) -> None:
    account_id, clean_folder = _split_account_folder(context, folder)
    _seed_n_messages(
        context,
        account_id,
        clean_folder,
        count,
        from_addr=from_addr,
        date_str=date_str,
        flags=[tag],
    )


@given('the folder "{folder}" holds {count:d} messages from "{from_addr}" dated "{date_str}" each tagged "{tag}"')
def step_folder_holds_n_messages_from_dated_tagged(
    context: Context, folder: str, count: int, from_addr: str, date_str: str, tag: str
) -> None:
    account_id, clean_folder = _split_account_folder(context, folder)
    _seed_n_messages(
        context,
        account_id,
        clean_folder,
        count,
        from_addr=from_addr,
        date_str=date_str,
        flags=[tag],
    )


@given('the folder "{folder}" holds {count:d} messages from "{from_addr}" dated "{date_str}" each carrying keyword "{kw}" and flags [{flags_raw}]')
def step_folder_holds_n_messages_carrying_kw_and_flags(
    context: Context,
    folder: str,
    count: int,
    from_addr: str,
    date_str: str,
    kw: str,
    flags_raw: str,
) -> None:
    account_id, clean_folder = _split_account_folder(context, folder)
    flag_list = [kw] + [f.strip() for f in flags_raw.split(",") if f.strip()]
    _seed_n_messages(
        context,
        account_id,
        clean_folder,
        count,
        from_addr=from_addr,
        date_str=date_str,
        flags=flag_list,
    )


@given('the folder "{folder}" holds {count:d} messages from "{from_addr}" dated "{date_str}"')
def step_folder_holds_n_messages_from_dated(
    context: Context, folder: str, count: int, from_addr: str, date_str: str
) -> None:
    account_id, clean_folder = _split_account_folder(context, folder)
    _seed_n_messages(
        context, account_id, clean_folder, count, from_addr=from_addr, date_str=date_str
    )


@then('the folder "{folder}" holds {count:d} messages with flag "{tag}"')
@given('the folder "{folder}" holds {count:d} messages with flag "{tag}"')
def step_folder_holds_n_messages_with_flag(
    context: Context, folder: str, count: int, tag: str
) -> None:
    account_id, clean_folder = _split_account_folder(context, folder)
    instance, user = resolve_account(account_id)
    if instance == "mock-gmail":
        state = getattr(context, "gmail_state", None)
        if state is None:
            raise AssertionError("No mock-gmail state available")
        msgs = state.messages_in_folder(clean_folder)
        if count == 0 and not msgs:
            raise AssertionError(
                f"Positive control failed: folder {folder!r} holds 0 messages total; "
                f"cannot witness absence of flag {tag!r}."
            )
        matching = 0
        for _, msg in msgs:
            if tag in msg.flags or tag in msg.labels:
                matching += 1
        if matching != count:
            raise AssertionError(
                f"Expected {count} messages with flag {tag!r} in {folder}, found {matching}"
            )
    else:
        all_uids = context.imap.folder_uids(instance, user, clean_folder)
        if count == 0 and not all_uids:
            raise AssertionError(
                f"Positive control failed: folder {folder!r} holds 0 messages total; "
                f"cannot witness absence of flag {tag!r}."
            )
        conn = context.imap.connect(instance, user)
        conn.select(context.imap.to_wire(instance, user, clean_folder))
        search_crit = "SEEN" if tag.lower() == "\\seen" else f'KEYWORD "{tag}"'
        status, data = conn.search(None, search_crit)
        if status != "OK":
            raise AssertionError(f"IMAP SEARCH failed on {folder}: {data}")
        uids = data[0].split() if data and data[0] else []
        if len(uids) != count:
            raise AssertionError(
                f"Expected {count} messages with flag {tag!r} in {folder}, found {len(uids)}"
            )






@given('policy "{policy_name}" grants no accounts')
def step_policy_grants_no_accounts(context: Context, policy_name: str) -> None:
    """Declare an empty-grant policy.

    Used by tool_surface_info.feature to assert that meta-tools work
    even when the caller has no read/write grants whatsoever.
    """
    from support.policy_builder import Policy

    builder = _ensure_builder(context)
    if any(p.name == policy_name for p in builder.policies):
        return
    builder.policies.append(Policy(name=policy_name, accounts={}))
    builder.write()


# --------------------------------------------------------------------- ADR 0028


@given("the server attachment sink directory is a fresh writable directory")
def step_sink_fresh_writable(context: Context) -> None:
    """Create a brand-new writable sink dir for the scenario and wire
    its path into the server config. The dir lives outside scratch_dir
    so a later chmod 0o500 read-only step does not lock down the rest
    of the scenario's writable scratch (audit, secrets, wal).
    """
    import tempfile

    sink = Path(tempfile.mkdtemp(prefix="imap-mcp-sink-"))
    context.attachment_sink_dir = sink
    context.attachment_sink_state = "configured"
    builder = _ensure_builder(context)
    builder.attachment_sink_directory = sink
    builder.write()


@given("the server attachment sink directory is not configured")
def step_sink_not_configured(context: Context) -> None:
    """Explicitly leave the sink unset. The server config will not
    carry an attachment_sink_directory; fetch_attachment must report
    sink_not_configured.
    """
    context.attachment_sink_dir = None
    context.attachment_sink_state = "unset"
    builder = _ensure_builder(context)
    builder.attachment_sink_directory = None
    builder.write()


@given("the server attachment sink directory points at a path that does not exist")
def step_sink_points_at_missing_path(context: Context) -> None:
    """Configure a path that is structurally absent: scratch_dir is
    real, but the leaf component is never created. The server sees a
    configured but missing directory."""
    sink = context.scratch_dir / "sink-does-not-exist"
    context.attachment_sink_dir = sink  # used for cleanup attempts
    context.attachment_sink_state = "broken"
    builder = _ensure_builder(context)
    builder.attachment_sink_directory = sink
    builder.write()


@given("the server attachment sink directory is a read-only directory")
def step_sink_read_only(context: Context) -> None:
    """Real, present, mode 0o500 so even the owner cannot write."""
    import tempfile

    sink = Path(tempfile.mkdtemp(prefix="imap-mcp-sink-ro-"))
    sink.chmod(0o500)
    context.attachment_sink_dir = sink
    context.attachment_sink_state = "broken"
    builder = _ensure_builder(context)
    builder.attachment_sink_directory = sink
    builder.write()


@given('"{target_dir}" is an empty writable directory and the configured sink path is a symlink pointing to "{target_dir_ref}"')
def step_sink_path_is_symlink(
    context: Context, target_dir: str, target_dir_ref: str
) -> None:
    target_path = Path(target_dir.replace("$TMPDIR", str(context.scratch_dir)))
    target_path.mkdir(parents=True, exist_ok=True)
    symlink_path = context.scratch_dir / "sink_symlink"
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()
    symlink_path.symlink_to(target_path)
    context.attachment_sink_dir = symlink_path
    context.attachment_sink_state = "configured"
    builder = _ensure_builder(context)
    builder.attachment_sink_directory = symlink_path
    builder.write()


@given('"{target_dir}" is an empty writable directory and "<sink_base>/{sub_path}" is a symlink pointing to "{target_dir_ref}"')
def step_sink_subpath_is_symlink(
    context: Context, target_dir: str, sub_path: str, target_dir_ref: str
) -> None:
    target_path = Path(target_dir.replace("$TMPDIR", str(context.scratch_dir)))
    target_path.mkdir(parents=True, exist_ok=True)
    base_sink = getattr(context, "attachment_sink_dir", None)
    if base_sink is None:
        raise AssertionError("Sink directory not set before planting caller symlink")
    symlink_path = base_sink / sub_path
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()
    symlink_path.symlink_to(target_path)




@when('{caller_id} calls fetch_body on the only visible message in "{qualified}"')
def step_caller_calls_fetch_body_only_visible(
    context: Context, caller_id: str, qualified: str
) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client, _store_result
    client = _ensure_mcp_client(context, caller_id)
    account_id, folder = _split_account_folder(context, qualified)
    payload = client.call_tool("list_messages", {"account": account_id, "folder": folder})
    _store_result(context, payload)
    messages = context.last_response.get("messages", [])
    if len(messages) != 1:
        raise AssertionError(
            f"Expected exactly 1 visible message in {qualified!r}, found {len(messages)}"
        )
    uid = messages[0]["uid"]
    payload2 = client.call_tool("fetch_body", {"account": account_id, "folder": folder, "uid": uid})
    _store_result(context, payload2)


@when('{caller_id} calls list_attachments on the only visible message in "{qualified}"')
def step_caller_calls_list_attachments_only_visible(
    context: Context, caller_id: str, qualified: str
) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client, _store_result
    client = _ensure_mcp_client(context, caller_id)
    account_id, folder = _split_account_folder(context, qualified)
    payload = client.call_tool("list_messages", {"account": account_id, "folder": folder})
    _store_result(context, payload)
    messages = context.last_response.get("messages", [])
    if len(messages) != 1:
        raise AssertionError(
            f"Expected exactly 1 visible message in {qualified!r}, found {len(messages)}"
        )
    uid = messages[0]["uid"]
    payload2 = client.call_tool("list_attachments", {"account": account_id, "folder": folder, "uid": uid})
    _store_result(context, payload2)


@when('{caller_id} calls fetch_attachment for part_id {part_id:d} on the only visible message in "{qualified}"')
def step_caller_calls_fetch_attachment_only_visible(
    context: Context, caller_id: str, part_id: int, qualified: str
) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client, _store_result
    client = _ensure_mcp_client(context, caller_id)
    account_id, folder = _split_account_folder(context, qualified)
    payload = client.call_tool("list_messages", {"account": account_id, "folder": folder})
    _store_result(context, payload)
    messages = context.last_response.get("messages", [])
    if len(messages) != 1:
        raise AssertionError(
            f"Expected exactly 1 visible message in {qualified!r}, found {len(messages)}"
        )
    uid = messages[0]["uid"]
    payload2 = client.call_tool(
        "fetch_attachment",
        {"account": account_id, "folder": folder, "uid": uid, "part_id": part_id},
    )
    _store_result(context, payload2)


@when('the human mail client sends the draft from "{src_qualified}" into "{dst_qualified}"')
def step_human_sends_draft(context: Context, src_qualified: str, dst_qualified: str) -> None:
    src_account, src_folder = _split_account_folder(context, src_qualified)
    _dst_account, dst_folder = _split_account_folder(context, dst_qualified)
    instance, user = resolve_account(src_account)
    if instance == "mock-gmail":
        state = getattr(context, "gmail_state", None)
        msgs = state.messages_in_folder(src_folder)
        if not msgs:
            raise AssertionError(f"No draft found in {src_qualified}")
        _uid, msg = msgs[-1]
        from mock_gmail.state import FOLDER_TO_LABEL
        src_label = FOLDER_TO_LABEL.get(src_folder, src_folder)
        dst_label = FOLDER_TO_LABEL.get(dst_folder, dst_folder)
        msg.labels.discard(src_label)
        msg.labels.add(dst_label)
    else:
        conn = context.imap.connect(instance, user)
        conn.select(context.imap.to_wire(instance, user, src_folder))
        status, data = conn.uid("SEARCH", None, "ALL")
        if status != "OK" or not data or not data[0]:
            raise AssertionError(f"No messages in {src_qualified}")
        uids = [int(u) for u in data[0].split()]
        last_uid = uids[-1]
        _st, fetch_data = conn.uid("FETCH", str(last_uid), "(RFC822)")
        raw = fetch_data[0][1]
        conn.create(context.imap.to_wire(instance, user, dst_folder))
        conn.append(context.imap.to_wire(instance, user, dst_folder), "(\\Seen)", None, raw)
        conn.uid("STORE", str(last_uid), "+FLAGS", "(\\Deleted)")
        conn.expunge()


@when('the human mail client sends the draft from "{src_qualified}" into "{dst_qualified}" stripping all X-SC-* headers while preserving Message-ID and body')
def step_human_sends_draft_stripping_headers(context: Context, src_qualified: str, dst_qualified: str) -> None:
    import email
    src_account, src_folder = _split_account_folder(context, src_qualified)
    _dst_account, dst_folder = _split_account_folder(context, dst_qualified)
    instance, user = resolve_account(src_account)
    if instance == "mock-gmail":
        state = getattr(context, "gmail_state", None)
        msgs = state.messages_in_folder(src_folder)
        if not msgs:
            raise AssertionError(f"No draft found in {src_qualified}")
        _uid, msg = msgs[-1]
        parsed = email.message_from_bytes(msg.rfc822)
        for h in list(parsed.keys()):
            if h.upper().startswith("X-SC-"):
                del parsed[h]
        stripped_bytes = parsed.as_bytes()
        msg.rfc822 = stripped_bytes
        from mock_gmail.state import FOLDER_TO_LABEL
        src_label = FOLDER_TO_LABEL.get(src_folder, src_folder)
        dst_label = FOLDER_TO_LABEL.get(dst_folder, dst_folder)
        msg.labels.discard(src_label)
        msg.labels.add(dst_label)
    else:
        conn = context.imap.connect(instance, user)
        conn.select(context.imap.to_wire(instance, user, src_folder))
        status, data = conn.uid("SEARCH", None, "ALL")
        if status != "OK" or not data or not data[0]:
            raise AssertionError(f"No messages in {src_qualified}")
        uids = [int(u) for u in data[0].split()]
        last_uid = uids[-1]
        _st, fetch_data = conn.uid("FETCH", str(last_uid), "(RFC822)")
        raw = fetch_data[0][1]
        parsed = email.message_from_bytes(raw)
        for h in list(parsed.keys()):
            if h.upper().startswith("X-SC-"):
                del parsed[h]
        stripped_bytes = parsed.as_bytes()
        conn.create(context.imap.to_wire(instance, user, dst_folder))
        conn.append(context.imap.to_wire(instance, user, dst_folder), "(\\Seen)", None, stripped_bytes)
        conn.uid("STORE", str(last_uid), "+FLAGS", "(\\Deleted)")
        conn.expunge()


@when('an external mail client replaces the body of the draft in "{qualified}" with "{new_body}" while preserving all X-SC-* headers')
def step_external_replaces_draft_body(context: Context, qualified: str, new_body: str) -> None:
    import email
    account_id, folder = _split_account_folder(context, qualified)
    instance, user = resolve_account(account_id)
    if instance == "mock-gmail":
        state = getattr(context, "gmail_state", None)
        msgs = state.messages_in_folder(folder)
        if not msgs:
            raise AssertionError(f"No draft found in {qualified}")
        _uid, msg = msgs[-1]
        parsed = email.message_from_bytes(msg.rfc822)
        if "Content-Transfer-Encoding" in parsed:
            del parsed["Content-Transfer-Encoding"]
        parsed.set_payload(new_body + "\r\n")
        msg.rfc822 = parsed.as_bytes()
    else:
        conn = context.imap.connect(instance, user)
        conn.select(context.imap.to_wire(instance, user, folder))
        status, data = conn.uid("SEARCH", None, "ALL")
        if status != "OK" or not data or not data[0]:
            raise AssertionError(f"No draft found in {qualified}")
        uids = [int(u) for u in data[0].split()]
        last_uid = uids[-1]
        _st, fetch_data = conn.uid("FETCH", str(last_uid), "(RFC822)")
        raw = fetch_data[0][1]
        parsed = email.message_from_bytes(raw)
        if "Content-Transfer-Encoding" in parsed:
            del parsed["Content-Transfer-Encoding"]
        parsed.set_payload(new_body + "\r\n")
        new_raw = parsed.as_bytes()
        conn.append(context.imap.to_wire(instance, user, folder), "(\\Draft)", None, new_raw)
        conn.uid("STORE", str(last_uid), "+FLAGS", "(\\Deleted)")
        conn.expunge()


@given('an external sender delivers a message into "{qualified}" copying X-SC-Draft-Id, X-SC-Draft-Rev, and X-SC-Provenance from that draft but with body "{body}"')
def step_external_delivers_forged_draft(context: Context, qualified: str, body: str) -> None:
    from datetime import datetime
    from email.message import EmailMessage
    account_id, folder = _split_account_folder(context, qualified)
    instance, user = resolve_account(account_id)
    drafts_msg = _fetch_message_by_subject(context, f"{account_id}:Drafts", "Harmlose Anfrage")
    draft_id = drafts_msg.get("X-SC-Draft-Id", "")
    draft_rev = drafts_msg.get("X-SC-Draft-Rev", "1")
    prov = drafts_msg.get("X-SC-Provenance", "")

    new_msg = EmailMessage()
    new_msg["From"] = "attacker@external.example"
    new_msg["To"] = f"{user}@bdd.local"
    new_msg["Subject"] = "Inbound forged"
    new_msg["X-SC-Draft-Id"] = draft_id
    new_msg["X-SC-Draft-Rev"] = draft_rev
    new_msg["X-SC-Provenance"] = prov
    new_msg.set_content(body)
    raw = new_msg.as_bytes()

    if instance == "mock-gmail":
        from mock_gmail.state import FOLDER_TO_LABEL, Message
        state = getattr(context, "gmail_state", None)
        lbl = FOLDER_TO_LABEL.get(folder, folder)
        gmsg = Message(
            gm_msgid=state.new_msgid(),
            gm_thrid=state.new_msgid(),
            rfc822=raw,
            labels={lbl} if lbl != "__ALL_MAIL__" else set(),
            subject="Inbound forged",
            from_addr="attacker@external.example",
            date=datetime.now(UTC).isoformat(),
        )
        state.add_message(gmsg)
    else:
        conn = context.imap.connect(instance, user)
        conn.create(context.imap.to_wire(instance, user, folder))
        conn.append(context.imap.to_wire(instance, user, folder), None, None, raw)


@given('an external client copies the draft message into "{qualified}"')
def step_external_copies_draft(context: Context, qualified: str) -> None:
    from datetime import datetime
    account_id, folder = _split_account_folder(context, qualified)
    instance, user = resolve_account(account_id)
    drafts_msg = _fetch_message_by_subject(context, f"{account_id}:Drafts", "Nicht aus INBOX verwerfbar")
    raw = drafts_msg.as_bytes()
    if instance == "mock-gmail":
        from mock_gmail.state import FOLDER_TO_LABEL, Message
        state = getattr(context, "gmail_state", None)
        lbl = FOLDER_TO_LABEL.get(folder, folder)
        gmsg = Message(
            gm_msgid=state.new_msgid(),
            gm_thrid=state.new_msgid(),
            rfc822=raw,
            labels={lbl} if lbl != "__ALL_MAIL__" else set(),
            subject="Nicht aus INBOX verwerfbar",
            date=datetime.now(UTC).isoformat(),
        )
        state.add_message(gmsg)
    else:
        conn = context.imap.connect(instance, user)
        conn.create(context.imap.to_wire(instance, user, folder))
        conn.append(context.imap.to_wire(instance, user, folder), None, None, raw)


@given('a second server instance is started for caller "{caller_id}" with policy granting FULL, draft_append, draft_edit, and move_out on "{folder}" and accept_incoming on "{trash}"')
def step_second_server_for_caller(context: Context, caller_id: str, folder: str, trash: str) -> None:
    import os
    import shutil

    import yaml

    from features.steps.mcp_steps import SERVER_BINARY_ENV, _client_info_kwargs
    from support.mcp_client import MCPClient

    second_config_dir = context.scratch_dir / f"config-{caller_id}"
    second_config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(context.config_dir / "accounts.yaml", second_config_dir / "accounts.yaml")
    callers_data = {
        "callers": [
            {
                "id": caller_id,
                "policy": f"{caller_id}-policy",
                "auth": {"type": "stdio_trusted"},
            }
        ]
    }
    (second_config_dir / "callers.yaml").write_text(yaml.safe_dump(callers_data), encoding="utf-8")
    policies_dir = second_config_dir / "policies"
    policies_dir.mkdir(exist_ok=True)
    builder = _ensure_builder(context)
    acct = builder.accounts[0].id
    policy_data = {
        "name": f"{caller_id}-policy",
        "accounts": {
            acct: [
                {
                    "path": folder,
                    "mode": "blacklist",
                    "default": "FULL",
                    "draft_append": True,
                    "draft_edit": True,
                    "move_out": True,
                    "rules": [],
                },
                {
                    "path": trash,
                    "mode": "blacklist",
                    "default": "METADATA",
                    "accept_incoming": True,
                    "rules": [],
                },
            ]
        },
    }
    (policies_dir / f"{caller_id}-policy.yaml").write_text(yaml.safe_dump(policy_data), encoding="utf-8")

    server_binary = Path(
        os.environ.get(
            SERVER_BINARY_ENV,
            context.bdd_root.parent / "server" / ".venv" / "bin" / "imap-mcp",
        )
    )
    extra_env = dict(getattr(context, "mcp_extra_env", None) or {})
    if getattr(context, "is_test_hooks", False) and not getattr(context, "without_test_mode", False):
        extra_env["IMAP_MCP_TEST_MODE"] = "1"
    else:
        extra_env.pop("IMAP_MCP_TEST_MODE", None)
    client2 = MCPClient(
        server_binary=server_binary,
        config_dir=second_config_dir,
        caller_id=caller_id,
        extra_env=extra_env,
        **_client_info_kwargs(context),
    )
    client2.start()
    clients = getattr(context, "mcp_clients", None)
    if clients is None:
        clients = {}
        context.mcp_clients = clients
    clients[caller_id] = client2
    context.mcp = client2


@given('a second server instance is started sharing the same config and wal.db for caller "{caller_id}"')
def step_second_server_sharing_config(context: Context, caller_id: str) -> None:
    from features.steps.mcp_steps import SERVER_BINARY_ENV, _client_info_kwargs
    from support.mcp_client import MCPClient

    server_binary = Path(
        os.environ.get(
            SERVER_BINARY_ENV,
            context.bdd_root.parent / "server" / ".venv" / "bin" / "imap-mcp",
        )
    )
    extra_env = dict(getattr(context, "mcp_extra_env", None) or {})
    if getattr(context, "is_test_hooks", False) and not getattr(context, "without_test_mode", False):
        extra_env["IMAP_MCP_TEST_MODE"] = "1"
    else:
        extra_env.pop("IMAP_MCP_TEST_MODE", None)
    client2 = MCPClient(
        server_binary=server_binary,
        config_dir=context.config_dir,
        caller_id=caller_id,
        extra_env=extra_env,
        unset_env=tuple(getattr(context, "mcp_unset_env", ())),
        **_client_info_kwargs(context),
    )
    client2.start()
    context.second_mcp = client2


@when('the two "{caller_id}" instances concurrently call update_draft on "{location}" for the returned draft_id with base_revision {base_rev:d} and distinct subjects "{subj_a}" and "{subj_b}"')
def step_concurrent_update_draft(
    context: Context,
    caller_id: str,
    location: str,
    base_rev: int,
    subj_a: str,
    subj_b: str,
) -> None:
    import concurrent.futures
    import json

    from features.steps.mcp_steps import _ensure_mcp_client
    account_id, folder = _split_account_folder(context, location)
    draft_id = context.last_response["draft_id"]
    client1 = _ensure_mcp_client(context, caller_id)
    client2 = context.second_mcp

    def call(c, subj):
        res = c.call_tool(
            "update_draft",
            {
                "account": account_id,
                "folder": folder,
                "draft_id": draft_id,
                "base_revision": base_rev,
                "changes": {"subject": subj},
            },
        )
        if isinstance(res, dict) and "content" in res:
            content = res.get("content", [])
            if content and isinstance(content[0], dict) and "text" in content[0]:
                try:
                    return json.loads(content[0]["text"])
                except Exception:
                    pass
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(call, client1, subj_a)
        f2 = executor.submit(call, client2, subj_b)
        res1 = f1.result()
        res2 = f2.result()

    context.concurrent_update_responses = [res1, res2]
    context.last_response = res1


@when('{caller_id} calls update_draft with account "{account}", folder "{folder}", the draft_id created by {creator}, base_revision {rev:d}, changes:')
def step_caller_calls_update_draft_by_creator(
    context: Context, caller_id: str, account: str, folder: str, creator: str, rev: int
) -> None:
    import json

    from features.steps.mcp_steps import _ensure_mcp_client, _store_result
    client = _ensure_mcp_client(context, caller_id)
    draft_id = getattr(context, "last_created_draft_id", None) or getattr(context, "returned_draft_id", None)
    if not draft_id:
        draft_id = context.last_response.get("draft_id")
    changes = json.loads(context.text or "{}")
    payload = client.call_tool(
        "update_draft",
        {
            "account": account,
            "folder": folder,
            "draft_id": draft_id,
            "base_revision": rev,
            "changes": changes,
        },
    )
    _store_result(context, payload)


@when('{caller_id} calls discard_draft with account "{account}", folder "{folder}", the draft_id created by {creator}')
def step_caller_calls_discard_draft_by_creator(
    context: Context, caller_id: str, account: str, folder: str, creator: str
) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client, _store_result
    client = _ensure_mcp_client(context, caller_id)
    draft_id = getattr(context, "last_created_draft_id", None) or getattr(context, "returned_draft_id", None)
    if not draft_id:
        draft_id = context.last_response.get("draft_id")
    payload = client.call_tool(
        "discard_draft",
        {
            "account": account,
            "folder": folder,
            "draft_id": draft_id,
        },
    )
    _store_result(context, payload)
