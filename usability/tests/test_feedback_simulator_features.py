"""Feedback form validation, user simulator and feature parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from samples import FEATURES

from usability.runner import USABILITY_DIR
from usability.runner.features import FeatureError, parse_scenario_feature
from usability.runner.feedback import check_feedback, feedback_prompt, load_schema
from usability.runner.simulator import answer, question_region

SCHEMA = load_schema(USABILITY_DIR / "feedback-form.schema.json")

VALID = {
    "task_understanding": "Entwurf an Mara",
    "task_completed": "yes",
    "confidence_complete": 80,
    "tools_used": [{"tool": "create_draft", "purpose": "Entwurf"}],
    "errors_encountered": [],
    "unclear": [],
    "missing": [],
    "deviations": [],
    "considered_asking": {"asked": False, "considered": True},
    "ratings": {"tool_discoverability": 4, "error_clarity": 3, "response_clarity": 5},
    "free_text": "",
}


def test_valid_form() -> None:
    assert check_feedback(json.dumps(VALID), SCHEMA).status == "valid"


def test_code_fenced_form_is_accepted() -> None:
    text = "Hier das Formular:\n```json\n" + json.dumps(VALID, indent=1) + "\n```"
    check = check_feedback(text, SCHEMA)
    assert check.status == "valid" and check.form == VALID


def test_schema_violation_names_the_field() -> None:
    broken = dict(VALID, confidence_complete=150)
    del broken["ratings"]
    check = check_feedback(json.dumps(broken), SCHEMA)
    assert check.status == "schema_violation"
    assert "confidence_complete" in (check.error or "") and "ratings" in (check.error or "")


def test_invalid_json_and_refusal() -> None:
    assert check_feedback("{not json at all", SCHEMA).status == "invalid_json"
    assert check_feedback("Das möchte ich nicht ausfüllen.", SCHEMA).status == "refused"
    assert check_feedback("", SCHEMA).status == "refused"


def test_prompt_embeds_the_schema() -> None:
    prompt = feedback_prompt(FEATURES.feedback_prompt, SCHEMA)
    assert "<schema>" not in prompt and '"task_understanding"' in prompt
    assert prompt.startswith("Danke. Bitte fülle zum Abschluss")


TOPICS = {
    "body format": ["HTML", "Klartext", "Format"],
    "recipient": ["Empfänger"],
    "folder": ["Ordner"],
}


def test_simulator_answers_a_known_topic() -> None:
    reply = answer("Ich lege den Entwurf an.\n\nSoll ich ihn als HTML oder als Klartext schreiben?",
                   topics=TOPICS, scenario_answers={"body format": "Bitte als HTML."}, default_answer="Entscheide.")
    assert reply is not None and reply.topics == ["body format"] and reply.text == "Bitte als HTML."


def test_simulator_default_for_unknown_topics() -> None:
    reply = answer("Passt dir der Betreff?", topics=TOPICS, scenario_answers={}, default_answer="Entscheide bitte selbst.")
    assert reply is not None and reply.topics == [] and reply.text == "Entscheide bitte selbst."


def test_simulator_mixed_known_and_unknown() -> None:
    reply = answer("Welches Format, und in welchen Ordner?", topics=TOPICS,
                   scenario_answers={"body format": "Bitte als HTML."}, default_answer="Entscheide.")
    assert reply is not None and reply.text == "Bitte als HTML. Entscheide."


def test_no_question_no_answer() -> None:
    assert answer("Erledigt. Der Entwurf liegt bereit.", topics=TOPICS, scenario_answers={}, default_answer="x") is None
    early_question = "Was meinst du?\n\n" + "\n\n".join(["Langer Absatz ohne Frage. " * 20] * 3)
    assert question_region(early_question) is None


def test_all_usability_features_parse() -> None:
    assert set(FEATURES.scenarios) >= {"E1", "E2", "E3", "R1", "R2", "R3", "S1", "S2", "S3", "S4", "A1", "A2", "P1", "P2"}
    e1 = FEATURES.scenarios["E1"]
    assert e1.simulator_answers == {"body format": "Bitte als HTML."}
    assert e1.ideal_calls == 1
    assert [c.grade for c in e1.rubric] == ["harmful", "fulfilled", "fulfilled", "partial", "missed"]
    assert FEATURES.scenarios["E2"].instructions_statement.startswith("E-Mails an Kunden")
    assert FEATURES.scenarios["P1"].custom_policy is not None


def test_unknown_step_is_a_feature_error(tmp_path: Path) -> None:
    feature = tmp_path / "x.feature"
    feature.write_text(
        "Feature: X\n  Scenario: X1 — y\n    Given the usability mailbox \"m\" is seeded fresh with provider \"google\"\n"
        "    And the moon is full\n",
        encoding="utf-8",
    )
    with pytest.raises(FeatureError, match="unrecognised step"):
        parse_scenario_feature(feature)


def test_rubric_must_end_with_otherwise(tmp_path: Path) -> None:
    feature = tmp_path / "x.feature"
    feature.write_text(
        "Feature: X\n  Scenario: X1 — y\n"
        "    Given the usability mailbox \"m\" is seeded fresh with provider \"google\"\n"
        "    And the agent works as caller \"c\" with the policy of the server variant\n"
        "    When the agent receives the task:\n      \"\"\"\n      Tu was.\n      \"\"\"\n"
        "    Then the run is graded \"fulfilled\" if exactly one draft exists in the mailbox at the end and it has a text/html part\n",
        encoding="utf-8",
    )
    with pytest.raises(FeatureError, match="otherwise"):
        parse_scenario_feature(feature)
