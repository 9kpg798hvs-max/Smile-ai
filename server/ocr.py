"""OCR provider interface for day-sheet extraction.

Two implementations:
- MockOCRProvider — deterministic canned output for dev/tests. Clearly
  labeled MOCK; the UI shows the ocr_model value.
- ClaudeOCRProvider — Claude vision + structured outputs. Requires
  ANTHROPIC_API_KEY and, for real PHI, a BAA (docs/05). NOT yet exercised
  against real day sheets — treat as unverified until the Phase-2 OCR eval
  set exists (docs/06 risk #3).
"""

import datetime
import os

from pydantic import BaseModel, Field


class OCREntry(BaseModel):
    patient_name: str = ""
    time: str = ""
    doctor_name: str = ""
    procedure: str = ""
    phone: str = ""
    crossed_out: bool = Field(
        default=False,
        description="Row is struck through / crossed out on the sheet",
    )
    # 0..1 per field; the UI flags anything below threshold, phone strictest
    confidence: dict[str, float] = Field(default_factory=dict)


class OCRResult(BaseModel):
    schedule_date: str | None = Field(
        default=None, description="ISO date printed on the sheet, if visible"
    )
    entries: list[OCREntry] = Field(default_factory=list)


class MockOCRProvider:
    """MOCK — returns canned entries; never reads the file contents."""

    model = "MOCK"
    prompt_version = "mock"

    def __init__(self, canned: OCRResult | None = None):
        self.canned = canned or OCRResult(
            schedule_date=datetime.date.today().isoformat(),
            entries=[
                OCREntry(
                    patient_name="Pat Mockley",
                    time="8:00 AM",
                    doctor_name="Dr. Mock",
                    procedure="Root canal #19",
                    phone="+15550200100",
                    confidence={"patient_name": 0.98, "phone": 0.97, "time": 0.99},
                ),
                OCREntry(
                    patient_name="Casey Mockford",
                    time="9:30 AM",
                    doctor_name="Dr. Mock",
                    procedure="Crown prep",
                    phone="+15550200101",
                    # low phone confidence: UI must flag for visual verify
                    confidence={"patient_name": 0.95, "phone": 0.61, "time": 0.98},
                ),
                OCREntry(
                    patient_name="Jamie Mocksmith",
                    time="11:00 AM",
                    doctor_name="Dr. Mock",
                    procedure="Crown prep",
                    phone="+15550200102",
                    crossed_out=True,  # cancelled on the sheet
                    confidence={"patient_name": 0.93, "phone": 0.9, "time": 0.97},
                ),
            ],
        )

    def extract(self, content: bytes, mime: str) -> OCRResult:
        return self.canned


_OCR_SYSTEM_PROMPT = """\
You extract rows from a dental office day sheet (photo, screenshot, or PDF
page). Return every patient row you can see.

- Copy fields exactly as printed; do not normalize or guess missing digits.
- crossed_out: true when a row is struck through, X-ed, or otherwise marked
  cancelled. When unsure, false — staff review handles ambiguity.
- confidence: your 0..1 confidence per field. Be conservative on phone
  numbers: a single misread digit texts a stranger. If any digit is unclear,
  confidence must be below 0.7.
- schedule_date: the date printed on the sheet, ISO format, or null.
- Never invent rows or fields you cannot see.
"""

OCR_PROMPT_VERSION = "1"


class ClaudeOCRProvider:
    """Claude vision extraction. UNVERIFIED against real day sheets so far."""

    prompt_version = OCR_PROMPT_VERSION

    def __init__(self, client=None, model: str | None = None):
        import anthropic

        self.client = client or anthropic.Anthropic()
        self.model = model or os.environ.get("OCR_MODEL", "claude-opus-4-8")

    def extract(self, content: bytes, mime: str) -> OCRResult:
        import base64

        data = base64.standard_b64encode(content).decode()
        if mime == "application/pdf":
            block = {
                "type": "document",
                "source": {"type": "base64", "media_type": mime, "data": data},
            }
        else:
            block = {
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": data},
            }
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=_OCR_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        block,
                        {"type": "text", "text": "Extract all rows from this day sheet."},
                    ],
                }
            ],
            output_format=OCRResult,
        )
        if response.parsed_output is None:
            raise ValueError(f"OCR parse failed (stop_reason={response.stop_reason})")
        return response.parsed_output
