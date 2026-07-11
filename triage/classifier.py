"""Top-level classify(): extract findings with the LLM, assign color with
deterministic rules, fail safe on any error.

Failure policy (SPEC §2.6): if extraction fails for any reason — API error,
parse failure, refusal — the reply is YELLOW with needs_manual_review=True.
It can never silently become green, and it never crashes the inbox.
"""

import logging

from .rules import triage
from .schema import Color, Findings, TriageResult, VisitContext

logger = logging.getLogger(__name__)


def classify(
    reply_text: str,
    context: VisitContext | None = None,
    extractor=None,
) -> TriageResult:
    if extractor is None:
        from .extractor import Extractor

        extractor = Extractor()

    try:
        findings = extractor.extract(reply_text, context)
    except Exception:
        logger.exception("extraction failed; failing safe to yellow")
        return TriageResult(
            color=Color.YELLOW,
            rationale=[
                "automatic extraction failed — escalated to yellow for manual review (SPEC §2.6)"
            ],
            needs_manual_review=True,
            findings=None,
            model=getattr(extractor, "model", None),
            prompt_version=getattr(extractor, "prompt_version", None),
        )

    result = triage(findings, context)
    result.model = getattr(extractor, "model", None)
    result.prompt_version = getattr(extractor, "prompt_version", None)
    return result
