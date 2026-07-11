"""Data model for the triage classifier.

Design constraint (SPEC.md §2.2): the extraction schema contains NO sentiment,
politeness, or positivity fields. The LLM extracts clinical findings only;
tone physically cannot reach the color decision because there is nowhere in
this schema to put it.
"""

from enum import Enum

from pydantic import BaseModel, Field


class Color(str, Enum):
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"


class SymptomCategory(str, Enum):
    PAIN = "pain"
    SWELLING = "swelling"
    # Numbness, tingling, altered sensation (SPEC §3: red)
    ALTERED_SENSATION = "altered_sensation"
    TEMPERATURE_SENSITIVITY = "temperature_sensitivity"
    BLEEDING = "bleeding"
    FEVER_OR_MALAISE = "fever_or_malaise"
    MEDICATION_REACTION = "medication_reaction"
    # Loose or lost temporary filling/crown (SPEC §3: red)
    TEMPORARY_RESTORATION_PROBLEM = "temporary_restoration_problem"
    OTHER = "other"


class Severity(str, Enum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    UNSTATED = "unstated"


class Trajectory(str, Enum):
    IMPROVING = "improving"
    STABLE = "stable"
    WORSENING = "worsening"
    UNSTATED = "unstated"


class ControlStatus(str, Enum):
    """Whether the patient reports the symptom is managed (e.g. by the
    prescribed meds) or explicitly not managed ("the pills aren't touching it")."""

    CONTROLLED = "controlled"
    UNCONTROLLED = "uncontrolled"
    UNSTATED = "unstated"


class Symptom(BaseModel):
    quote: str = Field(description="Verbatim span from the reply that reports this symptom")
    category: SymptomCategory
    severity: Severity
    trajectory: Trajectory
    control: ControlStatus


class Findings(BaseModel):
    """Clinical content extracted from one patient reply. No sentiment."""

    symptoms: list[Symptom] = Field(default_factory=list)
    questions: list[str] = Field(
        default_factory=list,
        description="Questions the patient asks, verbatim or near-verbatim",
    )
    is_vague_or_minimal: bool = Field(
        default=False,
        description='Reply is a non-specific non-answer: "ok", "fine", "I\'m doing ok", a bare emoji',
    )
    states_doing_well: bool = Field(
        default=False,
        description='Reply affirmatively states the patient is doing well ("no pain", "feeling great", "doing good")',
    )
    is_proxy_report: bool = Field(
        default=False,
        description="Someone other than the patient is replying (spouse, parent, adult child)",
    )
    proxy_relationship: str | None = Field(
        default=None, description='Relationship of the replier if stated, e.g. "wife"'
    )
    immunocompromise_mentioned: bool = Field(
        default=False,
        description="Reply mentions chemo, transplant, immunosuppressants, or another immunocompromising condition",
    )
    requests_contact: bool = Field(
        default=False, description="Patient asks to be called or seen"
    )
    unprompted_praise: bool = Field(
        default=False,
        description="Effusive unprompted praise for the doctor/practice (SPEC §3 green subtype)",
    )
    is_unintelligible_or_unrelated: bool = Field(
        default=False,
        description='Reply cannot be interpreted as a health status: wrong number, "who is this?", empty',
    )


class VisitContext(BaseModel):
    """Optional structured context about the visit. All fields optional —
    the classifier must work from reply text alone (SPEC eval requirement)."""

    procedure: str | None = None
    days_post_op: int | None = None
    appointment_number: int | None = None  # 1 or 2 (SPEC §5.3)
    prior_red_flag: bool = False  # red on appointment 1 surfaces at appointment 2
    known_immunocompromised: bool = False  # from chart, not from the message


class TriageResult(BaseModel):
    color: Color
    rationale: list[str] = Field(
        description="Human-readable reasons citing the findings that drove the color"
    )
    is_proxy_report: bool = False
    review_request_opportunity: bool = False  # green + unprompted praise (SPEC §3)
    needs_manual_review: bool = False  # set when extraction failed and we failed safe
    findings: Findings | None = None
    model: str | None = None
    prompt_version: str | None = None
