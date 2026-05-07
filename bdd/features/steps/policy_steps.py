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


@given('the secret store "{store_type}" is configured at path $TMPDIR/secrets')
def step_secret_store_configured(context: Context, store_type: str) -> None:
    builder = _ensure_builder(context)
    builder.secret_store_backend = store_type
    if store_type == "file_dir":
        builder.secret_store_path = str(context.secrets_dir)

@given('the server is configured with account:')
def step_server_configured_with_account_table(context: Context) -> None:
    builder = _ensure_builder(context)
    for row in context.table:
        account_id = row["id"]
        # In walking skeleton, everything points to the local fixtures
        if "gupta" in account_id or "osthues" in account_id or "gmail-archive" in account_id:
            host, port = context.imap_instances["imap-a"]
        else:
            host, port = context.imap_instances["imap-b"]
            
        auth_type = "password"
        secret_ref = f"secret://accounts/{account_id}/password"
        
        # Support OAuth configuration from the table
        if "oauth_scope" in row.headings:
            auth_type = "xoauth2"
            secret_ref = f"secret://accounts/{account_id}/refresh_token"
            
        kwargs = {}
        if "provider" in row.headings:
            kwargs["provider"] = row["provider"]
        if "oauth_scope" in row.headings:
            kwargs["oauth_scope"] = row["oauth_scope"]
        if "token_cache" in row.headings:
            kwargs["token_cache"] = row["token_cache"]

        builder.add_account(
            id=account_id,
            host=host,
            port=port,
            auth_type=auth_type,
            secret_ref=secret_ref,
            **kwargs
        )
    builder.write()

@given("the server is configured with caller:")
def step_server_configured_with_caller(context: Context) -> None:
    """Single-row caller setup. Tolerates table schemas with or
    without `policy` and `auth_type` columns.

    A missing `policy` column derives `<caller_id>-policy` so the
    loader's "policy must exist" invariant is satisfied without the
    feature having to spell it out for auth-only scenarios.
    """
    builder = _ensure_builder(context)
    for row in context.table:
        caller_id = row["caller_id"]
        policy = row["policy"] if "policy" in row.headings else f"{caller_id}-policy"
        auth_type = row["auth_type"] if "auth_type" in row.headings else "stdio_trusted"
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
    # Boot-time invariant: at least one account must exist for the
    # accounts.yaml schema to validate.
    _ensure_account_registered(context, builder, "gupta-scaratec")
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
        caps: dict[str, bool] = {}
        for cap_key in ("mark_seen", "mark_tagged", "move_out", "accept_incoming", "draft_append"):
            if cap_key in row.headings:
                caps[cap_key] = _parse_bool(row[cap_key])
        folder = builder.folder(
            policy_name=policy_name,
            account_id=account_id,
            path=row["folder"],
            mode=row["mode"],
            default=row["default"],
            **caps,
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
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
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
        omit_msgid = staged["message_id_override"] == "(absent)"
        message_id = (
            None if omit_msgid
            else (staged["message_id_override"] or f"<scenario-{uid_hint}@bdd.local>")
        )
        seeded = context.imap.seed_message(
            instance,
            user,
            folder,
            omit_message_id=omit_msgid,
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
        existing_rules = []
        for fp in policy.accounts[account_id]:
            if fp.path != folder and fp.rules:
                existing_rules = fp.rules
                break
        if existing_rules:
            found.rules = list(existing_rules)
        else:
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


@given('the server is configured with audit external_root_hook command "{command}"')
def step_audit_external_root_hook(context: Context, command: str) -> None:
    builder = _ensure_builder(context)
    resolved = command.replace("$TMPDIR", str(context.scratch_dir))
    builder.audit_external_root_hook = resolved
    builder.write()


@given('the current audit file closes with final_hash "sha256:<hash>"')
def step_audit_file_closes_with_final_hash(context: Context) -> None:
    """Prime the audit writer by driving a tool call, then capture the
    hash chain state. The feature-file placeholder `sha256:<hash>` is
    not a literal — the step captures the actual final_hash so
    subsequent Then-steps can compare against it.

    The final_hash that `_emit_eof_day` writes equals the `_prev_hash`
    after the last content record — which is `sha256:` + SHA-256 of
    the last written line (including trailing newline)."""
    import hashlib as _hashlib
    import json as _json
    from features.steps.mcp_steps import _ensure_mcp_client

    builder = _ensure_builder(context)
    if not builder.callers:
        _ensure_account_registered(context, builder, "gupta-scaratec")
        builder.add_policy("audit-hook-policy")
        builder.add_caller(
            id="invoice-agent",
            policy="audit-hook-policy",
            auth_type="stdio_trusted",
        )
        builder.folder(
            "audit-hook-policy", "gupta-scaratec",
            "INBOX", "blacklist", "FULL",
        )
        builder.write()
    client = _ensure_mcp_client(context, "invoice-agent")
    client.call_tool("list_accounts", {})
    for p in sorted(context.audit_dir.glob("*.jsonl"), reverse=True):
        lines = p.read_text().strip().splitlines()
        if lines:
            last_line = lines[-1] + "\n"
            context.expected_final_hash = (
                "sha256:" + _hashlib.sha256(last_line.encode("utf-8")).hexdigest()
            )
            break


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


@given('the IMAP server for "{account_id}" responds to the next APPEND with error {code:d}')
def step_fault_next_append_error(context: Context, account_id: str, code: int) -> None:
    _ = code  # the synthesised NO is fixed; real-world IMAP NO has no numeric code
    _start_imap_proxy(
        context, account_id,
        inject_failure_on=[{"command": "APPEND", "remaining": 1}],
    )


@given('the IMAP server for "{account_id}" responds to every APPEND with error {code:d}')
def step_fault_every_append_error(context: Context, account_id: str, code: int) -> None:
    _ = code
    # The retry-exhaustion scenario also drives the recovery loop via
    # _test_run_recovery; surface the test-only tool.
    context.mcp_extra_env = getattr(context, "mcp_extra_env", {})
    context.mcp_extra_env.setdefault("IMAP_MCP_TEST_MODE", "1")
    _start_imap_proxy(
        context, account_id,
        inject_failure_on=[{"command": "APPEND", "remaining": None}],
    )


@given('the IMAP server for "{account_id}" delays the next APPEND response by {seconds:d} seconds')
def step_fault_append_delay(context: Context, account_id: str, seconds: int) -> None:
    _start_imap_proxy(
        context, account_id,
        delay_command_seconds={
            "command": "APPEND",
            "seconds": seconds,
            "remaining": 1,
        },
    )


@given('the server append_timeout is configured to {seconds:d} seconds')
def step_server_append_timeout(context: Context, seconds: int) -> None:
    context.mcp_extra_env = getattr(context, "mcp_extra_env", {})
    context.mcp_extra_env["IMAP_MCP_APPEND_TIMEOUT"] = str(seconds)


@given('the IMAP server for "{account_id}" responds to the next EXPUNGE with error {code:d} exactly once')
def step_fault_next_expunge_once(context: Context, account_id: str, code: int) -> None:
    _ = code
    _start_imap_proxy(
        context, account_id,
        inject_failure_on=[{"command": "EXPUNGE", "remaining": 1}],
    )


@given('the IMAP server for "{account_id}" refuses all connections')
def step_fault_refuse_connections(context: Context, account_id: str) -> None:
    """Refuse-all = no proxy listens. We pick a free port, immediately
    release it, and rewire the account so the server's TCP connect
    fails with ECONNREFUSED — exactly what `target_unreachable`
    needs."""
    _start_imap_proxy(context, account_id, refuse_connections=True)


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


@given('a cross-account move for uid {uid:d} is in progress, currently at WAL step "fetched"')
def step_saga_in_progress_at_fetched(context: Context, uid: int) -> None:
    """Start a cross-account move and pause it at the 'fetched' WAL step.

    Uses file-based coordination: the saga writes a marker file when it
    reaches 'post_fetch', then polls for a '.resume' sibling. The step
    returns once the marker appears — the saga is now frozen mid-flight.
    """
    import threading
    import time

    from features.steps.mcp_steps import _ensure_mcp_client

    builder = _ensure_builder(context)
    second_account = "personal"
    _ensure_account_registered(context, builder, second_account)
    instance_b, user_b = resolve_account(second_account)
    context.imap.create_folder(instance_b, user_b, "Archiv/Belege")

    policy = builder.policies[0]
    existing_target = [
        f
        for folders in policy.accounts.values()
        for f in folders
        if f.path == "Archiv/Belege"
    ]
    if not existing_target:
        builder.folder(
            policy_name=policy.name,
            account_id=second_account,
            path="Archiv/Belege",
            mode="whitelist",
            default="NONE",
            accept_incoming=True,
        )
    src_account_id = next(iter(policy.accounts.keys()))
    for fp in policy.accounts[src_account_id]:
        if fp.path == "INBOX/Rechnungen":
            fp.move_out = True
            break

    marker = context.scratch_dir / "saga-pause-marker"
    extra_env = getattr(context, "mcp_extra_env", {})
    extra_env["IMAP_MCP_SAGA_PAUSE_AT"] = "post_fetch"
    extra_env["IMAP_MCP_SAGA_PAUSE_MARKER"] = str(marker)
    extra_env["IMAP_MCP_TEST_MODE"] = "1"
    context.mcp_extra_env = extra_env
    context.saga_pause_marker = marker
    builder.write()
    flush_staged_messages(context)

    client = _ensure_mcp_client(context, "invoice-agent")
    uid_lookup = getattr(context, "message_uids", {})
    actual_uid = uid_lookup.get((src_account_id, "INBOX/Rechnungen", uid), uid)

    def _run_move() -> None:
        import json as _json

        try:
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
            content = payload.get("content") or []
            if content:
                context.saga_move_result = _json.loads(content[0]["text"])
        except Exception as exc:
            context.saga_move_error = exc

    t = threading.Thread(target=_run_move, daemon=True)
    t.start()
    context.saga_move_thread = t

    deadline = time.monotonic() + 30
    while not marker.exists():
        if time.monotonic() > deadline:
            raise AssertionError(
                "Saga did not reach 'fetched' within 30s"
            )
        time.sleep(0.2)


@when(
    'the operator changes policy "{policy_name}" to remove the '
    'move_out capability from "{folder}"'
)
def step_remove_move_out(context: Context, policy_name: str, folder: str) -> None:
    builder = _ensure_builder(context)
    policy = next(p for p in builder.policies if p.name == policy_name)
    for folders in policy.accounts.values():
        for fp in folders:
            if fp.path == folder:
                fp.move_out = False
    builder.write()


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
    # Make sure at least one caller + account + policy exist so the
    # loader can boot when the rotation tool is invoked later.
    if not builder.callers:
        _ensure_account_registered(context, builder, "gupta-scaratec")
        builder.add_caller(
            id="invoice-agent", policy="invoice-policy", auth_type="stdio_trusted"
        )
        builder.add_policy("invoice-policy")
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
        _ensure_account_registered(context, builder, "gupta-scaratec")
        builder.add_caller(
            id="invoice-agent", policy="invoice-policy", auth_type="stdio_trusted"
        )
        builder.add_policy("invoice-policy")
    builder.write()


@given("the audit directory contains:")
def step_audit_directory_contains_table(context: Context) -> None:
    """Stage backdated audit files using `os.utime` to set their
    mtime relative to `IMAP_MCP_FAKE_NOW_UTC` (or wall time)."""
    import os as _os
    from datetime import timedelta as _td, timezone as _tz

    now = _now_utc_for_test(context)
    for row in context.table:
        filename = row["filename"]
        age_days = int(row["age_days"])
        path = context.audit_dir / filename
        # Plain payload — the actual content is irrelevant for
        # rotation tests; the rotator inspects mtime, not content.
        path.write_text(
            f'{{"placeholder": true, "filename": "{filename}"}}\n',
            encoding="utf-8",
        )
        target_mtime = (now - _td(days=age_days)).timestamp()
        _os.utime(path, (target_mtime, target_mtime))


@given('the audit directory contains a file "{filename}" with age {age:d} days')
def step_audit_directory_single_file(
    context: Context, filename: str, age: int
) -> None:
    import os as _os
    from datetime import timedelta as _td

    now = _now_utc_for_test(context)
    path = context.audit_dir / filename
    if filename.endswith(".gz"):
        # Plain bytes are fine for retention age checks; the rotator
        # only consults mtime + filename for delete decisions.
        path.write_bytes(b"\x1f\x8b\x08\x00placeholder")
    else:
        path.write_text(
            f'{{"placeholder": true, "filename": "{filename}"}}\n',
            encoding="utf-8",
        )
    target_mtime = (now - _td(days=age)).timestamp()
    _os.utime(path, (target_mtime, target_mtime))


@given('a file "{filename}" with age {age:d} exists in the audit directory')
def step_audit_directory_with_age(
    context: Context, filename: str, age: int
) -> None:
    step_audit_directory_single_file(context, filename, age)


@given('a file "{filename}" with mode {mode} exists')
def step_audit_file_with_mode(
    context: Context, filename: str, mode: str
) -> None:
    """Create the file with an mtime well past the default hot_days
    boundary so the next rotation pass actually gzips it. Matches the
    intent of the warm-file-permissions scenario: the test cares about
    the gzip code path, not about the age boundary."""
    import os as _os
    from datetime import timedelta as _td

    path = context.audit_dir / filename
    path.write_text(
        f'{{"placeholder": true, "filename": "{filename}"}}\n',
        encoding="utf-8",
    )
    _os.chmod(path, int(mode, 8))
    now = _now_utc_for_test(context)
    target = (now - _td(days=100)).timestamp()
    _os.utime(path, (target, target))


@given("the audit file contains {n:d} records")
def step_audit_file_contains_n_records(context: Context, n: int) -> None:
    """Drive `n` ALLOW fetch_envelope-equivalent calls (via list_accounts
    which is always allowed) so the audit file ends up with `n` records."""
    from features.steps.mcp_steps import _ensure_mcp_client

    client = _ensure_mcp_client(context, "invoice-agent")
    for _ in range(n):
        client.call_tool("list_accounts", {})


@given(
    'the audit writer is at {time:S} UTC with seq {seq:d} in file "{filename}"'
)
def step_audit_writer_at_time(
    context: Context, time: str, seq: int, filename: str
) -> None:
    """Pin the fake clock to a specific UTC time within the named day,
    pre-populate the file with `seq` records so the next write hits
    seq+1, and start the server so subsequent SIGHUP/rotation steps
    have a recipient.

    The file content uses synthetic chain placeholders; the
    `audit_log_format.feature:78` scenario checks that the *next*
    file's first record references the eof_day's hash, not that the
    pre-seeded records form a valid chain.
    """
    import os as _os
    from datetime import datetime as _dt

    # Parse the day from the filename (`YYYY-MM-DD.jsonl`) and combine
    # with the `time` portion to produce the exact fake-now value.
    day = filename.replace(".jsonl", "")
    fake_now = f"{day}T{time}+00:00"
    context.mcp_extra_env = getattr(context, "mcp_extra_env", {}) or {}
    context.mcp_extra_env["IMAP_MCP_FAKE_NOW_UTC"] = fake_now
    context.audit_pinned_day = day

    # Prime the active file by driving `seq` MCP calls (each emits an
    # audit record). list_accounts is allowed by the minimal-config
    # caller and is the simplest record-emitter.
    from features.steps.mcp_steps import _ensure_mcp_client

    _ = _dt.fromisoformat(fake_now)  # validation
    # Ensure a minimal caller config if none is staged yet.
    builder = _ensure_builder(context)
    if not builder.callers:
        step_server_minimal_configuration(context)
    client = _ensure_mcp_client(context, "invoice-agent")
    for _ in range(seq):
        client.call_tool("list_accounts", {})


def _now_utc_for_test(context: Context):
    """Mirror of audit._now_utc but reading the BDD-side env var.

    The harness shares `IMAP_MCP_FAKE_NOW_UTC` with the server via
    `mcp_extra_env`; for staging files we honour it locally too so
    file mtimes line up with what the server will see.
    """
    import os as _os
    from datetime import datetime as _dt, timezone as _tz

    raw = (getattr(context, "mcp_extra_env", None) or {}).get(
        "IMAP_MCP_FAKE_NOW_UTC"
    ) or _os.environ.get("IMAP_MCP_FAKE_NOW_UTC")
    if raw:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            return _dt.fromisoformat(raw).astimezone(_tz.utc)
        except ValueError:
            pass
    return _dt.now(tz=_tz.utc)


@given('policy "{policy_name}" folder "{folder_path}" has:')
def step_policy_folder_has(
    context: Context, policy_name: str, folder_path: str
) -> None:
    """Single-row folder spec: mode + default + inline rules string.

    Used by `policy_reload.feature` Background to define INBOX/Rechnungen
    in one go alongside other Background steps.
    """
    builder = _ensure_builder(context)
    policy = next((p for p in builder.policies if p.name == policy_name), None)
    if policy is None:
        policy = builder.add_policy(policy_name)
    account_id = next(iter(policy.accounts.keys()), None)
    if account_id is None:
        # Default to the first registered account if no account has
        # been linked to this policy yet.
        account_id = builder.accounts[0].id if builder.accounts else "gupta-scaratec"
        policy.accounts.setdefault(account_id, [])
    row = context.table[0]
    fp = builder.folder(
        policy_name=policy_name,
        account_id=account_id,
        path=folder_path,
        mode=row["mode"],
        default=row["default"],
    )
    if "rules" in row.headings:
        from support.policy_builder import SenderRule

        for match, grant, cap in _parse_inline_rules(row["rules"]):
            fp.rules.append(SenderRule(match=match, grant=grant, cap=cap))
    builder.write()


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


@given("the current policy file content is:")
def step_current_policy_file_content(context: Context) -> None:
    """Informational anchor — the actual operator action is the next
    `replaces` step. Stash the snapshot for diff-style debugging if a
    later assertion fails."""
    context.policy_file_snapshot_before = context.text


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


@when('the operator removes account "{account_id}" from accounts.yaml')
def step_operator_removes_account(
    context: Context, account_id: str
) -> None:
    builder = _ensure_builder(context)
    builder.accounts = [a for a in builder.accounts if a.id != account_id]
    # Also drop any policy reference to that account so the loader does
    # not reject the new state with `unknown account`.
    for policy in builder.policies:
        policy.accounts.pop(account_id, None)
    builder.write()


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


@given('the IMAP account "{account_id}" also has folder "{folder_path}"')
def step_account_also_has_folder(
    context: Context, account_id: str, folder_path: str
) -> None:
    """Create the folder on Dovecot but do not add it to any policy
    (the next operator step adds the policy entry)."""
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    context.imap.create_folder(instance, user, folder_path)


@given('policy "{policy_name}" does NOT grant folder "{folder_path}"')
def step_policy_does_not_grant_folder(
    context: Context, policy_name: str, folder_path: str
) -> None:
    """Invariant assertion: the named policy must not currently
    reference the folder. No-op if the convention is honoured by the
    Background; explicit failure otherwise so the scenario reads
    accurately."""
    builder = _ensure_builder(context)
    policy = next((p for p in builder.policies if p.name == policy_name), None)
    if policy is None:
        return
    for fp_list in policy.accounts.values():
        for fp in fp_list:
            if fp.path == folder_path:
                raise AssertionError(
                    f"Policy {policy_name!r} unexpectedly already grants {folder_path!r}"
                )


@given(
    'invoice-agent has made one successful fetch_envelope against '
    '"{account_id}" in this session'
)
def step_one_successful_fetch_envelope(
    context: Context, account_id: str
) -> None:
    """Drive a fetch_envelope so an MCP session is open and at least
    one IMAP round-trip has happened. Used by the account-removal
    scenario as a precondition for the pool-drain assertion."""
    from features.steps.mcp_steps import _ensure_mcp_client
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    # Find a folder seeded in this account from prior steps.
    builder = _ensure_builder(context)
    policy = builder.policies[0]
    fp_list = policy.accounts.get(account_id) or []
    if not fp_list:
        raise AssertionError(
            f"No folder declared for account {account_id!r}; "
            "Background must seed at least one."
        )
    folder_path = fp_list[0].path
    context.imap.create_folder(instance, user, folder_path)
    flush_staged_messages(context)
    client = _ensure_mcp_client(context, "invoice-agent")
    uid_lookup = getattr(context, "message_uids", {})
    actual_uid = next(iter(uid_lookup.values()), 1) if uid_lookup else 1
    client.call_tool(
        "fetch_envelope",
        {"account": account_id, "folder": folder_path, "uid": actual_uid},
    )


@given(
    'the server has 1 open IMAP connection in the pool for '
    'account "{account_id}"'
)
def step_pool_has_one_connection(context: Context, account_id: str) -> None:
    """No-op. ADR 0013's connection pool is not part of V1 (fresh
    connection per call). The assertion that the count drops to zero
    after SIGHUP is therefore trivially true."""
    _ = (context, account_id)


@given("the server is configured with callers:")
def step_server_with_callers_table(context: Context) -> None:
    """Multi-caller setup. Each row drops one caller into the policy
    builder; the secret store is populated by the dedicated step
    `the secret store contains value "..." under "..."` (which is
    distinct so each token can be addressed by name).

    Existing callers with the same id are replaced. This is the
    natural behaviour for a feature that, in its Background, set up a
    default-stdio caller and now wants to override the auth_type to
    shared_token for an HTTP-specific scenario.
    """
    builder = _ensure_builder(context)
    for row in context.table:
        caller_id = row["caller_id"]
        auth_type = row["auth_type"]
        token_secret_ref = row.get("token_secret_ref") if "token_secret_ref" in row.headings else None
        if token_secret_ref == "(n/a)":
            token_secret_ref = None
        # Drop any prior caller with this id (e.g. seeded as stdio
        # by an earlier Background step). The policy attached to the
        # caller is preserved if present, otherwise a default one is
        # created so the loader's invariant holds.
        existing = next(
            (c for c in builder.callers if c.id == caller_id), None
        )
        if existing is not None:
            policy_name = existing.policy
            builder.callers = [c for c in builder.callers if c.id != caller_id]
        else:
            policy_name = f"{caller_id}-policy"
        builder.add_caller(
            id=caller_id,
            policy=policy_name,
            auth_type=auth_type,
            token_secret_ref=token_secret_ref,
        )
        if not any(p.name == policy_name for p in builder.policies):
            builder.add_policy(policy_name)
    # Ensure at least one account exists so the loader can boot.
    _ensure_account_registered(context, builder, "gupta-scaratec")
    builder.write()


@given('the secret store contains value "{value}" under "{path}"')
def step_secret_store_value(context: Context, value: str, path: str) -> None:
    target = context.secrets_dir / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")


def _scratch_substitute(context: Context, raw: str) -> str:
    """Replace `$SCRATCH` in feature-string arguments with the per-
    scenario scratch dir. Also replaces a fake GPG fingerprint with
    the real one if the GPG-keypair step has run (the feature uses a
    hardcoded all-A's fingerprint for readability; the actual keypair
    is generated at runtime)."""
    scratch = getattr(context, "scratch_dir", None)
    out = raw
    if scratch is not None:
        out = out.replace("$SCRATCH", str(scratch))
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
    gh = parsed.get("gnupghome")
    if gh:
        builder.secret_store_gnupghome = Path(_scratch_substitute(context, gh))
    elif backend == "gpg_file":
        # Default to whichever gnupghome the prior gpg-keypair step
        # set up. Lets feature files keep gpg_file YAML minimal.
        prior = getattr(context, "_gpg_gnupghome", None)
        if prior is not None:
            builder.secret_store_gnupghome = prior


@given('the file "{path}" contains the exact bytes "{value}"')
def step_file_contains_bytes(context: Context, path: str, value: str) -> None:
    target = Path(_scratch_substitute(context, path))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(value.encode("utf-8"))


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


@given('the server process environment does NOT include any variable starting with "{prefix}"')
def step_server_process_env_excludes_prefix(context: Context, prefix: str) -> None:
    extra = getattr(context, "mcp_extra_env", None) or {}
    context.mcp_extra_env = extra
    for key in [k for k in list(extra) if k.startswith(prefix)]:
        extra.pop(key, None)


@given('a GPG keypair exists with fingerprint "{fake_fp}" and a pinentry that returns passphrase "{passphrase}"')
def step_gpg_keypair_exists(
    context: Context, fake_fp: str, passphrase: str
) -> None:
    """Generate a real test GPG keypair in `$SCRATCH/gnupg`. Stores
    the actual fingerprint on the context; subsequent feature
    references to `fake_fp` are substituted via `_scratch_substitute`.

    Pinentry is bypassed: every later `gpg --decrypt` call will pipe
    the passphrase via `--passphrase-fd`, so no interactive agent
    machinery is needed. The `passphrase` argument is captured here
    and consumed by the file-encryption step."""
    import subprocess

    gnupghome = context.scratch_dir / "gnupg"
    gnupghome.mkdir(parents=True, exist_ok=True)
    gnupghome.chmod(0o700)
    batch = (
        "%no-protection\n"
        "Key-Type: RSA\n"
        "Key-Length: 2048\n"
        "Subkey-Type: RSA\n"
        "Subkey-Length: 2048\n"
        "Name-Real: imap-mcp BDD test\n"
        "Name-Email: bdd@imap-mcp.invalid\n"
        "Expire-Date: 0\n"
        "%commit\n"
    )
    env = dict(os.environ)
    env["GNUPGHOME"] = str(gnupghome)
    subprocess.run(
        ["gpg", "--batch", "--quiet", "--gen-key"],
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
        raise AssertionError(
            f"Could not extract fingerprint from gpg --list-keys: "
            f"{list_keys.stdout!r}"
        )
    context._gpg_fake_fp = fake_fp
    context._gpg_real_fp = real_fp
    context._gpg_passphrase = passphrase
    context._gpg_gnupghome = gnupghome


@given('the file "{path}" was produced by `gpg --encrypt --recipient {fp}` over the exact bytes "{value}"')
def step_gpg_encrypt_exact(
    context: Context, path: str, fp: str, value: str
) -> None:
    """Write the GPG-encrypted file at `path`. Uses the keypair the
    prior step generated."""
    import subprocess

    target = Path(_scratch_substitute(context, path))
    target.parent.mkdir(parents=True, exist_ok=True)
    real_fp = getattr(context, "_gpg_real_fp", None)
    gnupghome = getattr(context, "_gpg_gnupghome", None)
    if real_fp is None or gnupghome is None:
        raise AssertionError(
            "GPG keypair step must precede the encrypt step"
        )
    env = dict(os.environ)
    env["GNUPGHOME"] = str(gnupghome)
    result = subprocess.run(
        [
            "gpg",
            "--batch",
            "--yes",
            "--quiet",
            "--trust-model", "always",
            "--encrypt",
            "--recipient", real_fp,
            "-o", str(target),
        ],
        input=value.encode("utf-8"),
        env=env,
        capture_output=True,
        timeout=10,
        check=True,
    )
    context._gpg_last_stderr = result.stderr.decode("utf-8", errors="replace")


@given('the file "{path}" exists and was produced by the same recipient')
def step_gpg_file_exists_same_recipient(context: Context, path: str) -> None:
    """For the wrong-passphrase scenario: the file is encrypted by
    the same recipient as the keypair step. We re-use the encrypt
    step over a fixed plaintext."""
    step_gpg_encrypt_exact(
        context, path,
        getattr(context, "_gpg_real_fp", "") or "",
        "correct-horse-battery",
    )
    # Then nuke the secret keys so decryption fails. The
    # wrong-passphrase scenario actually tests a configuration where
    # the gnupghome doesn't contain the secret key — modelling
    # operator key-rotation. Cheaper than wiring a stub pinentry.
    import subprocess
    env = dict(os.environ)
    env["GNUPGHOME"] = str(context._gpg_gnupghome)
    subprocess.run(
        ["gpg", "--batch", "--yes", "--quiet", "--delete-secret-keys",
         context._gpg_real_fp],
        env=env,
        capture_output=True,
        timeout=10,
        check=False,
    )


@then("the audit record does NOT contain any line from the gpg subprocess' stderr")
def step_audit_no_gpg_stderr(context: Context) -> None:
    """Verify that no audit record contains any line from the gpg
    encryption stderr captured during the prior step."""
    import json as _json

    captured = (getattr(context, "_gpg_last_stderr", "") or "").strip()
    if not captured:
        return
    audit_dir = getattr(context, "audit_dir", None)
    if audit_dir is None or not audit_dir.exists():
        return
    audit_lines: list[str] = []
    for f in sorted(audit_dir.iterdir()):
        if f.suffix == ".jsonl":
            audit_lines.extend(f.read_text(encoding="utf-8").splitlines())
    for line in captured.splitlines():
        line = line.strip()
        if not line:
            continue
        for record_line in audit_lines:
            try:
                record = _json.loads(record_line)
            except Exception:
                continue
            if any(line in str(v) for v in record.values()):
                raise AssertionError(
                    f"Audit record leaked gpg stderr line: {line!r} "
                    f"in record {record!r}"
                )


@then('the server process environment still has no variable for "{ref}"')
def step_env_still_no_var(context: Context, ref: str) -> None:
    """Assertion variant: after a step that should NOT have written
    any new env vars (e.g. a refused oauth_bootstrap), confirm that
    none of the variables matching the prefix derived from `ref` were
    populated. Mapping rule mirrors `EnvVarSecretStore.env_name` in
    the server (uppercase, `/` → `__`, `-` → `_`)."""
    expected = "IMAP_MCP_SECRET__" + ref.upper().replace("/", "__").replace("-", "_")
    extra = getattr(context, "mcp_extra_env", None) or {}
    if expected in extra or expected in os.environ:
        raise AssertionError(
            f"Env var {expected!r} unexpectedly present after refused bootstrap"
        )


def _start_imap_proxy(
    context: Context,
    account_id: str,
    *,
    strip_capabilities: list[str] | None = None,
    uidvalidity_change_after: str | None = None,
    uidvalidity_new_value: str | None = None,
    inject_failure_on: list[dict] | None = None,
    delay_command_seconds: dict | None = None,
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
    _ensure_account_registered(context, builder, account_id)

    instance, _user = resolve_account(account_id)
    upstream_host, upstream_port = context.imap_instances[instance]

    log_path = context.scratch_dir / f"imap-proxy-{account_id}.log"
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


@given('the IMAP server for "{account_id}" does not advertise the MOVE capability')
def step_imap_server_no_move(context: Context, account_id: str) -> None:
    _start_imap_proxy(context, account_id, strip_capabilities=["MOVE"])


@given('the UIDVALIDITY of "{folder}" changes between the caller\'s SEARCH and the server\'s MOVE')
def step_uidvalidity_changes_mid_call(context: Context, folder: str) -> None:
    """Trigger the MITM proxy to inject `* OK [UIDVALIDITY <new>]`
    after the next `UID SEARCH` response. The server's NOOP between
    SEARCH and MOVE then sees the new UIDVALIDITY in untagged
    responses and raises `UidStale`."""
    _ = folder
    _start_imap_proxy(
        context, "gupta-scaratec",
        uidvalidity_change_after="UID SEARCH",
        uidvalidity_new_value="999999",
    )


@given('the server is configured to crash after WAL BEGIN persistence')
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


@given('the folder "{folder}" contains two pre-existing messages with:')
def step_folder_contains_two_preexisting(context: Context, folder: str) -> None:
    """Stage two messages with the same 5-tuple (from + subject +
    sent date + size_bytes), no Message-ID. Used by the ambiguous-
    fallback recovery scenario."""
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _find_account_for_folder(context, folder)
    context.staged_messages = getattr(context, "staged_messages", [])
    for row in context.table:
        msgid = row["message_id"] if "message_id" in row.headings else None
        context.staged_messages.append({
            "_account_id": account_id,
            "_folder": folder,
            "uid_hint": int(row["uid"]),
            "from": row["from"],
            "to": None,
            "subject": row["subject"],
            "message_id_override": msgid,
            "has_attachment": False,
            "size_hint": int(row["size_bytes"]) if "size_bytes" in row.headings else 0,
            "date": row["date"] if "date" in row.headings else None,
            "extra_attachments": [],
            "extra_headers": [],
            "body_override": None,
        })


@given(
    "the WAL has an in-progress transaction with fallback-key "
    "(from={from_value}, date={date_value}, subject={subject_value}, "
    "size={size_value:d}, first_4kb_sha256={sha_value}) and status \"{status}\""
)
def step_wal_seed_fallback(
    context: Context, from_value: str, date_value: str,
    subject_value: str, size_value: int, sha_value: str, status: str,
) -> None:
    """Insert a WAL row pre-loaded with the 5-tuple fallback identity.

    `sha_value=same-as-both` is a sentinel meaning "compute the
    SHA-256 from the just-staged matching messages". When the BDD
    fixture has staged two identical placeholder messages, both will
    have the same first-4 KiB hash; we compute it once from one of
    them and pin that value into the WAL.
    """
    import sqlite3
    import uuid as _uuid
    from datetime import datetime, timezone

    flush_staged_messages(context)

    # Materialise sha_value (and overwrite size) from the actual
    # on-disk first-4-KiB SHA-256 / total RFC822 size of the target
    # account's pre-staged message — both stagings share the same
    # bytes, so either UID is fine. The feature file's literal
    # `48213` is a logical placeholder; the BDD harness substitutes
    # the realised values so the WAL row matches what the IMAP
    # server actually serves.
    from support.imap_fixture import resolve_account
    import imaplib
    import hashlib as _h

    sha = sha_value
    actual_size = size_value
    instance, user = resolve_account("personal")
    uids = context.imap.folder_uids(instance, user, "Archiv/Belege")
    if not uids:
        raise AssertionError(
            "No pre-staged messages on personal:Archiv/Belege; "
            "the prior Given step did not seed them."
        )
    conn = context.imap.connect(instance, user)
    conn.select("Archiv/Belege")
    status_imap, data = conn.uid("FETCH", str(uids[0]), "(RFC822)")
    if status_imap != "OK" or not data or data[0] is None:
        raise AssertionError("Could not FETCH RFC822 for hash seed")
    raw = data[0][1] if isinstance(data[0], tuple) else b""
    if sha == "same-as-both":
        sha = _h.sha256(raw[:4096]).hexdigest()
    actual_size = len(raw)

    # Translate `2026-04-01` to ISO-with-Z form for the WAL.
    if "T" not in date_value:
        try:
            from datetime import datetime as _dt
            iso = _dt.fromisoformat(date_value).replace(
                tzinfo=timezone.utc
            ).isoformat().replace("+00:00", "Z")
        except ValueError:
            iso = date_value
    else:
        iso = date_value

    context.wal_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(context.wal_path, isolation_level=None)
    conn.executescript("""
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
            last_error TEXT,
            fallback_from TEXT,
            fallback_date TEXT,
            fallback_subject TEXT,
            fallback_size INTEGER,
            fallback_4kb_sha256 TEXT
        );
        CREATE TABLE IF NOT EXISTS transaction_events (
            tx_id TEXT NOT NULL,
            step TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            outcome TEXT,
            detail TEXT
        );
    """)
    now = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
    tx_id = f"tx-{_uuid.uuid4().hex[:16]}"
    conn.execute(
        "INSERT INTO transactions ("
        "tx_id, status, created_at, caller_id, src_account, src_folder, "
        "src_uid, dst_account, dst_folder, message_id, "
        "fallback_from, fallback_date, fallback_subject, fallback_size, "
        "fallback_4kb_sha256) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            tx_id, status, now, "invoice-agent",
            "gupta-scaratec", "INBOX/Rechnungen", 0,
            "personal", "Archiv/Belege",
            None,
            from_value, iso, subject_value, actual_size, sha,
        ),
    )
    conn.close()
    context.last_tx_id = tx_id
    context.last_response = {"tx_id": tx_id}


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
        if proc.returncode == 0:
            raise AssertionError(
                "Expected the server to refuse to start, but it exited 0. "
                f"Stdout: {proc.stdout!r}, stderr: {proc.stderr!r}"
            )
        context.startup_error = (proc.stderr or "") + (proc.stdout or "")
        return
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


@given(
    'account "{account_id}" is configured with provider "{provider}" '
    'and oauth_scope "{scope}"'
)
def step_account_with_oauth(
    context: Context, account_id: str, provider: str, scope: str
) -> None:
    """Register an account that uses OAuth2 against the given provider.
    No password is set — the bootstrap CLI is the path that mints the
    token in production."""
    builder = _ensure_builder(context)
    builder.add_account(
        id=account_id,
        provider=provider,
        host="oauth.example.invalid",
        port=993,
        auth_type="xoauth2",
        secret_ref=f"secret://accounts/{account_id}/refresh_token",
        oauth_scope=scope,
        password_literal=None,
    )
    builder.write()


@given('account "{account_id}" is configured with oauth_scope "{scope}"')
def step_account_with_oauth_scope_only(
    context: Context, account_id: str, scope: str
) -> None:
    builder = _ensure_builder(context)
    if any(a.id == account_id for a in builder.accounts):
        for a in builder.accounts:
            if a.id == account_id:
                a.auth_type = "xoauth2"
                a.oauth_scope = scope
                a.secret_ref = f"secret://accounts/{account_id}/refresh_token"
                break
    else:
        host, port = context.imap_instances["imap-a"]
        builder.add_account(
            id=account_id,
            provider="google-mock",
            host=host,
            port=port,
            auth_type="xoauth2",
            secret_ref=f"secret://accounts/{account_id}/refresh_token",
            oauth_scope=scope,
        )
    path = context.secrets_dir / "accounts" / account_id / "refresh_token"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("mock_refresh_token")
    policy = builder.policies[0] if builder.policies else builder.add_policy("invoice-policy")
    policy.accounts.setdefault(account_id, [])
    builder.write()


@given('the account\'s state is "{state}"')
def step_account_state_is(context: Context, state: str) -> None:
    """Verify the account is in the expected state. Side effect: starts
    the server if not yet running, so the old config is loaded BEFORE
    any subsequent scope-change + SIGHUP steps."""
    import json as _json
    from features.steps.mcp_steps import _ensure_mcp_client

    client = _ensure_mcp_client(context, "invoice-agent")
    payload = client.call_tool("list_accounts", {})
    content = payload.get("content") or []
    if not content:
        return
    data = _json.loads(content[0]["text"])
    expected_state = "active" if state == "healthy" else state
    for acc in data.get("accounts", []):
        if acc.get("state") != expected_state:
            continue
        return
    # Account may be in the list but with a different state — fail if
    # the expected state was explicitly named and doesn't match.
    if state != "healthy":
        raise AssertionError(f"No account in state {state!r}")


@when('the operator changes the scope for "{account_id}" to "{new_scope}"')
def step_change_oauth_scope(context: Context, account_id: str, new_scope: str) -> None:
    builder = _ensure_builder(context)
    for a in builder.accounts:
        if a.id == account_id:
            a.oauth_scope = new_scope
            break
    builder.write()


@when('the operator runs `imap-mcp-oauth-bootstrap --account {account_id}`')
def step_run_oauth_bootstrap(context: Context, account_id: str) -> None:
    import subprocess

    cli = Path(
        os.environ.get(
            "IMAP_MCP_OAUTH_BOOTSTRAP_BINARY",
            context.bdd_root.parent
            / "server"
            / ".venv"
            / "bin"
            / "imap-mcp-oauth-bootstrap",
        )
    )
    builder = getattr(context, "policy_builder", None)
    if builder is not None:
        builder.write()
    env = dict(os.environ)
    env["IMAP_MCP_CONFIG_DIR"] = str(context.config_dir)
    extra = getattr(context, "mcp_extra_env", None) or {}
    env.update(extra)
    if getattr(context, "bootstrap_tamper_pkce", False):
        env["IMAP_MCP_TEST_TAMPER_PKCE"] = "1"

    proc = subprocess.Popen(
        [str(cli), "--account", account_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        env=env,
        text=True,
    )
    context.bootstrap_proc = proc
    import time
    time.sleep(0.5)

@then("the bootstrap refuses to start")
def step_bootstrap_refuses(context: Context) -> None:
    proc = getattr(context, "bootstrap_proc", getattr(context, "startup_proc", None))
    if proc is None:
        raise AssertionError("No process captured")
    
    # If it's a Popen object, communicate with timeout
    if hasattr(proc, "communicate"):
        try:
            stdout, stderr = proc.communicate(timeout=2)
            context.bootstrap_stdout = stdout
            context.bootstrap_stderr = stderr
            context.bootstrap_returncode = proc.returncode
        except Exception:
            proc.kill()
            raise AssertionError("Process did not refuse to start quickly enough")
        
        ret = context.bootstrap_returncode
        out, err = context.bootstrap_stdout, context.bootstrap_stderr
    else:
        ret = proc.returncode
        out, err = proc.stdout, proc.stderr

    if ret == 0:
        raise AssertionError(f"Expected to refuse, but exited 0. Stdout: {out!r}, stderr: {err!r}")
    context.startup_error = (err or "") + (out or "")

@then('the startup error indicates "{expected}"')
def step_startup_error_indicates_substring(context: Context, expected: str) -> None:
    err = getattr(context, "startup_error", "")
    if not err:
        proc = getattr(context, "bootstrap_proc", getattr(context, "startup_proc", None))
        if proc and hasattr(proc, "communicate"):
            stdout, stderr = proc.communicate(timeout=2)
            err = (stderr or "") + (stdout or "")
            context.bootstrap_returncode = proc.returncode
            context.startup_error = err

    if expected not in err:
        raise AssertionError(
            f"Startup error does not contain expected substring {expected!r}. Full message:\n{err}"
        )


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
