"""LLM extraction: patient reply text -> Findings (structured output).

The model never assigns a color — see rules.py. Uses the Messages API's
structured outputs (client.messages.parse) so the response is guaranteed to
validate against the Findings schema.
"""

import os

import anthropic

from .prompts import PROMPT_VERSION, SYSTEM_PROMPT, prompt_hash
from .schema import Findings, VisitContext

DEFAULT_MODEL = os.environ.get("TRIAGE_MODEL", "claude-opus-4-8")


def build_user_message(reply_text: str, context: VisitContext | None = None) -> str:
    parts = []
    if context is not None:
        ctx_lines = []
        if context.procedure:
            ctx_lines.append(f"procedure: {context.procedure}")
        if context.days_post_op is not None:
            ctx_lines.append(f"days post-op: {context.days_post_op}")
        if context.appointment_number is not None:
            ctx_lines.append(f"appointment number: {context.appointment_number}")
        if ctx_lines:
            parts.append("Visit context:\n" + "\n".join(ctx_lines))
    parts.append(f"Patient reply:\n<reply>\n{reply_text}\n</reply>")
    return "\n\n".join(parts)


class Extractor:
    """Callable protocol: extract(reply_text, context) -> Findings.

    The eval harness and classifier accept anything with this method, so
    tests substitute a deterministic mock (see tests/mock_extractor.py).
    """

    def __init__(self, client: anthropic.Anthropic | None = None, model: str = DEFAULT_MODEL):
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.prompt_version = PROMPT_VERSION
        self.prompt_hash = prompt_hash()

    def extract(self, reply_text: str, context: VisitContext | None = None) -> Findings:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # Stable prefix: cache across the whole eval run / inbox batch.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": build_user_message(reply_text, context)}],
            output_format=Findings,
        )
        findings = response.parsed_output
        if findings is None:
            raise ValueError(
                f"structured output parse failed (stop_reason={response.stop_reason})"
            )
        return findings
