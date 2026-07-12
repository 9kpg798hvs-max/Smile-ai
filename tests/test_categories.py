"""Tests for emergency signals and the findings → SmileFlow category mapping."""

from triage.rules import categorize, triage
from triage.schema import (
    Category,
    Color,
    ControlStatus,
    Findings,
    QuestionTopic,
    Severity,
    Symptom,
    SymptomCategory,
    Trajectory,
)


def sym(category, severity=Severity.UNSTATED, quote="..."):
    return Symptom(
        quote=quote, category=category, severity=severity,
        trajectory=Trajectory.UNSTATED, control=ControlStatus.UNSTATED,
    )


def run(f: Findings) -> tuple[Color, Category]:
    color = triage(f).color
    return color, categorize(f, color)


# --- emergency signals -----------------------------------------------------------

def test_emergency_language_is_red_and_emergency_category():
    f = Findings(emergency_signals=["hard to swallow"], states_doing_well=True)
    color, cat = run(f)
    assert color is Color.RED
    assert cat is Category.EMERGENCY


def test_emergency_outranks_every_other_category():
    f = Findings(
        emergency_signals=["throat is swelling"],
        symptoms=[sym(SymptomCategory.SWELLING), sym(SymptomCategory.PAIN)],
        questions=["should I take more ibuprofen?"],
        question_topics=[QuestionTopic.MEDICATION],
    )
    _, cat = run(f)
    assert cat is Category.EMERGENCY


# --- category mapping ---------------------------------------------------------------

def test_swelling_category():
    color, cat = run(Findings(symptoms=[sym(SymptomCategory.SWELLING)]))
    assert (color, cat) == (Color.RED, Category.SWELLING)


def test_medication_reaction_maps_to_medication_with_red_urgency():
    color, cat = run(Findings(symptoms=[sym(SymptomCategory.MEDICATION_REACTION)]))
    assert (color, cat) == (Color.RED, Category.MEDICATION_QUESTION)


def test_medication_question_yellow():
    f = Findings(
        questions=["before or after eating?"],
        question_topics=[QuestionTopic.MEDICATION],
    )
    color, cat = run(f)
    assert (color, cat) == (Color.YELLOW, Category.MEDICATION_QUESTION)


def test_pain_category():
    color, cat = run(Findings(symptoms=[sym(SymptomCategory.PAIN, Severity.MILD)]))
    assert (color, cat) == (Color.YELLOW, Category.PAIN)


def test_severe_pain_red_pain_category():
    color, cat = run(Findings(symptoms=[sym(SymptomCategory.PAIN, Severity.SEVERE)]))
    assert (color, cat) == (Color.RED, Category.PAIN)


def test_appointment_request():
    f = Findings(
        questions=["can I come in tomorrow?"],
        question_topics=[QuestionTopic.APPOINTMENT],
    )
    color, cat = run(f)
    assert (color, cat) == (Color.YELLOW, Category.APPOINTMENT_REQUEST)


def test_doing_well():
    color, cat = run(Findings(states_doing_well=True))
    assert (color, cat) == (Color.GREEN, Category.DOING_WELL)


def test_diet_question_is_mild_concern():
    f = Findings(questions=["coffee ok?"], question_topics=[QuestionTopic.DIET])
    color, cat = run(f)
    assert (color, cat) == (Color.YELLOW, Category.QUESTION_OR_MILD_CONCERN)


def test_numbness_red_falls_in_mild_concern_bucket_but_stays_red():
    # Category is topical; urgency carries the alarm. A red never hides.
    color, cat = run(Findings(symptoms=[sym(SymptomCategory.ALTERED_SENSATION)]))
    assert color is Color.RED
    assert cat is Category.QUESTION_OR_MILD_CONCERN


def test_vague_reply_is_other():
    color, cat = run(Findings(is_vague_or_minimal=True))
    assert (color, cat) == (Color.YELLOW, Category.OTHER)


def test_category_values_match_server_enum():
    from server.models import ReplyCategory

    assert {c.value for c in Category} == {c.value for c in ReplyCategory}
