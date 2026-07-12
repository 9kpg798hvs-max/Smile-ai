"""Deterministic mapping from extracted Findings to a triage color.

This layer — not the LLM — decides the color. It exists so the SPEC.md
non-negotiables are enforceable, unit-testable code:

  §2.2  Triage on clinical content, never sentiment (sentiment isn't even
        representable in Findings).
  §2.3  Terse/vague replies are yellow, always.
  §2.5  Proxy reports are triaged identically; proxy is a flag, not a discount.
  §2.6  Bias to yellow under uncertainty: green is only reachable when the
        patient affirmatively reports doing well with nothing else going on.
        Anything unclassifiable is yellow, never green.
  §3    Red categories: swelling, altered sensation, worsening symptoms,
        loose/lost temporary, medication reaction, immunocompromise + symptom.

Decision on expected post-op pain (default pending doctor sign-off, see
README "Decisions taken"): reported pain that is not severe/worsening/
uncontrolled is YELLOW, not red (else every honest day-1 reply is red) and
not green (a symptom is never a green).
"""

from .schema import (
    Category,
    Color,
    ControlStatus,
    Findings,
    QuestionTopic,
    Severity,
    Symptom,
    SymptomCategory,
    Trajectory,
    TriageResult,
    VisitContext,
)

# Symptom categories that are red on sight, at any severity (SPEC §3).
ALWAYS_RED_CATEGORIES = frozenset(
    {
        SymptomCategory.SWELLING,
        SymptomCategory.ALTERED_SENSATION,
        SymptomCategory.FEVER_OR_MALAISE,
        SymptomCategory.MEDICATION_REACTION,
        SymptomCategory.TEMPORARY_RESTORATION_PROBLEM,
    }
)


def _symptom_red_reason(s: Symptom) -> str | None:
    if s.category in ALWAYS_RED_CATEGORIES:
        return f"{s.category.value} reported: \"{s.quote}\""
    if s.severity is Severity.SEVERE:
        return f"severe {s.category.value}: \"{s.quote}\""
    if s.trajectory is Trajectory.WORSENING:
        return f"worsening {s.category.value}: \"{s.quote}\""
    if s.control is ControlStatus.UNCONTROLLED:
        return f"{s.category.value} not controlled by meds: \"{s.quote}\""
    return None


def triage(findings: Findings, context: VisitContext | None = None) -> TriageResult:
    immunocompromised = findings.immunocompromise_mentioned or bool(
        context and context.known_immunocompromised
    )

    red_reasons: list[str] = []
    yellow_reasons: list[str] = []

    for signal in findings.emergency_signals:
        red_reasons.append(f'emergency language: "{signal}"')

    for s in findings.symptoms:
        reason = _symptom_red_reason(s)
        if reason is not None:
            red_reasons.append(reason)
        elif immunocompromised:
            # SPEC §3: any immunocompromise + any symptom is red.
            red_reasons.append(
                f"symptom ({s.category.value}) in an immunocompromised patient: \"{s.quote}\""
            )
        else:
            yellow_reasons.append(f"reported {s.category.value}: \"{s.quote}\"")

    if findings.requests_contact:
        if findings.symptoms:
            red_reasons.append("patient requests contact and reports symptoms")
        else:
            yellow_reasons.append("patient requests contact")

    for q in findings.questions:
        yellow_reasons.append(f'question asked: "{q}"')

    if findings.is_vague_or_minimal:
        yellow_reasons.append("vague/terse reply — an unanswered question, not a green light (SPEC §2.3)")

    if findings.is_unintelligible_or_unrelated:
        yellow_reasons.append("reply is unintelligible or unrelated — needs a human look")

    if red_reasons:
        color = Color.RED
        rationale = red_reasons
    elif yellow_reasons:
        color = Color.YELLOW
        rationale = yellow_reasons
    elif findings.states_doing_well:
        color = Color.GREEN
        rationale = ["patient affirmatively reports doing well; no symptoms, no questions"]
    else:
        # Nothing clinical, no affirmative "doing well" — e.g. "thanks".
        # Bias to yellow under uncertainty (SPEC §2.6).
        color = Color.YELLOW
        rationale = ["no affirmative status in reply — escalating under uncertainty (SPEC §2.6)"]

    return TriageResult(
        color=color,
        rationale=rationale,
        is_proxy_report=findings.is_proxy_report,
        review_request_opportunity=(color is Color.GREEN and findings.unprompted_praise),
        findings=findings,
    )


def categorize(findings: Findings, color: Color) -> Category:
    """Map findings to the SmileFlow reply category (topical, deterministic).

    The category describes WHAT the reply is about; `color` carries HOW
    urgent it is. Priority order mirrors clinical salience: emergency
    language first, then swelling, medication, pain.
    """
    symptom_categories = {s.category for s in findings.symptoms}

    if findings.emergency_signals:
        return Category.EMERGENCY
    if SymptomCategory.SWELLING in symptom_categories:
        return Category.SWELLING
    if (
        SymptomCategory.MEDICATION_REACTION in symptom_categories
        or QuestionTopic.MEDICATION in findings.question_topics
    ):
        return Category.MEDICATION_QUESTION
    if SymptomCategory.PAIN in symptom_categories:
        return Category.PAIN
    if QuestionTopic.APPOINTMENT in findings.question_topics or (
        findings.requests_contact and color is not Color.RED
    ):
        return Category.APPOINTMENT_REQUEST
    if color is Color.GREEN and findings.states_doing_well:
        return Category.DOING_WELL
    if findings.questions or findings.symptoms or findings.requests_contact:
        # Remaining symptomatic reds (numbness, lost temporary, fever…) and
        # all other questions/mild concerns land here; urgency still governs
        # alerting, so nothing urgent is hidden by this bucket.
        return Category.QUESTION_OR_MILD_CONCERN
    return Category.OTHER
