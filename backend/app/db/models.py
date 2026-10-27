"""SQLAlchemy models for every MemberAssist table.

Three conventions hold throughout:

* **Money is ``Numeric(10, 2)``**, never a float. Cost answers are arithmetic a
  member may check against a statement, so cents must not drift.
* **Every table holding synthetic records carries ``is_synthetic``.** Plans,
  benefits, prices and providers come from real public sources and do not.
* **Text columns that can contain conversation content are named
  ``*_masked``.** Nothing unmasked is ever written to the database.

The schema works unchanged on PostgreSQL 16 and on the temporary SQLite
development fallback, so only portable types are used (``JSON`` rather than
``JSONB``, no server-side ``gen_random_uuid``).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Roles and enumerated values are kept as constrained strings rather than
# database enums: SQLite has no ENUM type, and the constraint is what matters.
ROLES = ("member", "provider", "agent", "admin")
NETWORK_TIERS = ("in_network", "out_of_network")
CLAIM_STATUSES = ("PAID", "PENDING", "DENIED")
DENIAL_CODES = ("PA_MISSING", "OUT_OF_NETWORK", "NOT_COVERED", "DUPLICATE")
PA_STATUSES = ("PENDING", "APPROVED", "DENIED", "NEEDS_INFO")
MESSAGE_ROLES = ("user", "assistant", "system")
MESSAGE_PATHS = ("router_first", "function_calling", "safety_route", "blocked")
HANDOFF_STATUSES = ("open", "resolved")
PLACES_OF_SERVICE = ("office", "facility")


class Base(DeclarativeBase):
    """Declarative base with a portable JSON type mapping."""

    type_annotation_map: ClassVar[dict[type, object]] = {dict: JSON, list: JSON}


def _utcnow() -> dt.datetime:
    """Timezone-aware current time (stored as UTC everywhere)."""
    return dt.datetime.now(dt.UTC)


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------
class User(Base):
    """A login. Who someone is comes from here and the JWT, never from chat."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(f"role IN {ROLES}", name="ck_users_role"),
        # A member login must point at a member record; a provider at an NPI.
        CheckConstraint(
            "(role != 'member') OR (member_id IS NOT NULL)",
            name="ck_users_member_has_member_id",
        ),
        CheckConstraint(
            "(role != 'provider') OR (provider_npi IS NOT NULL)",
            name="ck_users_provider_has_npi",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), index=True)
    member_id: Mapped[str | None] = mapped_column(
        String(16), ForeignKey("members.member_id", ondelete="RESTRICT"), index=True
    )
    provider_npi: Mapped[str | None] = mapped_column(
        String(10), ForeignKey("providers.npi", ondelete="RESTRICT"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(120))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    member: Mapped[Member | None] = relationship(back_populates="user", lazy="selectin")


# ---------------------------------------------------------------------------
# plans (real data, from the CMS Marketplace API or the Marketplace PUFs)
# ---------------------------------------------------------------------------
class Plan(Base):
    """A real Marketplace plan design."""

    __tablename__ = "plans"

    plan_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    hios_id: Mapped[str] = mapped_column(String(32), index=True)
    issuer: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300))
    metal_level: Mapped[str] = mapped_column(String(32))
    plan_type: Mapped[str] = mapped_column(String(16))
    year: Mapped[int] = mapped_column(Integer, index=True)
    state: Mapped[str] = mapped_column(String(2))
    county_fips: Mapped[str | None] = mapped_column(String(5))
    deductible_ind: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    oop_max_ind: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    hsa_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(Text)
    retrieved_on: Mapped[dt.date] = mapped_column(Date)

    benefits: Mapped[list[PlanBenefit]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class PlanBenefit(Base):
    """Cost sharing for one benefit category on one plan, per network tier."""

    __tablename__ = "plan_benefits"
    __table_args__ = (
        UniqueConstraint("plan_id", "benefit_name", "network_tier", name="uq_plan_benefit_tier"),
        CheckConstraint(f"network_tier IN {NETWORK_TIERS}", name="ck_plan_benefits_tier"),
        CheckConstraint(
            "coinsurance IS NULL OR (coinsurance >= 0 AND coinsurance <= 1)",
            name="ck_plan_benefits_coinsurance_fraction",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("plans.plan_id", ondelete="CASCADE"), index=True
    )
    benefit_name: Mapped[str] = mapped_column(String(200), index=True)
    network_tier: Mapped[str] = mapped_column(String(16))
    copay: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    #: Stored as a fraction (0.20), not a percentage (20).
    coinsurance: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    after_deductible: Mapped[bool] = mapped_column(Boolean, default=False)
    is_covered: Mapped[bool] = mapped_column(Boolean, default=True)
    pa_required: Mapped[bool] = mapped_column(Boolean, default=False)
    #: The exact wording from the plan's own cost-sharing text, used in answers.
    display_text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64))

    plan: Mapped[Plan] = relationship(back_populates="benefits")


# ---------------------------------------------------------------------------
# members (synthetic)
# ---------------------------------------------------------------------------
class Member(Base):
    """A synthetic member, re-homed to the project's chosen county."""

    __tablename__ = "members"

    member_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(80))
    last_name: Mapped[str] = mapped_column(String(80))
    dob: Mapped[dt.date] = mapped_column(Date)
    gender: Mapped[str] = mapped_column(String(16))
    phone: Mapped[str] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(160))
    address: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(2))
    zip_code: Mapped[str] = mapped_column(String(10))
    plan_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("plans.plan_id", ondelete="RESTRICT"), index=True
    )
    synthea_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User | None] = relationship(back_populates="member")
    claims: Mapped[list[Claim]] = relationship(back_populates="member")

    @property
    def full_name(self) -> str:
        """Display name. Only ever used behind the gateway, never sent to an LLM."""
        return f"{self.first_name} {self.last_name}"


class Accumulator(Base):
    """How much of the deductible and out-of-pocket maximum a member has used."""

    __tablename__ = "accumulators"
    __table_args__ = (
        UniqueConstraint("member_id", "plan_year", name="uq_accumulator_member_year"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("members.member_id", ondelete="CASCADE"), index=True
    )
    plan_year: Mapped[int] = mapped_column(Integer)
    deductible_used: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    oop_used: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# providers (real NPIs; the network flag is ours and documented)
# ---------------------------------------------------------------------------
class Provider(Base):
    """A real provider from the NPI Registry."""

    __tablename__ = "providers"

    npi: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    #: The registry's own taxonomy description, which is often a sub-specialty
    #: ("Orthopaedic Surgery, Sports Medicine").
    specialty: Mapped[str] = mapped_column(String(160), index=True)
    #: The broad taxonomy we searched for ("Orthopaedic Surgery"). Member
    #: questions name a broad specialty, so this is what find_doctor filters on.
    specialty_group: Mapped[str] = mapped_column(String(80), index=True)
    taxonomy_code: Mapped[str | None] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(80), index=True)
    state: Mapped[str] = mapped_column(String(2))
    zip_code: Mapped[str] = mapped_column(String(10))
    phone: Mapped[str | None] = mapped_column(String(32))
    in_network: Mapped[bool] = mapped_column(Boolean, index=True)
    #: The documented rule that decided ``in_network``, for auditability.
    network_rule: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(Text)
    retrieved_on: Mapped[dt.date] = mapped_column(Date)


class ProviderMemberLink(Base):
    """Which members a provider may see (Phase 11 provider mode)."""

    __tablename__ = "provider_member_links"
    __table_args__ = (UniqueConstraint("provider_npi", "member_id", name="uq_provider_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_npi: Mapped[str] = mapped_column(
        String(10), ForeignKey("providers.npi", ondelete="CASCADE"), index=True
    )
    member_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("members.member_id", ondelete="CASCADE"), index=True
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------------------
# prices (real CMS data)
# ---------------------------------------------------------------------------
class ProcedurePrice(Base):
    """A Medicare benchmark price for one procedure code and place of service.

    ``avg_submitted_charge`` is nullable on purpose: the CMS utilization dataset
    publishes it, the Physician Fee Schedule relative value files do not. The
    cost estimator produces a low-high range when it is present and a single
    benchmark estimate when it is not, and says which in the answer.
    """

    __tablename__ = "procedure_prices"
    __table_args__ = (
        UniqueConstraint(
            "hcpcs_code", "place_of_service", "data_year", name="uq_price_code_pos_year"
        ),
        CheckConstraint(f"place_of_service IN {PLACES_OF_SERVICE}", name="ck_price_pos"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hcpcs_code: Mapped[str] = mapped_column(String(8), index=True)
    description: Mapped[str] = mapped_column(String(300))
    place_of_service: Mapped[str] = mapped_column(String(16))
    avg_submitted_charge: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    avg_allowed: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    avg_payment: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    data_year: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(Text)
    #: How avg_allowed was obtained, e.g. the RVU x conversion-factor formula.
    derivation: Mapped[str] = mapped_column(Text)
    retrieved_on: Mapped[dt.date] = mapped_column(Date)


# ---------------------------------------------------------------------------
# claims (synthetic, built from Synthea encounters and real prices)
# ---------------------------------------------------------------------------
class Claim(Base):
    """A synthetic claim. Its status always records the rule that produced it."""

    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(f"status IN {CLAIM_STATUSES}", name="ck_claims_status"),
        CheckConstraint(
            f"denial_code IS NULL OR denial_code IN {DENIAL_CODES}",
            name="ck_claims_denial_code",
        ),
        # A denied claim must say why; a non-denied claim must not pretend to.
        CheckConstraint(
            "(status = 'DENIED') = (denial_code IS NOT NULL)",
            name="ck_claims_denial_consistency",
        ),
        Index("ix_claims_member_service_date", "member_id", "service_date"),
    )

    claim_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    member_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("members.member_id", ondelete="CASCADE"), index=True
    )
    provider_npi: Mapped[str] = mapped_column(
        String(10), ForeignKey("providers.npi", ondelete="RESTRICT"), index=True
    )
    service_date: Mapped[dt.date] = mapped_column(Date, index=True)
    icd10_codes: Mapped[list] = mapped_column(JSON, default=list)
    hcpcs_code: Mapped[str] = mapped_column(String(8), index=True)
    place_of_service: Mapped[str] = mapped_column(String(16))
    billed: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    allowed: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    plan_paid: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    member_resp: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(16), index=True)
    denial_code: Mapped[str | None] = mapped_column(String(32), index=True)
    denial_reason: Mapped[str | None] = mapped_column(Text)
    #: Plain-language record of why this status was assigned.
    status_rule: Mapped[str] = mapped_column(Text)
    pa_id: Mapped[str | None] = mapped_column(
        String(24), ForeignKey("pa_requests.pa_id", ondelete="SET NULL")
    )
    #: How much of ``member_resp`` counted toward the deductible.
    applied_to_deductible: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    #: How much of ``member_resp`` counted toward the out-of-pocket maximum.
    #: Out of network this is less than ``member_resp``, because the balance
    #: billed above the plan's allowed amount counts toward neither limit.
    applied_to_oop: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)

    member: Mapped[Member] = relationship(back_populates="claims")


class PaRequest(Base):
    """A prior-authorization request. Humans decide; we only track readiness."""

    __tablename__ = "pa_requests"
    __table_args__ = (CheckConstraint(f"status IN {PA_STATUSES}", name="ck_pa_status"),)

    pa_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    member_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("members.member_id", ondelete="CASCADE"), index=True
    )
    provider_npi: Mapped[str] = mapped_column(
        String(10), ForeignKey("providers.npi", ondelete="RESTRICT")
    )
    hcpcs_code: Mapped[str] = mapped_column(String(8))
    icd10_codes: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), index=True)
    submitted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    missing_items: Mapped[list] = mapped_column(JSON, default=list)
    notes_masked: Mapped[str | None] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class Refill(Base):
    """A medication refill schedule (Phase 15)."""

    __tablename__ = "refills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("members.member_id", ondelete="CASCADE"), index=True
    )
    rxcui: Mapped[str] = mapped_column(String(16), index=True)
    drug_name: Mapped[str] = mapped_column(String(200))
    last_fill: Mapped[dt.date] = mapped_column(Date)
    days_supply: Mapped[int] = mapped_column(Integer)
    next_due: Mapped[dt.date] = mapped_column(Date, index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------------------
# conversations (masked text only)
# ---------------------------------------------------------------------------
class Conversation(Base):
    """One chat session. ``contained`` means it ended without a human handoff."""

    __tablename__ = "conversations"
    __table_args__ = (
        # conversations.handoff_id and handoffs.conversation_id reference each
        # other. use_alter emits this one as a separate ALTER TABLE so the
        # tables can still be created in a valid order on PostgreSQL.
        ForeignKeyConstraint(
            ["handoff_id"],
            ["handoffs.id"],
            ondelete="SET NULL",
            use_alter=True,
            name="fk_conversations_handoff_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    contained: Mapped[bool | None] = mapped_column(Boolean)
    #: Constraint declared in __table_args__ with use_alter (see above).
    handoff_id: Mapped[int | None] = mapped_column(Integer)
    max_frustration: Mapped[float] = mapped_column(Numeric(4, 3), default=0)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    """One turn. ``text_masked`` is the only text form ever stored."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(f"role IN {MESSAGE_ROLES}", name="ck_messages_role"),
        CheckConstraint(f"path IS NULL OR path IN {MESSAGE_PATHS}", name="ck_messages_path"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    text_masked: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(64), index=True)
    intent_conf: Mapped[float | None] = mapped_column(Numeric(4, 3))
    injection_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    emotion: Mapped[str | None] = mapped_column(String(32))
    frustration: Mapped[float | None] = mapped_column(Numeric(4, 3))
    retrieval_conf: Mapped[float | None] = mapped_column(Numeric(5, 4))
    path: Mapped[str | None] = mapped_column(String(24), index=True)
    model_used: Mapped[str | None] = mapped_column(String(80))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    stage_timings: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    feedback: Mapped[Feedback | None] = relationship(
        back_populates="message", cascade="all, delete-orphan", uselist=False
    )


class Feedback(Base):
    """Thumbs up/down on one answer, with an optional comment."""

    __tablename__ = "feedback"
    __table_args__ = (CheckConstraint("rating IN (-1, 1)", name="ck_feedback_rating"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("messages.id", ondelete="CASCADE"), unique=True
    )
    rating: Mapped[int] = mapped_column(Integer)
    comment_masked: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    message: Mapped[Message] = relationship(back_populates="feedback")


class Handoff(Base):
    """A conversation escalated to a human agent."""

    __tablename__ = "handoffs"
    __table_args__ = (CheckConstraint(f"status IN {HANDOFF_STATUSES}", name="ck_handoff_status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    trigger: Mapped[str] = mapped_column(String(48), index=True)
    summary_masked: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    agent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )


class AuditLog(Base):
    """Append-only, hash-chained audit trail.

    ``hash`` covers this row's content plus ``prev_hash``, so altering or
    removing any earlier row breaks the chain detectably.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    conversation_id: Mapped[int | None] = mapped_column(Integer, index=True)
    event: Mapped[str] = mapped_column(String(48), index=True)
    details_masked: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class Upload(Base):
    """An uploaded document. The raw file is deleted after text extraction."""

    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    text_masked: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# data provenance
# ---------------------------------------------------------------------------
class DataSource(Base):
    """One row per external source actually used, written by the fetch scripts.

    ``docs/data_sources.md`` is the human-readable record; this table is the
    machine-readable one the admin page and the data-provenance test read.
    """

    __tablename__ = "data_sources"
    __table_args__ = (UniqueConstraint("key", name="uq_data_source_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    licence_note: Mapped[str] = mapped_column(Text)
    retrieved_on: Mapped[dt.date] = mapped_column(Date)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
