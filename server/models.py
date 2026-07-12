"""SQLAlchemy models — the full schema from docs/02-database-schema.md.

Multi-tenant: every tenant-owned table carries practice_id. The duplicate
follow-up prevention (workflow requirement #7) is the UNIQUE constraint on
visits(patient_id, doctor_id, visit_date, procedure) plus the UNIQUE
follow_ups.visit_id.
"""

import datetime
import enum
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# --- enums -------------------------------------------------------------------

class Role(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    PRACTICE_ADMIN = "practice_admin"
    OFFICE_MANAGER = "office_manager"
    DOCTOR = "doctor"
    STAFF = "staff"


class UploadStatus(str, enum.Enum):
    PROCESSING = "processing"
    EXTRACTED = "extracted"
    IN_REVIEW = "in_review"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class ExclusionSource(str, enum.Enum):
    OCR = "ocr"
    STAFF = "staff"
    DOCTOR = "doctor"
    SYSTEM = "system"  # e.g. duplicate, opted out


class FollowUpStatus(str, enum.Enum):
    PENDING = "pending"
    READY_TO_SEND = "ready_to_send"
    SENT = "sent"
    DELIVERED = "delivered"
    AWAITING_REPLY = "awaiting_reply"
    REPLIED = "replied"
    NEEDS_ATTENTION = "needs_attention"
    FAILED = "failed"
    EXCLUDED = "excluded"


class MessageDirection(str, enum.Enum):
    OUT = "out"
    IN = "in"


class MessageStatus(str, enum.Enum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    RECEIVED = "received"  # inbound


class ConversationStatus(str, enum.Enum):
    OPEN = "open"
    ARCHIVED = "archived"


class ReplyCategory(str, enum.Enum):
    DOING_WELL = "doing_well"
    PAIN = "pain"
    SWELLING = "swelling"
    MEDICATION_QUESTION = "medication_question"
    EMERGENCY = "emergency"
    APPOINTMENT_REQUEST = "appointment_request"
    QUESTION_OR_MILD_CONCERN = "question_or_mild_concern"
    OTHER = "other"


class Urgency(str, enum.Enum):
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"


class DraftStatus(str, enum.Enum):
    SUGGESTED = "suggested"
    APPROVED = "approved"
    DISCARDED = "discarded"


class Tone(str, enum.Enum):
    FORMAL = "formal"
    FRIENDLY = "friendly"
    CUSTOM = "custom"


# --- tenancy & people ----------------------------------------------------------

class Practice(Base):
    __tablename__ = "practices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    # e.g. {"staff_can_send_replies": false}
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Office(Base):
    __tablename__ = "offices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    practice_id: Mapped[str] = mapped_column(ForeignKey("practices.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(400))
    phone_display: Mapped[str | None] = mapped_column(String(40))
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # NULL practice_id = super admin (platform-level)
    practice_id: Mapped[str | None] = mapped_column(ForeignKey("practices.id"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(400))
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(Enum(Role))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    doctor_profile: Mapped["DoctorProfile | None"] = relationship(back_populates="user")


class AuthSession(Base):
    __tablename__ = "sessions"
    # sha256 of the bearer token; the raw token exists only in the cookie
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))


class UserOfficeAccess(Base):
    __tablename__ = "user_office_access"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    office_id: Mapped[str] = mapped_column(ForeignKey("offices.id"), primary_key=True)


class DoctorProfile(Base):
    __tablename__ = "doctor_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))  # "Dr. Belani"
    default_send_time: Mapped[datetime.time] = mapped_column(Time, default=datetime.time(18, 0))
    tone: Mapped[Tone] = mapped_column(Enum(Tone), default=Tone.FRIENDLY)
    signature: Mapped[str | None] = mapped_column(String(200))
    wider_access: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="doctor_profile")


class PhoneNumber(Base):
    __tablename__ = "phone_numbers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    practice_id: Mapped[str] = mapped_column(ForeignKey("practices.id"), index=True)
    office_id: Mapped[str | None] = mapped_column(ForeignKey("offices.id"))
    doctor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    e164: Mapped[str] = mapped_column(String(20), unique=True)
    provider: Mapped[str] = mapped_column(String(20), default="mock")  # mock | twilio
    kind: Mapped[str] = mapped_column(String(20), default="dedicated")  # hosted | dedicated
    status: Mapped[str] = mapped_column(String(20), default="active")


# --- patients & visits -----------------------------------------------------------

class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    practice_id: Mapped[str] = mapped_column(ForeignKey("practices.id"), index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    mobile_e164: Mapped[str] = mapped_column(String(20), index=True)
    language: Mapped[str] = mapped_column(String(10), default="en")
    sms_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Visit(Base):
    __tablename__ = "visits"
    # Duplicate follow-up prevention (workflow requirement #7): one visit per
    # (patient, doctor, date, procedure); follow_ups.visit_id is UNIQUE below.
    __table_args__ = (
        UniqueConstraint("patient_id", "doctor_id", "visit_date", "procedure",
                         name="uq_visit_dedup"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    practice_id: Mapped[str] = mapped_column(ForeignKey("practices.id"), index=True)
    office_id: Mapped[str] = mapped_column(ForeignKey("offices.id"))
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    doctor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    visit_date: Mapped[datetime.date] = mapped_column(Date)
    procedure: Mapped[str] = mapped_column(String(200), default="")
    appointment_number: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20), default="ocr")  # ocr | manual | import
    schedule_entry_id: Mapped[str | None] = mapped_column(String(36))


# --- schedule intake ---------------------------------------------------------------

class ScheduleUpload(Base):
    __tablename__ = "schedule_uploads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    practice_id: Mapped[str] = mapped_column(ForeignKey("practices.id"), index=True)
    office_id: Mapped[str] = mapped_column(ForeignKey("offices.id"))
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    file_path: Mapped[str] = mapped_column(String(500))
    mime: Mapped[str] = mapped_column(String(100))
    schedule_date: Mapped[datetime.date | None] = mapped_column(Date)
    status: Mapped[UploadStatus] = mapped_column(Enum(UploadStatus), default=UploadStatus.PROCESSING)
    ocr_model: Mapped[str | None] = mapped_column(String(100))
    ocr_prompt_version: Mapped[str | None] = mapped_column(String(20))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    entries: Mapped[list["ScheduleEntry"]] = relationship(back_populates="upload")


class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    upload_id: Mapped[str] = mapped_column(ForeignKey("schedule_uploads.id"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    patient_name_raw: Mapped[str] = mapped_column(String(200), default="")
    time_raw: Mapped[str] = mapped_column(String(40), default="")
    doctor_name_raw: Mapped[str] = mapped_column(String(200), default="")
    procedure_raw: Mapped[str] = mapped_column(String(200), default="")
    phone_raw: Mapped[str] = mapped_column(String(40), default="")
    ocr_crossed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_confidence_json: Mapped[dict] = mapped_column(JSON, default=dict)  # per-field 0..1
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusion_source: Mapped[ExclusionSource | None] = mapped_column(Enum(ExclusionSource))
    exclusion_reason: Mapped[str | None] = mapped_column(String(300))
    matched_patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"))
    resolved_doctor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    edited_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    edited_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    upload: Mapped[ScheduleUpload] = relationship(back_populates="entries")


# --- follow-ups & messaging ----------------------------------------------------------

class FollowUp(Base):
    __tablename__ = "follow_ups"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    practice_id: Mapped[str] = mapped_column(ForeignKey("practices.id"), index=True)
    visit_id: Mapped[str] = mapped_column(ForeignKey("visits.id"), unique=True)
    status: Mapped[FollowUpStatus] = mapped_column(
        Enum(FollowUpStatus), default=FollowUpStatus.PENDING, index=True
    )
    template_id: Mapped[str | None] = mapped_column(ForeignKey("templates.id"))
    rendered_body: Mapped[str | None] = mapped_column(Text)
    scheduled_send_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(300))


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    practice_id: Mapped[str] = mapped_column(ForeignKey("practices.id"), index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    doctor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    office_id: Mapped[str] = mapped_column(ForeignKey("offices.id"))
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus), default=ConversationStatus.OPEN
    )
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    last_message_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    unread_count: Mapped[int] = mapped_column(Integer, default=0)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    practice_id: Mapped[str] = mapped_column(ForeignKey("practices.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    follow_up_id: Mapped[str | None] = mapped_column(ForeignKey("follow_ups.id"))
    direction: Mapped[MessageDirection] = mapped_column(Enum(MessageDirection))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[MessageStatus] = mapped_column(Enum(MessageStatus))
    provider: Mapped[str] = mapped_column(String(20), default="mock")
    provider_message_id: Mapped[str | None] = mapped_column(String(100))
    from_e164: Mapped[str] = mapped_column(String(20))
    to_e164: Mapped[str] = mapped_column(String(20))
    sent_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    failed_reason: Mapped[str | None] = mapped_column(String(300))


class DeliveryEvent(Base):
    __tablename__ = "delivery_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    event: Mapped[str] = mapped_column(String(30))
    provider_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    practice_id: Mapped[str] = mapped_column(ForeignKey("practices.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"))
    file_path: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(100))
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InternalNote(Base):
    __tablename__ = "internal_notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- AI -------------------------------------------------------------------------------

class ReplyClassification(Base):
    __tablename__ = "reply_classifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), unique=True)
    category: Mapped[ReplyCategory] = mapped_column(Enum(ReplyCategory))
    urgency: Mapped[Urgency] = mapped_column(Enum(Urgency))
    confidence: Mapped[float | None] = mapped_column(Float)
    findings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    override_category: Mapped[ReplyCategory | None] = mapped_column(Enum(ReplyCategory))
    override_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    override_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class AIDraft(Base):
    __tablename__ = "ai_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    edited_body: Mapped[str | None] = mapped_column(Text)  # doctor-voice training signal
    status: Mapped[DraftStatus] = mapped_column(Enum(DraftStatus), default=DraftStatus.SUGGESTED)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    sent_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"))


# --- templates, notifications, audit ---------------------------------------------------

class Template(Base):
    __tablename__ = "templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    practice_id: Mapped[str] = mapped_column(ForeignKey("practices.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    tone: Mapped[Tone] = mapped_column(Enum(Tone), default=Tone.FRIENDLY)
    doctor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    office_id: Mapped[str | None] = mapped_column(ForeignKey("offices.id"))
    procedure: Mapped[str | None] = mapped_column(String(200))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    read_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    practice_id: Mapped[str | None] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(36))
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
