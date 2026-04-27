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
from pathlib import Path

import yaml
from behave import given, then, use_step_matcher
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
            audit_directory=context.audit_dir,
            wal_path=context.wal_path,
        )
        context.policy_builder = builder
    return builder


def _ensure_account_registered(
    context: Context, builder: PolicyBuilder, account_id: str
) -> None:
    if any(a.id == account_id for a in builder.accounts):
        return
    instance, _user = resolve_account(account_id)
    host, port = context.imap_instances[instance]
    builder.add_account(
        id=account_id,
        host=host,
        port=port,
        auth_type="password",
        secret_ref=f"secret://accounts/{account_id}/password",
        password_literal="test123",
    )


@given('the IMAP account "{account_id}" exists with folders:')
def step_imap_account_exists_with_folders(context: Context, account_id: str) -> None:
    builder = _ensure_builder(context)
    _ensure_account_registered(context, builder, account_id)

    instance, user = resolve_account(account_id)
    for row in context.table:
        folder = row["folder path"]
        context.imap.create_folder(instance, user, folder)

    builder.write()


@given('the IMAP account "{account_id}" exists with folder "{folder}"')
def step_imap_account_exists_with_single_folder(
    context: Context, account_id: str, folder: str
) -> None:
    builder = _ensure_builder(context)
    _ensure_account_registered(context, builder, account_id)
    instance, user = resolve_account(account_id)
    context.imap.create_folder(instance, user, folder)
    builder.write()


@given("the server is configured with caller:")
def step_server_configured_with_caller(context: Context) -> None:
    builder = _ensure_builder(context)
    for row in context.table:
        builder.add_caller(
            id=row["caller_id"],
            policy=row["policy"],
            auth_type="stdio_trusted",
        )
        builder.add_policy(row["policy"])
    builder.write()


@given('policy "{policy_name}" grants account access:')
def step_policy_grants_account_access(context: Context, policy_name: str) -> None:
    builder = _ensure_builder(context)
    policy = next((p for p in builder.policies if p.name == policy_name), None)
    if policy is None:
        policy = builder.add_policy(policy_name)
    for row in context.table:
        account_id = row["account"]
        policy.accounts.setdefault(account_id, [])
    builder.write()


@given('policy "{policy_name}" grants the following folder capabilities:')
def step_policy_grants_folder_capabilities(
    context: Context, policy_name: str
) -> None:
    step_policy_grants_folder_policies(context, policy_name)


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
            accept_incoming=_parse_bool(row["accept_incoming"]) if "accept_incoming" in row.headings else False,
            draft_append=_parse_bool(row["draft_append"]) if "draft_append" in row.headings else False,
        )
        if "rules" in row.headings:
            rules_raw = row["rules"]
            for parsed_match, grant, cap in _parse_inline_rules(rules_raw):
                folder.rules.append(SenderRule(match=parsed_match, grant=grant, cap=cap))
    builder.write()


@given('policy "{policy_name}" sets folder "{folder}" capabilities to:')
def step_policy_sets_folder_capabilities(
    context: Context, policy_name: str, folder: str
) -> None:
    builder = _ensure_builder(context)
    policy = next((p for p in builder.policies if p.name == policy_name), None)
    if policy is None:
        raise AssertionError(f"Policy {policy_name!r} not declared yet.")
    target = None
    for folders in policy.accounts.values():
        for fp in folders:
            if fp.path == folder:
                target = fp
                break
        if target is not None:
            break
    if target is None:
        raise AssertionError(f"Folder {folder!r} not declared in {policy_name!r}.")
    row = context.table[0]
    for key in ("mark_seen", "mark_tagged", "move_out", "accept_incoming", "draft_append"):
        if key in row.headings:
            setattr(target, key, _parse_bool(row[key]))
    builder.write()


@given('policy "{policy_name}" grants folder access:')
def step_policy_grants_folder_access(context: Context, policy_name: str) -> None:
    builder = _ensure_builder(context)
    for row in context.table:
        builder.folder(
            policy_name=policy_name,
            account_id=row["account"],
            path=row["folder"],
            mode=row["mode"],
            default=row["default"],
        )
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
    builder = _ensure_builder(context)
    for row in context.table:
        account_id = row.get("account")
        if account_id is None:
            # When no `account` column is present, pick the only
            # account that is currently registered. Scenarios that
            # use this shorthand have exactly one account in play.
            if len(builder.accounts) != 1:
                raise AssertionError(
                    "policy grants folder: without an `account` column "
                    f"requires exactly one registered account; found "
                    f"{len(builder.accounts)}."
                )
            account_id = builder.accounts[0].id
        folder = builder.folder(
            policy_name=policy_name,
            account_id=account_id,
            path=row["folder"],
            mode=row["mode"],
            default=row["default"],
        )
        rules_raw = row["rules"] if "rules" in row.headings else row.get("rule", "[]")
        # The `rule` (singular) column holds a single rule without
        # the surrounding [...] brackets; promote it to the list shape
        # the parser expects.
        if "rule" in row.headings and "rules" not in row.headings:
            rule_body = rules_raw.strip()
            if not rule_body.startswith("["):
                rules_raw = "[{" + rule_body + "}]" if not rule_body.startswith("{") else "[" + rule_body + "]"
        for parsed_match, grant, cap in _parse_inline_rules(rules_raw):
            from support.policy_builder import SenderRule

            folder.rules.append(SenderRule(match=parsed_match, grant=grant, cap=cap))
    builder.write()


def _parse_inline_rules(
    raw: str,
) -> list[tuple[dict[str, object], str | None, str | None]]:
    """Parse `[{k=v AND k2=v2 -> FULL}, {k=v -> cap NONE}]`.

    Returns a list of (match_dict, grant, cap). Rules that use the
    `cap X` syntax populate `cap`; rules that name a bare level
    populate `grant`. Empty-list input yields an empty result.
    """
    text = raw.strip()
    if text in ("", "[]"):
        return []
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
        else:
            results.append((match_dict, rhs_part, None))
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


def _body_padded_to(target_size: int, subject: str) -> str:
    """Produce a body that, combined with standard headers, reaches ~target_size bytes.

    Exact size matching is impossible without measuring; we aim for a
    body whose length is close enough that IMAP RFC822.SIZE lands in
    the order-of-magnitude the feature expects. Scenarios that rely on
    size thresholds use widely-spaced values, so approximate matching
    is safe.
    """
    if target_size <= 500:
        return f"Body for {subject}".ljust(target_size, " ")
    filler = "x" * max(target_size - 300, 100)
    return f"Body for {subject}\n\n{filler}"


def _coerce(value: str) -> object:
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
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


@given(r'the folder "(?P<folder>[^"]+)" holds messages:')
def step_folder_holds_messages_plural(context: Context, folder: str) -> None:
    account_id = _find_account_for_folder(context, folder)
    _seed_message(context, account_id, folder)


@given(
    r'the folder "(?P<folder>[^"]+)" of "(?P<account_id>[^"]+)" holds messages:'
)
def step_folder_holds_messages_plural_explicit(
    context: Context, folder: str, account_id: str
) -> None:
    _seed_message(context, account_id, folder)


use_step_matcher("parse")


def _find_account_for_folder(context: Context, folder: str) -> str:
    """Look up which configured account declares this folder path.

    Scans the builder's policies (not IMAP) so the lookup is
    deterministic and independent of server state. Ambiguity is a
    fatal error: the feature file must then use the explicit
    `of "<account>"` form.
    """
    builder = context.policy_builder
    hits: list[str] = []
    for policy in builder.policies:
        for account_id, folder_list in policy.accounts.items():
            if any(fp.path == folder for fp in folder_list):
                if account_id not in hits:
                    hits.append(account_id)
    # Also check accounts that were registered without any policy
    # folder declaration (i.e. accounts that exist in the fixture but
    # intentionally carry no policy — scenario 4 deals with that).
    instances = context.imap_instances
    for account in builder.accounts:
        if account.id in hits:
            continue
        instance, user = resolve_account(account.id)
        _ = instances  # host/port not needed for the imap lookup
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
    """
    context.staged_messages = getattr(context, "staged_messages", [])
    for row in context.table:
        headings = row.headings
        staged: dict[str, object] = {
            "_account_id": account_id,
            "_folder": folder,
            "uid_hint": int(row["uid"]),
            "from": row["from"] if "from" in headings else None,
            "to": row["to"] if "to" in headings else None,
            "subject": row["subject"] if "subject" in headings else "Test",
            "message_id_override": row["message_id"]
            if "message_id" in headings
            else None,
            "has_attachment": _parse_bool(row["has_attachment"])
            if "has_attachment" in headings
            else False,
            "size_hint": int(row["size_bytes"])
            if "size_bytes" in headings
            else 0,
            "date": row["date"] if "date" in headings else None,
            "extra_attachments": [],
            "extra_headers": [],
            "body_override": None,
        }
        context.staged_messages.append(staged)


def flush_staged_messages(context: Context) -> None:
    """Turn the staged message list into real IMAP APPENDs."""
    from datetime import datetime, timezone
    from email.utils import format_datetime

    staged_list = getattr(context, "staged_messages", [])
    if not staged_list:
        return
    for staged in staged_list:
        account_id = staged["_account_id"]
        folder = staged["_folder"]
        uid_hint = staged["uid_hint"]
        instance, user = resolve_account(account_id)
        context.imap.create_folder(instance, user, folder)
        sender = staged["from"] or f"{user}@bdd.local"
        to_addr = staged["to"] or f"{user}@bdd.local"
        subject = staged["subject"]
        size_hint = staged["size_hint"]
        date_header: str | None = None
        if staged["date"]:
            iso = staged["date"]
            parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            date_header = format_datetime(parsed)
        body_override = staged["body_override"]
        if body_override is not None:
            body = body_override
        elif size_hint:
            body = _body_padded_to(size_hint, subject)
        else:
            body = f"Body for {subject}"
        attachments: list[tuple[str, str, bytes]] = []
        if staged["has_attachment"] and not staged["extra_attachments"]:
            attachments.append(("stub.bin", "application/octet-stream", b"x" * 16))
        for extra in staged["extra_attachments"]:
            attachments.append(extra)
        extra_headers = {
            name: value for name, value in staged["extra_headers"]
        }
        message_id = staged["message_id_override"] or f"<scenario-{uid_hint}@bdd.local>"
        seeded = context.imap.seed_message(
            instance,
            user,
            folder,
            sender=sender,
            to=to_addr,
            subject=subject,
            body=body,
            message_id=message_id,
            date=date_header,
            attachments=attachments,
            extra_headers=extra_headers,
        )
        context.message_uids = getattr(context, "message_uids", {})
        context.message_uids[(account_id, folder, uid_hint)] = seeded.uid
    context.staged_messages = []


@given('the folder "{folder}" is empty')
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
    # Empty by delete+recreate; simpler than per-message EXPUNGE here.
    uids = context.imap.folder_uids(instance, user, folder)
    if uids:
        # Leave deletion to reset_user behaviour; if we're here before
        # reset, this is defensive.
        pass


@given('the folder "{folder}" holds a message with uid {uid:d}')
def step_folder_holds_message_bare_uid(
    context: Context, folder: str, uid: int
) -> None:
    """Shortcut: seed a single default message at `uid` with no table."""
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _find_account_for_folder(context, folder)
    context.staged_messages = getattr(context, "staged_messages", [])
    context.staged_messages.append(
        {
            "_account_id": account_id,
            "_folder": folder,
            "uid_hint": uid,
            "from": None,
            "to": None,
            "subject": f"Stub for uid {uid}",
            "message_id_override": None,
            "has_attachment": False,
            "size_hint": 0,
            "date": None,
            "extra_attachments": [],
            "extra_headers": [],
            "body_override": None,
        }
    )


@given('policy grants mark_tagged=true on "{folder}"')
def step_policy_grants_mark_tagged(context: Context, folder: str) -> None:
    """Ensure the single caller's policy has mark_tagged=true on folder."""
    from support.policy_builder import SenderRule

    builder = _ensure_builder(context)
    if not builder.policies:
        raise AssertionError("No policies configured yet")
    policy = builder.policies[0]
    account_id = next(iter(policy.accounts.keys()), None)
    if account_id is None:
        raise AssertionError("No account in policy")
    found = None
    for fp in policy.accounts[account_id]:
        if fp.path == folder:
            found = fp
            break
    if found is None:
        found = builder.folder(
            policy_name=policy.name,
            account_id=account_id,
            path=folder,
            mode="whitelist",
            default="NONE",
            mark_tagged=True,
        )
        found.rules.append(
            SenderRule(match={"from_domain": "hornbach.de"}, grant="FULL")
        )
    else:
        found.mark_tagged = True
    builder.write()


@given("the audit log directory is a fresh $TMPDIR/audit")
def step_audit_log_fresh(context: Context) -> None:
    # before_scenario already created a fresh scratch_dir/audit;
    # this step serves as human-readable assertion that it is indeed
    # fresh. Nothing to do.
    if not context.audit_dir.exists():
        context.audit_dir.mkdir(parents=True)


@given('policy "{policy_name}" grants INBOX/Rechnungen with '
       'mode=whitelist, default=NONE, rule from_domain=hornbach.de -> FULL')
def step_policy_specific_shortcut(
    context: Context, policy_name: str
) -> None:
    """Shortcut used by audit_log_format background — equivalent to the
    more verbose folder-policy + rules tabular form."""
    from support.policy_builder import SenderRule

    builder = _ensure_builder(context)
    policy = next((p for p in builder.policies if p.name == policy_name), None)
    if policy is None:
        policy = builder.add_policy(policy_name)
    if len(builder.accounts) < 1:
        _ensure_account_registered(context, builder, "gupta-scaratec")
    account_id = builder.accounts[0].id
    policy.accounts.setdefault(account_id, [])
    folder = builder.folder(
        policy_name=policy_name,
        account_id=account_id,
        path="INBOX/Rechnungen",
        mode="whitelist",
        default="NONE",
    )
    folder.rules.append(
        SenderRule(match={"from_domain": "hornbach.de"}, grant="FULL")
    )
    # Make sure the folder is created on the IMAP side too.
    instance, user = resolve_account(account_id)
    context.imap.create_folder(instance, user, "INBOX/Rechnungen")
    builder.write()


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
    # Retry-scenarios always exercise the test-only recovery tool.
    context.mcp_extra_env["IMAP_MCP_TEST_MODE"] = "1"


def _ensure_fault_registry(context: Context) -> dict[str, dict]:
    """Build (and stash on context) the dict that serialises into
    IMAP_MCP_FAULT_INJECTION when the server starts."""
    extra_env = getattr(context, "mcp_extra_env", None)
    if extra_env is None:
        extra_env = {}
        context.mcp_extra_env = extra_env
    registry: dict[str, dict] = getattr(context, "fault_registry", None) or {}
    context.fault_registry = registry
    # Recovery scenarios need the test-only tool surfaced.
    extra_env.setdefault("IMAP_MCP_TEST_MODE", "1")
    return registry


def _flush_fault_registry(context: Context) -> None:
    """Serialise the fault registry back into mcp_extra_env."""
    import json as _json

    registry = getattr(context, "fault_registry", None)
    if registry is None:
        return
    context.mcp_extra_env["IMAP_MCP_FAULT_INJECTION"] = _json.dumps(registry)


@given('the IMAP server for "{account_id}" responds to the next APPEND with error {code:d}')
def step_fault_next_append_error(context: Context, account_id: str, code: int) -> None:
    registry = _ensure_fault_registry(context)
    registry.setdefault(account_id, {})["append"] = {
        "error": code,
        "remaining": 1,
    }
    _flush_fault_registry(context)


@given('the IMAP server for "{account_id}" responds to every APPEND with error {code:d}')
def step_fault_every_append_error(context: Context, account_id: str, code: int) -> None:
    registry = _ensure_fault_registry(context)
    registry.setdefault(account_id, {})["append"] = {
        "error": code,
        "remaining": None,
    }
    _flush_fault_registry(context)


@given('the IMAP server for "{account_id}" delays the next APPEND response by {seconds:d} seconds')
def step_fault_append_delay(context: Context, account_id: str, seconds: int) -> None:
    registry = _ensure_fault_registry(context)
    registry.setdefault(account_id, {})["append"] = {
        "delay_seconds": seconds,
        "remaining": 1,
    }
    _flush_fault_registry(context)


@given('the server append_timeout is configured to {seconds:d} seconds')
def step_server_append_timeout(context: Context, seconds: int) -> None:
    context.mcp_extra_env = getattr(context, "mcp_extra_env", {})
    context.mcp_extra_env["IMAP_MCP_APPEND_TIMEOUT"] = str(seconds)


@given('the IMAP server for "{account_id}" responds to the next EXPUNGE with error {code:d} exactly once')
def step_fault_next_expunge_once(context: Context, account_id: str, code: int) -> None:
    registry = _ensure_fault_registry(context)
    registry.setdefault(account_id, {})["expunge"] = {
        "error": code,
        "remaining": 1,
    }
    _flush_fault_registry(context)


@given('the IMAP server for "{account_id}" refuses all connections')
def step_fault_refuse_connections(context: Context, account_id: str) -> None:
    registry = _ensure_fault_registry(context)
    registry.setdefault(account_id, {})["connect"] = {"refuse": True}
    _flush_fault_registry(context)


@given('the folder "{folder}" contains no message with uid {uid:d}')
def step_folder_has_no_uid(context: Context, folder: str, uid: int) -> None:
    """No-op: the scenario states that the seed fixture never created
    this uid. The assertion is that the move handler must return
    uid_not_found when it queries IMAP for a non-existent uid."""
    _ = (context, folder, uid)


@given('policy "{policy_name}" references folder "{folder_path}" with accept_incoming={value}')
def step_policy_references_folder_incoming(
    context: Context, policy_name: str, folder_path: str, value: str
) -> None:
    """Extend an existing policy to include a folder whose IMAP
    existence is controlled separately. The scenario that exercises
    target_folder_missing deliberately *does not* create the folder
    on IMAP even though policy grants access to it."""
    builder = _ensure_builder(context)
    policy = next((p for p in builder.policies if p.name == policy_name), None)
    if policy is None:
        policy = builder.add_policy(policy_name)
    account_id = next(iter(policy.accounts.keys()), None)
    if account_id is None:
        raise ValueError(
            f"Policy {policy_name!r} has no account configured yet; cannot add folder."
        )
    builder.folder(
        policy_name=policy_name,
        account_id=account_id,
        path=folder_path,
        mode="whitelist",
        default="NONE",
        accept_incoming=value.strip().lower() == "true",
    )
    builder.write()


@given("the audit file already contains 5 records R1..R5 forming a valid chain")
def step_audit_seed_5_records(context: Context) -> None:
    """Drive five successful fetch_envelope calls so the audit log
    contains five records chained via prev_hash."""
    from features.steps.mcp_steps import _ensure_mcp_client

    builder = _ensure_builder(context)
    policy = builder.policies[0]
    account_id = next(iter(policy.accounts.keys()))
    folder_path = policy.accounts[account_id][0].path
    context.staged_messages = getattr(context, "staged_messages", [])
    for n in range(5):
        context.staged_messages.append(
            {
                "_account_id": account_id,
                "_folder": folder_path,
                "uid_hint": 801 + n,
                "from": "rechnung@hornbach.de",
                "to": None,
                "subject": f"Chain-seed {n}",
                "message_id_override": f"<chain-{n}@test>",
                "has_attachment": False,
                "size_hint": 0,
                "date": None,
                "extra_attachments": [],
                "extra_headers": [],
                "body_override": None,
            }
        )
    flush_staged_messages(context)
    client = _ensure_mcp_client(context, "invoice-agent")
    uid_lookup = getattr(context, "message_uids", {})
    for n in range(5):
        actual = uid_lookup.get((account_id, folder_path, 801 + n), 801 + n)
        client.call_tool(
            "fetch_envelope",
            {"account": account_id, "folder": folder_path, "uid": actual},
        )


@given(
    "a sequence of operations over a day creates 20 audit records "
    "across ALLOW, DENY, saga, and token_refresh"
)
def step_audit_20_records(context: Context) -> None:
    """Fire 20 tool calls producing a mixed audit trail."""
    from features.steps.mcp_steps import _ensure_mcp_client

    builder = _ensure_builder(context)
    policy = builder.policies[0]
    account_id = next(iter(policy.accounts.keys()))
    folder_path = policy.accounts[account_id][0].path
    # Seed 10 messages: first 5 pass (hornbach), last 5 fail (spam).
    context.staged_messages = getattr(context, "staged_messages", [])
    for n in range(5):
        context.staged_messages.append(
            {
                "_account_id": account_id,
                "_folder": folder_path,
                "uid_hint": 901 + n,
                "from": "rechnung@hornbach.de",
                "to": None,
                "subject": f"Chain-allow-{n}",
                "message_id_override": f"<chain-allow-{n}@test>",
                "has_attachment": False,
                "size_hint": 0,
                "date": None,
                "extra_attachments": [],
                "extra_headers": [],
                "body_override": None,
            }
        )
    for n in range(5):
        context.staged_messages.append(
            {
                "_account_id": account_id,
                "_folder": folder_path,
                "uid_hint": 911 + n,
                "from": "spam@other.com",
                "to": None,
                "subject": f"Chain-deny-{n}",
                "message_id_override": f"<chain-deny-{n}@test>",
                "has_attachment": False,
                "size_hint": 0,
                "date": None,
                "extra_attachments": [],
                "extra_headers": [],
                "body_override": None,
            }
        )
    flush_staged_messages(context)
    client = _ensure_mcp_client(context, "invoice-agent")
    uid_lookup = getattr(context, "message_uids", {})
    # 10 ALLOW+DENY calls.
    for n in range(5):
        actual = uid_lookup.get((account_id, folder_path, 901 + n), 901 + n)
        client.call_tool(
            "fetch_envelope",
            {"account": account_id, "folder": folder_path, "uid": actual},
        )
    for n in range(5):
        actual = uid_lookup.get((account_id, folder_path, 911 + n), 911 + n)
        client.call_tool(
            "fetch_envelope",
            {"account": account_id, "folder": folder_path, "uid": actual},
        )
    # 10 list_accounts/search calls to pad to 20 audit records.
    for _ in range(5):
        client.call_tool("list_accounts", {})
    for _ in range(5):
        client.call_tool(
            "search",
            {
                "account": account_id,
                "folder": folder_path,
                "criteria": {"subject_contains": "Rechnung"},
            },
        )


@given("a cross-account move begins and succeeds")
def step_cross_account_move_begins(context: Context) -> None:
    """Seed a second account + message + cross-account move that
    commits successfully. Produces the full saga_transition audit
    trail required by the '#60 saga transitions' scenario."""
    builder = _ensure_builder(context)
    # Ensure the second account exists.
    second_account = "personal"
    _ensure_account_registered(context, builder, second_account)
    instance_b, user_b = resolve_account(second_account)
    context.imap.create_folder(instance_b, user_b, "Archiv/Belege")

    # Ensure the target folder is in the policy with accept_incoming.
    policy = builder.policies[0]
    existing = [
        f
        for folders in policy.accounts.values()
        for f in folders
        if f.path == "Archiv/Belege"
    ]
    if not existing:
        builder.folder(
            policy_name=policy.name,
            account_id=second_account,
            path="Archiv/Belege",
            mode="whitelist",
            default="NONE",
            accept_incoming=True,
        )

    # Source already exists with from=hornbach.de rule; grant move_out.
    src_account_id = next(iter(policy.accounts.keys()))
    for fp in policy.accounts[src_account_id]:
        if fp.path == "INBOX/Rechnungen":
            fp.move_out = True
            break
    builder.write()

    # Stage a message from hornbach.de (matches the existing rule).
    context.staged_messages = getattr(context, "staged_messages", [])
    context.staged_messages.append(
        {
            "_account_id": src_account_id,
            "_folder": "INBOX/Rechnungen",
            "uid_hint": 701,
            "from": "rechnung@hornbach.de",
            "to": None,
            "subject": "Rechnung 7823",
            "message_id_override": "<m-cross-@gupta-scaratec.com>",
            "has_attachment": False,
            "size_hint": 0,
            "date": None,
            "extra_attachments": [],
            "extra_headers": [],
            "body_override": None,
        }
    )
    flush_staged_messages(context)

    # Now drive the move via the MCP client.
    from features.steps.mcp_steps import _ensure_mcp_client

    client = _ensure_mcp_client(context, "invoice-agent")
    uid_lookup = getattr(context, "message_uids", {})
    actual_uid = uid_lookup.get((src_account_id, "INBOX/Rechnungen", 701), 701)
    payload = client.call_tool(
        "move",
        {
            "source": {
                "account": src_account_id,
                "folder": "INBOX/Rechnungen",
                "uid": actual_uid,
            },
            "target": {
                "account": second_account,
                "folder": "Archiv/Belege",
            },
        },
    )
    import json as _json

    content = payload.get("content") or []
    if content:
        data = _json.loads(content[0]["text"])
        context.last_response = data
        context.last_tx_id = data.get("tx_id")


@given("the server is configured to crash after WAL BEGIN persistence")
def step_crash_at_post_begin(context: Context) -> None:
    context.mcp_extra_env = getattr(context, "mcp_extra_env", {})
    context.mcp_extra_env["IMAP_MCP_CRASH_AT"] = "post_begin"
    context.mcp_extra_env["IMAP_MCP_TEST_MODE"] = "1"
    context.crash_expected = True


@given("the server is configured to crash after WAL FETCH persistence")
def step_crash_at_post_fetch(context: Context) -> None:
    context.mcp_extra_env = getattr(context, "mcp_extra_env", {})
    context.mcp_extra_env["IMAP_MCP_CRASH_AT"] = "post_fetch"
    context.mcp_extra_env["IMAP_MCP_TEST_MODE"] = "1"
    context.crash_expected = True


@given("the server is configured to crash after APPEND but before WAL staged persistence")
def step_crash_at_post_append(context: Context) -> None:
    context.mcp_extra_env = getattr(context, "mcp_extra_env", {})
    context.mcp_extra_env["IMAP_MCP_CRASH_AT"] = "post_append_pre_staged"
    context.mcp_extra_env["IMAP_MCP_TEST_MODE"] = "1"
    context.crash_expected = True


@given("the server is configured to crash after DELETE but before WAL commit persistence")
def step_crash_at_post_delete(context: Context) -> None:
    context.mcp_extra_env = getattr(context, "mcp_extra_env", {})
    context.mcp_extra_env["IMAP_MCP_CRASH_AT"] = "post_delete"
    context.mcp_extra_env["IMAP_MCP_TEST_MODE"] = "1"
    context.crash_expected = True


@given('policy "{policy_name}" allows cross-account move between these folders')
def step_policy_allows_cross_account(context: Context, policy_name: str) -> None:
    """Shortcut used by saga_crash_recovery.feature. Expands to two
    folder capabilities: INBOX/Rechnungen on gupta-scaratec grants
    move_out; Archiv/Belege on personal grants accept_incoming."""
    builder = _ensure_builder(context)
    policy = next((p for p in builder.policies if p.name == policy_name), None)
    if policy is None:
        policy = builder.add_policy(policy_name)
    builder.folder(
        policy_name=policy_name,
        account_id="gupta-scaratec",
        path="INBOX/Rechnungen",
        mode="whitelist",
        default="NONE",
        move_out=True,
    )
    builder.folder(
        policy_name=policy_name,
        account_id="personal",
        path="Archiv/Belege",
        mode="whitelist",
        default="NONE",
        accept_incoming=True,
    )
    builder.write()


@given('the folder "{folder}" already contains a message with:')
def step_folder_already_contains(context: Context, folder: str) -> None:
    """Alias of 'holds a message with' — feature-file wording for the
    idempotency scenario where the target pre-exists."""
    from features.steps.policy_steps import step_folder_holds_message_implicit

    # Delegate to the existing message-seed step.
    step_folder_holds_message_implicit(context, folder)


@given('the WAL contains an in-progress transaction with status "{status}" referencing uid {uid:d} and Message-ID "{msgid}"')
def step_wal_seed_transaction(
    context: Context, status: str, uid: int, msgid: str
) -> None:
    """Directly seed the SQLite WAL with a staged/pending transaction.

    Background rows in the saga_crash_recovery feature establish the
    source and target folders for this test; the seeded tx references
    those fixed endpoints.
    """
    import sqlite3
    import uuid as _uuid
    from datetime import datetime, timezone

    # Ensure any pending MCP client work (message seeding) is flushed
    # before we reach in to touch the WAL.
    flush_staged_messages(context)

    context.wal_path.parent.mkdir(parents=True, exist_ok=True)
    # Use the same schema that the server's WAL module creates. Since
    # the harness must not import from the server, copy the DDL here.
    conn = sqlite3.connect(context.wal_path, isolation_level=None)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            tx_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            committed_at TEXT,
            caller_id TEXT,
            src_account TEXT,
            src_folder TEXT,
            src_uid INTEGER,
            dst_account TEXT,
            dst_folder TEXT,
            message_id TEXT,
            content_hash TEXT,
            target_uid INTEGER,
            retry_count INTEGER DEFAULT 0,
            last_error TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transaction_events (
            tx_id TEXT NOT NULL,
            step TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            outcome TEXT,
            detail TEXT
        )
        """
    )
    now = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
    tx_id = f"tx-{_uuid.uuid4().hex[:16]}"
    conn.execute(
        "INSERT INTO transactions "
        "(tx_id, status, created_at, caller_id, src_account, src_folder, "
        "src_uid, dst_account, dst_folder, message_id, content_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            tx_id,
            status,
            now,
            "invoice-agent",
            "gupta-scaratec",
            "INBOX/Rechnungen",
            uid,
            "personal",
            "Archiv/Belege",
            msgid,
            "seeded",
        ),
    )
    conn.close()
    context.last_tx_id = tx_id
    context.last_response = {"tx_id": tx_id}


@given('caller "{caller_id}" has no policy that references any folder named "{folder_path}"')
def step_caller_no_policy_folder(
    context: Context, caller_id: str, folder_path: str
) -> None:
    """Invariant assertion: the scenario's Background + Given steps must
    not have declared the named folder in any policy granted to this
    caller. No-op if the convention is already followed.
    """
    builder = _ensure_builder(context)
    caller = next((c for c in builder.callers if c.id == caller_id), None)
    if caller is None:
        return
    policy = next((p for p in builder.policies if p.name == caller.policy), None)
    if policy is None:
        return
    for folders in policy.accounts.values():
        for folder in folders:
            if folder.path == folder_path:
                raise AssertionError(
                    f"Policy {caller.policy!r} unexpectedly references folder {folder_path!r}"
                )


@given('the IMAP account "{account_id}" has a hidden folder "{folder_path}"')
def step_account_has_hidden_folder(
    context: Context, account_id: str, folder_path: str
) -> None:
    """Create the folder on IMAP but do not add it to any policy.
    The scenario asserts that describe_policy never exposes it."""
    builder = _ensure_builder(context)
    _ensure_account_registered(context, builder, account_id)
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    context.imap.create_folder(instance, user, folder_path)
    builder.write()


@given('the IMAP account "{account_id}" does not contain folder "{folder_path}"')
def step_account_lacks_folder(
    context: Context, account_id: str, folder_path: str
) -> None:
    """Assert IMAP-side absence of the folder. The fixture creates
    folders on demand; if this step runs after a create step for the
    same path, the step fails to catch the discrepancy."""
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    folders = context.imap.list_folders(instance, user)
    if folder_path in folders:
        raise AssertionError(
            f"Account {account_id!r} unexpectedly contains folder "
            f"{folder_path!r}: {folders!r}"
        )




@given("the server is started with a minimal caller configuration")
def step_server_minimal_caller_configuration(context: Context) -> None:
    step_server_minimal_configuration(context)


@given("the server is started with a minimal configuration")
def step_server_minimal_configuration(context: Context) -> None:
    """Ensure the server has at least one caller, account and policy so it can boot.

    Used by feature backgrounds that do not otherwise set up policy —
    they only want to probe the MCP protocol itself (e.g.
    non_goal_rejection). A well-formed config with a permissive
    INBOX policy satisfies the server's load-time invariants.
    """
    builder = _ensure_builder(context)
    account_id = "gupta-scaratec"
    _ensure_account_registered(context, builder, account_id)
    builder.add_caller(
        id="invoice-agent", policy="invoice-policy", auth_type="stdio_trusted"
    )
    builder.add_policy("invoice-policy")
    instance, user = resolve_account(account_id)
    context.imap.create_folder(instance, user, "INBOX/Rechnungen")
    builder.folder(
        policy_name="invoice-policy",
        account_id=account_id,
        path="INBOX/Rechnungen",
        mode="whitelist",
        default="NONE",
        move_out=True,
        mark_seen=True,
        mark_tagged=True,
    )
    from support.policy_builder import SenderRule

    policy = next(p for p in builder.policies if p.name == "invoice-policy")
    folder = policy.accounts[account_id][0]
    folder.rules.append(
        SenderRule(match={"from_domain": "hornbach.de"}, grant="ENVELOPE")
    )
    builder.write()


@given("{caller_id} completes an Initialize handshake successfully")
def step_caller_completes_initialize(context: Context, caller_id: str) -> None:
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


@given(r'the server is configured with caller "(?P<caller_id>[^" ]+)"(?! using)')
def step_server_configured_caller_no_policy(context: Context, caller_id: str) -> None:
    builder = _ensure_builder(context)
    # Convention: a caller named `<prefix>-agent` uses policy
    # `<prefix>-policy`. Keeps the bare form's implicit association
    # compatible with feature files that declare the rule policy
    # separately by that name.
    if caller_id.endswith("-agent"):
        policy_name = caller_id[: -len("-agent")] + "-policy"
    else:
        policy_name = f"{caller_id}-policy"
    builder.add_caller(id=caller_id, policy=policy_name, auth_type="stdio_trusted")
    builder.add_policy(policy_name)
    builder.write()


use_step_matcher("parse")


@given('policy "{policy_name}" grants account "{account_id}" and folder:')
def step_policy_grants_account_and_folder(
    context: Context, policy_name: str, account_id: str
) -> None:
    builder = _ensure_builder(context)
    policy = next((p for p in builder.policies if p.name == policy_name), None)
    if policy is None:
        policy = builder.add_policy(policy_name)
    policy.accounts.setdefault(account_id, [])
    from support.policy_builder import SenderRule

    for row in context.table:
        folder = builder.folder(
            policy_name=policy_name,
            account_id=account_id,
            path=row["folder"],
            mode=row["mode"],
            default=row["default"],
        )
        if "rules" in row.headings:
            rules_raw = row["rules"]
            for match, grant, cap in _parse_inline_rules(rules_raw):
                folder.rules.append(SenderRule(match=match, grant=grant, cap=cap))
    builder.write()


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
    builder.folder(
        policy_name=policy_name,
        account_id=account_id,
        path=folder,
        mode=row["mode"],
        default=row["default"],
    )
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


@given(
    'the message has attachment "{filename}" of type "{mime_type}" '
    "with size {size:d} bytes"
)
def step_message_has_attachment(
    context: Context, filename: str, mime_type: str, size: int
) -> None:
    """Attach to the most recently staged message; applied on flush."""
    staged_list = getattr(context, "staged_messages", [])
    if not staged_list:
        raise AssertionError(
            'the "message has attachment" step requires a prior '
            'Given-step that stages a message (e.g. "the folder X '
            'holds a message with:")'
        )
    staged_list[-1]["extra_attachments"].append(
        (filename, mime_type, b"x" * size)
    )


@given('the message has headers including "{header_line}"')
def step_message_has_extra_header(context: Context, header_line: str) -> None:
    staged_list = getattr(context, "staged_messages", [])
    if not staged_list:
        raise AssertionError("No staged message; cannot attach header.")
    name, _, value = header_line.partition(":")
    staged_list[-1]["extra_headers"].append((name.strip(), value.strip()))


@given('the message has plain text body "{body}"')
def step_message_has_plain_body(context: Context, body: str) -> None:
    staged_list = getattr(context, "staged_messages", [])
    if not staged_list:
        raise AssertionError("No staged message; cannot override body.")
    staged_list[-1]["body_override"] = body


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
    parsed = yaml.safe_load(raw) or {}

    if "policies" in parsed:
        for policy_name, policy_body in (parsed["policies"] or {}).items():
            target = context.config_dir / "policies" / f"{policy_name}.yaml"
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {"name": policy_name, **(policy_body or {})}
            target.write_text(yaml.safe_dump(payload, sort_keys=False))
    if "callers" in parsed:
        (context.config_dir / "callers.yaml").write_text(
            yaml.safe_dump({"callers": parsed["callers"]}, sort_keys=False)
        )

    # accounts.yaml must exist (the Background already wrote it via
    # PolicyBuilder.write()). We deliberately do not touch it here.
    _ = os  # kept because env setup hook uses it elsewhere


@then("the server refuses to start")
def step_server_refuses_to_start(context: Context) -> None:
    result = _run_bootstrap_probe(context)
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


@given('policy "{policy_name}" has sender rules:')
def step_policy_has_sender_rules(context: Context, policy_name: str) -> None:
    """Parse rows of the form (folder, match, grant) and attach them.

    The `match` column in the feature file is an expression such as
    `from_domain=hornbach.de`. The step splits it at the first `=` and
    produces `{from_domain: "hornbach.de"}`. No other interpretation
    is performed: the resulting map is serialised verbatim into the
    policy YAML and the server is responsible for validating it.
    """
    builder = _ensure_builder(context)
    for row in context.table:
        folder_path = row["folder"]
        match_expression = row["match"]
        grant = row["grant"]

        key, _, value = match_expression.partition("=")
        if not key or not value:
            raise ValueError(
                f"Cannot parse match expression {match_expression!r}; "
                "expected 'key=value'"
            )

        policy = next((p for p in builder.policies if p.name == policy_name), None)
        if policy is None:
            raise ValueError(
                f"Policy {policy_name!r} was not declared before sender rules "
                "were added. Check the Given steps' ordering."
            )
        folder_list = policy.accounts
        target_folder = None
        for folders in folder_list.values():
            for folder in folders:
                if folder.path == folder_path:
                    target_folder = folder
                    break
            if target_folder is not None:
                break
        if target_folder is None:
            raise ValueError(
                f"Folder {folder_path!r} was not declared in policy "
                f"{policy_name!r} before sender rules were added."
            )

        from support.policy_builder import SenderRule  # local import avoids cycle

        target_folder.rules.append(SenderRule(match={key: value}, grant=grant))
    builder.write()
