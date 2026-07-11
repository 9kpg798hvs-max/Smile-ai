"""Unit tests for the deterministic rules layer.

Each test names the SPEC.md section it enforces. These run offline —
no API key needed.
"""

from triage.schema import (
    Color,
    ControlStatus,
    Findings,
    Severity,
    Symptom,
    SymptomCategory,
    Trajectory,
    VisitContext,
)
from triage.rules import triage


def sym(
    category=SymptomCategory.PAIN,
    severity=Severity.UNSTATED,
    trajectory=Trajectory.UNSTATED,
    control=ControlStatus.UNSTATED,
    quote="...",
):
    return Symptom(
        quote=quote, category=category, severity=severity, trajectory=trajectory, control=control
    )


# --- SPEC §3: red categories ------------------------------------------------

def test_swelling_is_red_regardless_of_severity():
    f = Findings(symptoms=[sym(SymptomCategory.SWELLING, Severity.MILD, Trajectory.IMPROVING)])
    assert triage(f).color is Color.RED


def test_numbness_is_red():
    f = Findings(symptoms=[sym(SymptomCategory.ALTERED_SENSATION)])
    assert triage(f).color is Color.RED


def test_lost_temporary_is_red():
    f = Findings(symptoms=[sym(SymptomCategory.TEMPORARY_RESTORATION_PROBLEM)])
    assert triage(f).color is Color.RED


def test_medication_reaction_is_red():
    f = Findings(symptoms=[sym(SymptomCategory.MEDICATION_REACTION)])
    assert triage(f).color is Color.RED


def test_fever_is_red():
    f = Findings(symptoms=[sym(SymptomCategory.FEVER_OR_MALAISE)])
    assert triage(f).color is Color.RED


def test_severe_pain_is_red():
    f = Findings(symptoms=[sym(SymptomCategory.PAIN, severity=Severity.SEVERE)])
    assert triage(f).color is Color.RED


def test_worsening_pain_is_red():
    f = Findings(symptoms=[sym(SymptomCategory.PAIN, trajectory=Trajectory.WORSENING)])
    assert triage(f).color is Color.RED


def test_uncontrolled_pain_is_red():
    f = Findings(symptoms=[sym(SymptomCategory.PAIN, control=ControlStatus.UNCONTROLLED)])
    assert triage(f).color is Color.RED


# --- Default decision: expected post-op pain --------------------------------

def test_mild_controlled_improving_pain_is_yellow_not_red_not_green():
    f = Findings(
        symptoms=[
            sym(
                SymptomCategory.PAIN,
                Severity.MILD,
                Trajectory.IMPROVING,
                ControlStatus.CONTROLLED,
            )
        ],
        states_doing_well=True,
    )
    assert triage(f).color is Color.YELLOW


def test_mild_cold_sensitivity_alone_is_yellow():
    f = Findings(symptoms=[sym(SymptomCategory.TEMPERATURE_SENSITIVITY, Severity.MILD)])
    assert triage(f).color is Color.YELLOW


def test_mild_stable_bleeding_is_yellow_but_worsening_is_red():
    mild = Findings(symptoms=[sym(SymptomCategory.BLEEDING, Severity.MILD, Trajectory.STABLE)])
    assert triage(mild).color is Color.YELLOW
    worse = Findings(symptoms=[sym(SymptomCategory.BLEEDING, trajectory=Trajectory.WORSENING)])
    assert triage(worse).color is Color.RED


# --- SPEC §2.2: sentiment cannot rescue a red --------------------------------

def test_warm_tone_with_swelling_is_red():
    # "Hope all is well! Everything is going very well" + swelling + chemo.
    # Tone isn't in the schema; the findings alone must produce red.
    f = Findings(
        symptoms=[
            sym(SymptomCategory.SWELLING, Severity.MILD),
            sym(SymptomCategory.TEMPERATURE_SENSITIVITY, Severity.MILD),
        ],
        states_doing_well=True,
        unprompted_praise=True,
        immunocompromise_mentioned=True,
    )
    assert triage(f).color is Color.RED


# --- SPEC §3: immunocompromise + any symptom ---------------------------------

def test_immunocompromise_plus_mild_symptom_is_red():
    f = Findings(
        symptoms=[sym(SymptomCategory.PAIN, Severity.MILD, Trajectory.IMPROVING)],
        immunocompromise_mentioned=True,
    )
    assert triage(f).color is Color.RED


def test_immunocompromise_from_chart_context_is_red():
    f = Findings(symptoms=[sym(SymptomCategory.PAIN, Severity.MILD)])
    ctx = VisitContext(known_immunocompromised=True)
    assert triage(f, ctx).color is Color.RED


def test_immunocompromise_without_symptom_is_not_red():
    f = Findings(states_doing_well=True, immunocompromise_mentioned=True)
    assert triage(f).color is Color.GREEN


# --- SPEC §2.3: terse is not well ---------------------------------------------

def test_vague_reply_is_yellow_always():
    f = Findings(is_vague_or_minimal=True)
    assert triage(f).color is Color.YELLOW


def test_vague_reply_is_yellow_even_if_positive_sounding():
    # "I'm doing ok" — extractor may set both flags; vague wins over green.
    f = Findings(is_vague_or_minimal=True, states_doing_well=True)
    assert triage(f).color is Color.YELLOW


# --- SPEC §3: questions are yellow --------------------------------------------

def test_question_is_yellow():
    f = Findings(questions=["Can I eat solid food yet?"], states_doing_well=True)
    assert triage(f).color is Color.YELLOW


# --- SPEC §2.5: proxy replies ---------------------------------------------------

def test_proxy_well_report_is_green_with_flag():
    f = Findings(states_doing_well=True, is_proxy_report=True, proxy_relationship="wife")
    r = triage(f)
    assert r.color is Color.GREEN
    assert r.is_proxy_report


def test_proxy_symptom_report_is_not_underweighted():
    f = Findings(
        symptoms=[sym(SymptomCategory.SWELLING, quote="her face looks more swollen")],
        is_proxy_report=True,
    )
    r = triage(f)
    assert r.color is Color.RED
    assert r.is_proxy_report


# --- SPEC §2.6: bias to yellow under uncertainty --------------------------------

def test_empty_findings_is_yellow_not_green():
    assert triage(Findings()).color is Color.YELLOW


def test_unintelligible_is_yellow():
    f = Findings(is_unintelligible_or_unrelated=True)
    assert triage(f).color is Color.YELLOW


def test_requests_contact_alone_is_yellow():
    f = Findings(requests_contact=True)
    assert triage(f).color is Color.YELLOW


def test_requests_contact_with_symptom_is_red():
    f = Findings(requests_contact=True, symptoms=[sym(SymptomCategory.PAIN, Severity.MILD)])
    assert triage(f).color is Color.RED


# --- SPEC §3: green + praise subtype --------------------------------------------

def test_clear_well_report_is_green():
    f = Findings(states_doing_well=True)
    r = triage(f)
    assert r.color is Color.GREEN
    assert not r.review_request_opportunity


def test_praise_green_sets_review_opportunity():
    f = Findings(states_doing_well=True, unprompted_praise=True)
    r = triage(f)
    assert r.color is Color.GREEN
    assert r.review_request_opportunity


def test_praise_on_non_green_never_sets_review_opportunity():
    f = Findings(
        symptoms=[sym(SymptomCategory.SWELLING)],
        states_doing_well=True,
        unprompted_praise=True,
    )
    r = triage(f)
    assert r.color is Color.RED
    assert not r.review_request_opportunity


# --- Rationale is populated ------------------------------------------------------

def test_rationale_cites_evidence_quote():
    f = Findings(symptoms=[sym(SymptomCategory.SWELLING, quote="my cheek puffed up")])
    r = triage(f)
    assert any("my cheek puffed up" in reason for reason in r.rationale)
    assert r.rationale
