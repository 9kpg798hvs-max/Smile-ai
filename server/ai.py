"""Reply AI pipeline: classify an inbound patient message and draft a response.

Hard rules enforced HERE, in code, regardless of which providers are plugged in:
- The AI never sends anything; it writes ReplyClassification and AIDraft rows.
- A red-urgency draft is ALWAYS the fixed escalation text (SPEC §2.4) — it
  never comes from an LLM, so it can never contain advice or diagnosis.
- Classification failure of any kind degrades to yellow + needs_manual_review
  (triage.classifier fail-safe), never to green, never to a crash.
"""

import logging

from sqlalchemy.orm import Session

from triage.classifier import classify
from triage.rules import categorize
from triage.schema import Category, Color, TriageResult

from .models import (
    AIDraft,
    DoctorProfile,
    Message,
    Office,
    Patient,
    ReplyCategory,
    ReplyClassification,
    Urgency,
    User,
)

logger = logging.getLogger(__name__)

ESCALATION_DRAFT = (
    "Thank you for telling me. I want to see that — please call the office "
    "now at {office_phone}."
)


class MockDraftProvider:
    """MOCK/deterministic drafting for yellow and green replies.

    The production LLM drafter (doctor-voice) plugs in behind the same
    interface; reds never reach any draft provider (see draft_reply).
    """

    name = "mock"

    def draft(self, *, reply_text: str, result: TriageResult, category: Category,
              patient_first_name: str, doctor_display_name: str, office_phone: str) -> str:
        if result.color is Color.GREEN:
            return (
                f"Glad to hear it, {patient_first_name}. Keep doing what you're "
                "doing, and reach out if anything changes."
            )
        if category is Category.APPOINTMENT_REQUEST:
            return (
                f"Of course — please call the office at {office_phone} and "
                "we'll get you scheduled."
            )
        return (
            f"Thanks for reaching out, {patient_first_name} — good question. "
            f"{doctor_display_name} will get back to you shortly; if anything "
            f"gets worse in the meantime, call us at {office_phone}."
        )


def classify_and_draft(
    db: Session,
    *,
    message: Message,
    patient: Patient,
    doctor: User,
    office: Office | None,
    reply_extractor,
    draft_provider,
) -> ReplyClassification:
    try:
        if reply_extractor is None:
            # Lazily construct the real extractor (needs ANTHROPIC_API_KEY).
            from triage.extractor import Extractor

            reply_extractor = Extractor()
        result: TriageResult = classify(message.body, extractor=reply_extractor)
    except Exception:
        logger.exception("reply AI unavailable; failing safe to yellow")
        result = TriageResult(
            color=Color.YELLOW,
            rationale=["AI unavailable — escalated for manual review"],
            needs_manual_review=True,
        )
    category = (
        categorize(result.findings, result.color)
        if result.findings is not None
        else Category.OTHER  # extraction failed; fail-safe yellow from classify()
    )

    classification = ReplyClassification(
        message_id=message.id,
        category=ReplyCategory(category.value),
        urgency=Urgency(result.color.value),
        confidence=None if result.needs_manual_review else 1.0,
        findings_json=result.findings.model_dump() if result.findings else {},
        model=result.model,
        prompt_version=result.prompt_version,
    )
    db.add(classification)

    office_phone = (office.phone_display if office else None) or "the office"
    profile = db.get(DoctorProfile, doctor.id)
    doctor_name = profile.display_name if profile else doctor.full_name

    if result.color is Color.RED:
        # Escalation only — never generated, never advisory (SPEC §2.4).
        body = ESCALATION_DRAFT.format(office_phone=office_phone)
    else:
        try:
            body = draft_provider.draft(
                reply_text=message.body,
                result=result,
                category=category,
                patient_first_name=patient.first_name,
                doctor_display_name=doctor_name,
                office_phone=office_phone,
            )
        except Exception:
            logger.exception("draft provider failed; falling back to neutral draft")
            body = (
                f"Thanks for your message, {patient.first_name} — "
                f"{doctor_name} will get back to you shortly."
            )

    db.add(AIDraft(message_id=message.id, body=body))
    return classification
