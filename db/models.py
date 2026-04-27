"""
db/models.py
────────────
SQLAlchemy 2.0 ORM models for the Black River Broker.

Entity groups
─────────────
  Producers      supply side: companies offering drone/sensor services
  Consumers      demand side: companies with project requirements
  Projects       engagements, scoped in phases; each phase may have its own deal
  Deals          negotiated agreements; spot, master-agreement, or retainer
  Performance    delivery records that feed producer reliability scoring
  Issues         problems during execution with full resolution audit trail
  Research       the broker's continuous market-discovery activity
  Outreach       communication log with producer contacts
  Broker events  immutable system-level audit log

Design notes
────────────
  • UUID primary keys throughout — no sequence contention, safe to merge shards
  • TimestampMixin adds created_at / updated_at to every table
  • Soft deletes (is_active) where rows are referenced by foreign keys
  • Vector(1536) columns on Producer and ProjectRequirement enable semantic search
    (pgvector extension; falls back to Text in SQLite dev environments)
  • JSONB for structured-but-variable data (pricing terms, capability metadata)
  • State machines expressed as String + CheckConstraint; see *Status enums
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Float, ForeignKey,
    Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

try:
    from pgvector.sqlalchemy import Vector
    _VEC = Vector(1536)
    HAS_PGVECTOR = True
except ImportError:
    _VEC = Text          # SQLite / environments without pgvector
    HAS_PGVECTOR = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )


# ── Enumerations ──────────────────────────────────────────────────────────────

class ProducerStatus(str, PyEnum):
    """State machine for the broker's relationship with a producer."""
    DISCOVERED         = "discovered"       # found via research
    CONTACTED          = "contacted"        # outreach sent
    RESPONDED          = "responded"        # replied; capabilities being assessed
    QUALIFIED          = "qualified"        # confirmed capabilities & coverage
    ACTIVE             = "active"           # has current or recent engagement
    DORMANT            = "dormant"          # no activity 6+ months
    INACTIVE           = "inactive"         # no longer operating / unresponsive


class ServiceType(str, PyEnum):
    """Canonical service categories used for capability matching."""
    AERIAL_PHOTOGRAPHY     = "aerial_photography"
    LIDAR_SCANNING         = "lidar_scanning"
    MULTISPECTRAL_IMAGING  = "multispectral_imaging"
    THERMAL_IMAGING        = "thermal_imaging"
    PHOTOGRAMMETRY         = "photogrammetry"
    THREE_D_MODELING       = "3d_modeling"
    INSPECTION             = "inspection"
    MAPPING                = "mapping"
    SURVEILLANCE           = "surveillance"
    CONSTRUCTION_PROGRESS  = "construction_progress"
    OTHER                  = "other"


class ProjectStatus(str, PyEnum):
    """Lifecycle of a consumer engagement from scoping through delivery."""
    SCOPING        = "scoping"        # consumer defining requirements
    RFQ_SENT       = "rfq_sent"       # broker issued RFQs
    NEGOTIATING    = "negotiating"    # active deal negotiation
    CONTRACTED     = "contracted"     # signed, not yet started
    ACTIVE         = "active"         # work in progress
    ON_HOLD        = "on_hold"        # temporarily paused
    COMPLETED      = "completed"      # all phases accepted
    DISPUTED       = "disputed"       # open issue, resolution pending
    CANCELLED      = "cancelled"      # terminated


class DealType(str, PyEnum):
    SPOT             = "spot"         # single transaction
    MASTER_AGREEMENT = "master"       # framework + task orders
    RETAINER         = "retainer"     # recurring periodic service


class DealStatus(str, PyEnum):
    DRAFT      = "draft"
    PROPOSED   = "proposed"
    ACCEPTED   = "accepted"
    ACTIVE     = "active"
    COMPLETED  = "completed"
    TERMINATED = "terminated"
    DISPUTED   = "disputed"


class IssueType(str, PyEnum):
    MISSED_DEADLINE      = "missed_deadline"
    QUALITY_BELOW_SLA    = "quality_below_sla"
    NO_COMMUNICATION     = "no_communication"
    SCOPE_DISPUTE        = "scope_dispute"
    PAYMENT_DISPUTE      = "payment_dispute"
    SAFETY_INCIDENT      = "safety_incident"
    OTHER                = "other"


class IssueStatus(str, PyEnum):
    DETECTED          = "detected"
    NOTIFIED          = "notified"          # both parties informed
    MEDIATING         = "mediating"         # broker negotiating
    RESOLVED          = "resolved"          # closed satisfactorily
    ESCALATED         = "escalated"         # mediation failed
    REPLACEMENT_FOUND = "replacement_found" # new producer identified
    CLOSED            = "closed"            # fully resolved


class OutreachStatus(str, PyEnum):
    DRAFT       = "draft"
    SENT        = "sent"
    RESPONDED   = "responded"
    NO_RESPONSE = "no_response"
    BOUNCED     = "bounced"
    OPTED_OUT   = "opted_out"


# ═════════════════════════════════════════════════════════════════════════════
# PRODUCER SIDE
# ═════════════════════════════════════════════════════════════════════════════

class Producer(TimestampMixin, Base):
    """
    A company that provides aerial / sensor services.

    The broker discovers producers through research, qualifies them through
    outreach, and builds a performance history through completed engagements.
    reliability_score (0.0–1.0) is computed from PerformanceRecords and
    updated whenever a new record is written.

    description_embedding enables semantic search: "find producers that do
    weekly construction progress documentation in the Pacific Northwest."
    """
    __tablename__ = "producers"

    id:              Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    name:            Mapped[str] = mapped_column(String(256), nullable=False)
    website:         Mapped[Optional[str]] = mapped_column(String(512))
    email_domain:    Mapped[Optional[str]] = mapped_column(String(128))   # e.g. "aeroplan.io"
    phone:           Mapped[Optional[str]] = mapped_column(String(32))
    hq_country:      Mapped[Optional[str]] = mapped_column(String(64))
    hq_state:        Mapped[Optional[str]] = mapped_column(String(64))
    hq_city:         Mapped[Optional[str]] = mapped_column(String(128))

    status:          Mapped[str] = mapped_column(
        String(32), default=ProducerStatus.DISCOVERED,
        nullable=False
    )
    is_active:       Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Derived from PerformanceRecord; updated by trigger / application logic
    reliability_score:   Mapped[Optional[float]] = mapped_column(Float)   # 0.0–1.0
    completed_projects:  Mapped[int]              = mapped_column(Integer, default=0, nullable=False)
    avg_response_hours:  Mapped[Optional[float]]  = mapped_column(Float)  # avg outreach → reply

    # Free-text description scraped/summarised from website + past comms
    description:           Mapped[Optional[str]] = mapped_column(Text)
    description_embedding: Mapped[Optional[str]] = mapped_column(_VEC)  # pgvector in prod

    # Metadata from research
    source_url:       Mapped[Optional[str]] = mapped_column(String(512))  # where broker found them
    last_researched:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes:            Mapped[Optional[str]]  = mapped_column(Text)        # broker internal notes

    __table_args__ = (
        CheckConstraint("status IN ('discovered','contacted','responded','qualified','active','dormant','inactive')", name="ck_producer_status"),
        CheckConstraint("reliability_score IS NULL OR (reliability_score >= 0.0 AND reliability_score <= 1.0)", name="ck_reliability_range"),
    )

    contacts:           Mapped[List["ProducerContact"]]     = relationship(back_populates="producer", cascade="all, delete-orphan")
    capabilities:       Mapped[List["ProducerCapability"]]  = relationship(back_populates="producer", cascade="all, delete-orphan")
    coverage_areas:     Mapped[List["ProducerCoverageArea"]]= relationship(back_populates="producer", cascade="all, delete-orphan")
    certifications:     Mapped[List["ProducerCertification"]]= relationship(back_populates="producer", cascade="all, delete-orphan")
    pricing_history:    Mapped[List["ProducerPricingRecord"]]= relationship(back_populates="producer")
    outreach_records:   Mapped[List["OutreachRecord"]]      = relationship(back_populates="producer")
    performance_records:Mapped[List["PerformanceRecord"]]   = relationship(back_populates="producer")
    deals:              Mapped[List["Deal"]]                = relationship(back_populates="producer")


class ProducerContact(TimestampMixin, Base):
    """A named individual at a producer company."""
    __tablename__ = "producer_contacts"

    id:          Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    producer_id: Mapped[str] = mapped_column(ForeignKey("producers.id"), nullable=False, index=True)
    name:        Mapped[str] = mapped_column(String(256), nullable=False)
    role:        Mapped[Optional[str]] = mapped_column(String(128))   # "Operations Manager"
    email:       Mapped[Optional[str]] = mapped_column(String(256))
    phone:       Mapped[Optional[str]] = mapped_column(String(32))
    is_primary:  Mapped[bool]          = mapped_column(Boolean, default=False, nullable=False)
    notes:       Mapped[Optional[str]] = mapped_column(Text)

    producer: Mapped["Producer"] = relationship(back_populates="contacts")


class ProducerCapability(TimestampMixin, Base):
    """
    A specific service a producer can deliver.

    service_type is the canonical category; capability_detail is free-text
    (e.g. "weekly photogrammetry scans for construction progress tracking,
    output as point cloud + orthoimage"). capability_embedding allows the
    broker to find this capability via semantic similarity search when a
    consumer describes their need in natural language.
    """
    __tablename__ = "producer_capabilities"

    id:              Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    producer_id:     Mapped[str] = mapped_column(ForeignKey("producers.id"), nullable=False, index=True)
    service_type:    Mapped[str] = mapped_column(String(64), nullable=False)
    capability_detail:    Mapped[Optional[str]] = mapped_column(Text)
    capability_embedding: Mapped[Optional[str]] = mapped_column(_VEC)

    # Operational parameters
    max_area_km2:       Mapped[Optional[float]] = mapped_column(Float)   # single-flight coverage
    min_flight_hours:   Mapped[Optional[float]] = mapped_column(Float)
    typical_lead_days:  Mapped[Optional[int]]   = mapped_column(Integer) # days from order to flight

    # Equipment used for this capability
    equipment_notes: Mapped[Optional[str]] = mapped_column(Text)   # "DJI Matrice 300 + P1 camera"
    extra:           Mapped[Optional[dict]] = mapped_column(JSONB)  # extensible key-value store

    __table_args__ = (
        CheckConstraint(
            "service_type IN ('aerial_photography','lidar_scanning','multispectral_imaging',"
            "'thermal_imaging','photogrammetry','3d_modeling','inspection','mapping',"
            "'surveillance','construction_progress','other')",
            name="ck_service_type"
        ),
    )

    producer: Mapped["Producer"] = relationship(back_populates="capabilities")


class ProducerCoverageArea(TimestampMixin, Base):
    """Geographic area a producer will service."""
    __tablename__ = "producer_coverage_areas"

    id:          Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    producer_id: Mapped[str] = mapped_column(ForeignKey("producers.id"), nullable=False, index=True)
    country:     Mapped[str] = mapped_column(String(64), nullable=False)
    state:       Mapped[Optional[str]] = mapped_column(String(64))
    city:        Mapped[Optional[str]] = mapped_column(String(128))
    radius_km:   Mapped[Optional[float]] = mapped_column(Float)   # radius from city centre
    notes:       Mapped[Optional[str]]  = mapped_column(Text)

    producer: Mapped["Producer"] = relationship(back_populates="coverage_areas")


class ProducerCertification(TimestampMixin, Base):
    """Regulatory certifications and insurance held by a producer."""
    __tablename__ = "producer_certifications"

    id:          Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    producer_id: Mapped[str] = mapped_column(ForeignKey("producers.id"), nullable=False, index=True)
    name:        Mapped[str] = mapped_column(String(128), nullable=False)  # "FAA Part 107"
    issuer:      Mapped[Optional[str]] = mapped_column(String(128))
    cert_number: Mapped[Optional[str]] = mapped_column(String(128))
    issued_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    producer: Mapped["Producer"] = relationship(back_populates="certifications")


class ProducerPricingRecord(TimestampMixin, Base):
    """
    Observed pricing data point for a producer.

    Populated from quotes received during negotiations. Over time these build
    into a market-rate reference the broker uses to assess whether a quote
    is competitive. Never shown to consumers.
    """
    __tablename__ = "producer_pricing_history"

    id:            Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    producer_id:   Mapped[str] = mapped_column(ForeignKey("producers.id"), nullable=False, index=True)
    service_type:  Mapped[str] = mapped_column(String(64), nullable=False)
    price_model:   Mapped[str] = mapped_column(String(32), nullable=False)  # "per_day","per_flight","fixed"
    amount_usd:    Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit:          Mapped[str]   = mapped_column(String(32), nullable=False)  # "day","flight","km2"
    scope_notes:   Mapped[Optional[str]] = mapped_column(Text)
    deal_id:       Mapped[Optional[str]] = mapped_column(ForeignKey("deals.id"))
    observed_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    producer: Mapped["Producer"] = relationship(back_populates="pricing_history")


# ═════════════════════════════════════════════════════════════════════════════
# CONSUMER SIDE
# ═════════════════════════════════════════════════════════════════════════════

class Consumer(TimestampMixin, Base):
    """
    A company or individual seeking drone/sensor services.

    Consumers come to the broker with a project need. The broker learns their
    preferences (quality vs. price weighting, preferred delivery formats, past
    experiences) and applies that knowledge to future engagements.
    """
    __tablename__ = "consumers"

    id:       Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    name:     Mapped[str] = mapped_column(String(256), nullable=False)
    industry: Mapped[Optional[str]] = mapped_column(String(128))   # "construction","agriculture"
    website:  Mapped[Optional[str]] = mapped_column(String(512))
    hq_country: Mapped[Optional[str]] = mapped_column(String(64))
    hq_state:   Mapped[Optional[str]] = mapped_column(String(64))
    hq_city:    Mapped[Optional[str]] = mapped_column(String(128))
    is_active:  Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Broker-maintained preference profile (updated after each engagement)
    preferences: Mapped[Optional[dict]] = mapped_column(JSONB)
    # e.g. {"price_weight": 0.3, "quality_weight": 0.5, "speed_weight": 0.2,
    #        "preferred_producers": [...], "blocked_producers": [...]}

    notes: Mapped[Optional[str]] = mapped_column(Text)

    contacts: Mapped[List["ConsumerContact"]] = relationship(back_populates="consumer", cascade="all, delete-orphan")
    projects: Mapped[List["Project"]]         = relationship(back_populates="consumer")


class ConsumerContact(TimestampMixin, Base):
    __tablename__ = "consumer_contacts"

    id:          Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    consumer_id: Mapped[str] = mapped_column(ForeignKey("consumers.id"), nullable=False, index=True)
    name:        Mapped[str] = mapped_column(String(256), nullable=False)
    role:        Mapped[Optional[str]] = mapped_column(String(128))
    email:       Mapped[Optional[str]] = mapped_column(String(256))
    phone:       Mapped[Optional[str]] = mapped_column(String(32))
    is_primary:  Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    consumer: Mapped["Consumer"] = relationship(back_populates="contacts")


# ═════════════════════════════════════════════════════════════════════════════
# PROJECTS, PHASES, REQUIREMENTS
# ═════════════════════════════════════════════════════════════════════════════

class Project(TimestampMixin, Base):
    """
    A consumer engagement that may span multiple phases and producers.

    A construction project is the canonical example: site-prep scans differ
    from structural-inspection scans which differ from facade-documentation
    scans. Each phase may be contracted separately or under a master agreement.

    next_checkin_at drives the broker's monitoring loop — it queries for
    projects where this timestamp has passed and dispatches check-in tasks.
    """
    __tablename__ = "projects"

    id:          Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    consumer_id: Mapped[str] = mapped_column(ForeignKey("consumers.id"), nullable=False, index=True)
    name:        Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    status:      Mapped[str] = mapped_column(String(32), default=ProjectStatus.SCOPING, nullable=False)

    # Geography
    site_country: Mapped[Optional[str]] = mapped_column(String(64))
    site_state:   Mapped[Optional[str]] = mapped_column(String(64))
    site_city:    Mapped[Optional[str]] = mapped_column(String(128))
    site_address: Mapped[Optional[str]] = mapped_column(String(512))
    site_area_km2:Mapped[Optional[float]] = mapped_column(Float)

    # Timeline
    planned_start:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    planned_end:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_start:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_end:     Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Budget (consumer-private — never surfaced to producers)
    budget_ceiling_usd: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))

    # Monitoring
    next_checkin_at:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    checkin_interval_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('scoping','rfq_sent','negotiating','contracted','active',"
            "'on_hold','completed','disputed','cancelled')",
            name="ck_project_status"
        ),
    )

    consumer: Mapped["Consumer"]          = relationship(back_populates="projects")
    phases:   Mapped[List["ProjectPhase"]]= relationship(back_populates="project", cascade="all, delete-orphan", order_by="ProjectPhase.sequence")
    deals:    Mapped[List["Deal"]]        = relationship(back_populates="project")
    issues:   Mapped[List["Issue"]]       = relationship(back_populates="project")


class ProjectPhase(TimestampMixin, Base):
    """
    A distinct stage within a project with its own service requirements.

    Examples for a construction project:
      seq 1  Site Survey & Baseline       — one-time full-site photogrammetry
      seq 2  Foundation Progress          — weekly scans, limited area
      seq 3  Structural Framing           — bi-weekly full-site + 3D model
      seq 4  Facade & Envelope            — thermal + optical inspection
      seq 5  Punch List & Closeout        — final documentation orthoimage

    A phase may reference a specific deal (task order) or be uncontracted
    (still in scoping).
    """
    __tablename__ = "project_phases"

    id:         Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    sequence:   Mapped[int] = mapped_column(Integer, nullable=False)
    name:       Mapped[str] = mapped_column(String(256), nullable=False)
    description:Mapped[Optional[str]] = mapped_column(Text)
    status:     Mapped[str] = mapped_column(String(32), default="pending", nullable=False)

    planned_start:Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    planned_end:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_end:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Linked deal (may be null if phase is not yet contracted)
    deal_id:    Mapped[Optional[str]] = mapped_column(ForeignKey("deals.id"))

    project:      Mapped["Project"]              = relationship(back_populates="phases")
    requirements: Mapped[List["ProjectRequirement"]] = relationship(back_populates="phase", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("project_id", "sequence", name="uq_phase_sequence"),
        CheckConstraint("status IN ('pending','rfq_sent','contracted','active','completed','skipped')", name="ck_phase_status"),
    )


class ProjectRequirement(TimestampMixin, Base):
    """
    A specific deliverable or constraint for a project phase.

    requirement_embedding enables the broker to find producers by semantic
    similarity: embed the requirement text and query against
    ProducerCapability.capability_embedding.

    Examples:
      "Weekly RGB orthoimagery at 2cm GSD for a 4-hectare excavation site"
      "Bi-weekly LiDAR point cloud, ≥100 pts/m², delivered as LAZ + DEM"
    """
    __tablename__ = "project_requirements"

    id:         Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    phase_id:   Mapped[str] = mapped_column(ForeignKey("project_phases.id"), nullable=False, index=True)
    description:Mapped[str] = mapped_column(Text, nullable=False)

    service_type:         Mapped[Optional[str]] = mapped_column(String(64))
    frequency:            Mapped[Optional[str]] = mapped_column(String(64))   # "weekly","bi-weekly","monthly","as-needed"
    area_km2:             Mapped[Optional[float]] = mapped_column(Float)
    resolution_notes:     Mapped[Optional[str]]  = mapped_column(String(256)) # "2cm GSD"
    deliverable_formats:  Mapped[Optional[dict]] = mapped_column(JSONB)       # {"orthoimage":true,"point_cloud":true}
    sla_turnaround_hours: Mapped[Optional[int]]  = mapped_column(Integer)     # hours from flight to delivery

    requirement_embedding: Mapped[Optional[str]] = mapped_column(_VEC)

    phase: Mapped["ProjectPhase"] = relationship(back_populates="requirements")


# ═════════════════════════════════════════════════════════════════════════════
# DEALS AND TASK ORDERS
# ═════════════════════════════════════════════════════════════════════════════

class Deal(TimestampMixin, Base):
    """
    A negotiated agreement between the broker (on behalf of a consumer) and
    a producer.

    deal_type determines structure:
      SPOT            — single transaction, price_total is the full amount
      MASTER_AGREEMENT— framework contract; individual TaskOrders carry price
      RETAINER        — periodic fee covers a defined service level

    private_broker_notes are never surfaced to either party — they capture the
    broker's knowledge of each side's constraints used during negotiation.
    """
    __tablename__ = "deals"

    id:          Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id:  Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    producer_id: Mapped[str] = mapped_column(ForeignKey("producers.id"), nullable=False, index=True)
    deal_type:   Mapped[str] = mapped_column(String(32), nullable=False, default=DealType.SPOT)
    status:      Mapped[str] = mapped_column(String(32), nullable=False, default=DealStatus.DRAFT)

    # Pricing
    price_total_usd:    Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    price_per_unit_usd: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    price_unit:         Mapped[Optional[str]]   = mapped_column(String(32))  # "day","flight","km2"
    currency:           Mapped[str]             = mapped_column(String(8), default="USD", nullable=False)

    # Contract terms stored as structured JSON
    # e.g. {"notice_period_days":14,"payment_net_days":30,"penalties":{...}}
    terms: Mapped[Optional[dict]] = mapped_column(JSONB)

    # On-chain settlement (from existing ChainClient integration)
    award_id:           Mapped[Optional[str]] = mapped_column(String(128))
    settlement_tx_hash: Mapped[Optional[str]] = mapped_column(String(128))
    settlement_block:   Mapped[Optional[int]] = mapped_column(Integer)

    # Broker's private negotiation context — never shown to either party
    private_broker_notes: Mapped[Optional[str]] = mapped_column(Text)
    consumer_budget_at_negotiation: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    producer_floor_at_negotiation:  Mapped[Optional[float]] = mapped_column(Numeric(12, 2))

    effective_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    effective_to:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("deal_type IN ('spot','master','retainer')", name="ck_deal_type"),
        CheckConstraint("status IN ('draft','proposed','accepted','active','completed','terminated','disputed')", name="ck_deal_status"),
    )

    project:      Mapped["Project"]          = relationship(back_populates="deals")
    producer:     Mapped["Producer"]         = relationship(back_populates="deals")
    task_orders:  Mapped[List["TaskOrder"]]  = relationship(back_populates="deal", cascade="all, delete-orphan")
    performance:  Mapped[List["PerformanceRecord"]] = relationship(back_populates="deal")


class TaskOrder(TimestampMixin, Base):
    """
    A specific work order issued under a Master Agreement.

    Each phase of a construction project typically becomes one task order.
    The task order references the phase it covers and carries its own
    pricing (may vary from the master-agreement unit rate).
    """
    __tablename__ = "task_orders"

    id:       Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    deal_id:  Mapped[str] = mapped_column(ForeignKey("deals.id"), nullable=False, index=True)
    phase_id: Mapped[Optional[str]] = mapped_column(ForeignKey("project_phases.id"))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    title:    Mapped[str] = mapped_column(String(256), nullable=False)
    scope:    Mapped[Optional[str]] = mapped_column(Text)

    price_usd:   Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    status:      Mapped[str] = mapped_column(String(32), default="pending", nullable=False)

    issued_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    due_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at:Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    settlement_tx_hash: Mapped[Optional[str]] = mapped_column(String(128))

    __table_args__ = (
        CheckConstraint("status IN ('pending','issued','in_progress','delivered','accepted','disputed','cancelled')", name="ck_task_order_status"),
    )

    deal:  Mapped["Deal"]                = relationship(back_populates="task_orders")
    phase: Mapped[Optional["ProjectPhase"]] = relationship()


# ═════════════════════════════════════════════════════════════════════════════
# PERFORMANCE AND RELIABILITY
# ═════════════════════════════════════════════════════════════════════════════

class PerformanceRecord(TimestampMixin, Base):
    """
    Delivery assessment for a specific engagement or task order.

    Written by the broker after each deliverable is accepted or disputed.
    The fields here feed the reliability_score computation on Producer.

    on_time, quality_met, and communication_score are the three primary
    inputs to reliability scoring. consumer_satisfaction (0–5) is the
    consumer's explicit rating if they provide one.
    """
    __tablename__ = "performance_records"

    id:          Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    producer_id: Mapped[str] = mapped_column(ForeignKey("producers.id"), nullable=False, index=True)
    deal_id:     Mapped[Optional[str]] = mapped_column(ForeignKey("deals.id"))
    task_order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("task_orders.id"))

    on_time:              Mapped[Optional[bool]]  = mapped_column(Boolean)
    hours_early_late:     Mapped[Optional[float]] = mapped_column(Float)    # negative = late
    quality_met:          Mapped[Optional[bool]]  = mapped_column(Boolean)
    quality_notes:        Mapped[Optional[str]]   = mapped_column(Text)
    communication_score:  Mapped[Optional[int]]   = mapped_column(Integer)  # 1–5
    consumer_satisfaction:Mapped[Optional[int]]   = mapped_column(Integer)  # 1–5

    issue_id:    Mapped[Optional[str]] = mapped_column(ForeignKey("issues.id"))
    notes:       Mapped[Optional[str]] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("communication_score IS NULL OR communication_score BETWEEN 1 AND 5", name="ck_comm_score"),
        CheckConstraint("consumer_satisfaction IS NULL OR consumer_satisfaction BETWEEN 1 AND 5", name="ck_satisfaction"),
    )

    producer:   Mapped["Producer"]          = relationship(back_populates="performance_records")
    deal:       Mapped[Optional["Deal"]]    = relationship(back_populates="performance")


# ═════════════════════════════════════════════════════════════════════════════
# ISSUES AND RESOLUTION
# ═════════════════════════════════════════════════════════════════════════════

class Issue(TimestampMixin, Base):
    """
    A problem detected during project execution.

    State machine: detected → notified → mediating → resolved
                                                   → escalated → replacement_found → closed

    The broker detects issues through:
      - Check-in responses from consumer or producer
      - Missed task-order deadlines (automated monitoring)
      - Explicit consumer complaint

    resolution_summary is written by the broker when the issue closes and
    becomes part of the producer's history.
    """
    __tablename__ = "issues"

    id:            Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id:    Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    deal_id:       Mapped[Optional[str]] = mapped_column(ForeignKey("deals.id"))
    task_order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("task_orders.id"))
    producer_id:   Mapped[Optional[str]] = mapped_column(ForeignKey("producers.id"))

    issue_type:  Mapped[str] = mapped_column(String(64), nullable=False)
    severity:    Mapped[str] = mapped_column(String(16), default="medium", nullable=False)  # low/medium/high/critical
    status:      Mapped[str] = mapped_column(String(32), default=IssueStatus.DETECTED, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    detected_at:  Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolution_summary: Mapped[Optional[str]] = mapped_column(Text)

    # If escalated to replacement, track the new producer
    replacement_producer_id: Mapped[Optional[str]] = mapped_column(ForeignKey("producers.id"))
    replacement_deal_id:     Mapped[Optional[str]] = mapped_column(ForeignKey("deals.id"))

    __table_args__ = (
        CheckConstraint("severity IN ('low','medium','high','critical')", name="ck_issue_severity"),
        CheckConstraint("status IN ('detected','notified','mediating','resolved','escalated','replacement_found','closed')", name="ck_issue_status"),
    )

    project:  Mapped["Project"]          = relationship(back_populates="issues")
    events:   Mapped[List["IssueEvent"]] = relationship(back_populates="issue", cascade="all, delete-orphan", order_by="IssueEvent.occurred_at")


class IssueEvent(Base):
    """
    Immutable audit log of every action taken on an issue.

    actor is "broker", "consumer", or "producer:{id}".
    action is a short verb phrase: "notified_producer", "consumer_responded",
    "mediation_proposal_sent", "replacement_contracted", etc.
    """
    __tablename__ = "issue_events"

    id:         Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    issue_id:   Mapped[str] = mapped_column(ForeignKey("issues.id"), nullable=False, index=True)
    actor:      Mapped[str] = mapped_column(String(128), nullable=False)
    action:     Mapped[str] = mapped_column(String(128), nullable=False)
    detail:     Mapped[Optional[str]] = mapped_column(Text)
    occurred_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    issue: Mapped["Issue"] = relationship(back_populates="events")


# ═════════════════════════════════════════════════════════════════════════════
# RESEARCH AND OUTREACH
# ═════════════════════════════════════════════════════════════════════════════

class ResearchRun(TimestampMixin, Base):
    """
    A discrete market-research session executed by the broker's research agent.

    The broker runs these periodically (weekly by default) and also on-demand
    when a consumer request arrives for a service type / region where the
    registry is thin.
    """
    __tablename__ = "research_runs"

    id:           Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    trigger:      Mapped[str] = mapped_column(String(64), nullable=False)  # "scheduled","consumer_request","manual"
    service_type: Mapped[Optional[str]] = mapped_column(String(64))
    region:       Mapped[Optional[str]] = mapped_column(String(128))       # free text: "Pacific Northwest, USA"
    queries_run:  Mapped[int]  = mapped_column(Integer, default=0, nullable=False)
    urls_analyzed:Mapped[int]  = mapped_column(Integer, default=0, nullable=False)
    new_producers:Mapped[int]  = mapped_column(Integer, default=0, nullable=False)
    updated_producers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status:       Mapped[str]  = mapped_column(String(32), default="running", nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes:        Mapped[Optional[str]] = mapped_column(Text)

    findings: Mapped[List["ResearchFinding"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class ResearchFinding(TimestampMixin, Base):
    """
    A single finding from a research run — typically a discovered or updated producer.

    raw_data stores the scraped/extracted content before it was normalised
    into Producer rows. Useful for re-processing if extraction logic improves.
    """
    __tablename__ = "research_findings"

    id:          Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id:      Mapped[str] = mapped_column(ForeignKey("research_runs.id"), nullable=False, index=True)
    finding_type:Mapped[str] = mapped_column(String(32), nullable=False)  # "new_producer","updated_producer","irrelevant"
    source_url:  Mapped[Optional[str]] = mapped_column(String(512))
    producer_id: Mapped[Optional[str]] = mapped_column(ForeignKey("producers.id"))
    raw_data:    Mapped[Optional[dict]] = mapped_column(JSONB)             # extracted key-value pairs
    notes:       Mapped[Optional[str]] = mapped_column(Text)

    run:      Mapped["ResearchRun"]       = relationship(back_populates="findings")
    producer: Mapped[Optional["Producer"]] = relationship()


class OutreachRecord(TimestampMixin, Base):
    """
    A communication from the broker to a producer contact.

    channel is typically "email" but may be "linkedin", "phone", "web_form".
    message_body stores the full text so the broker can reference prior
    outreach when following up.

    response_summary is written by the broker after processing a reply —
    it captures the key facts (capabilities confirmed, pricing range shared,
    availability stated) rather than storing the raw email thread.
    """
    __tablename__ = "outreach_records"

    id:           Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    producer_id:  Mapped[str] = mapped_column(ForeignKey("producers.id"), nullable=False, index=True)
    contact_id:   Mapped[Optional[str]] = mapped_column(ForeignKey("producer_contacts.id"))
    channel:      Mapped[str] = mapped_column(String(32), default="email", nullable=False)
    outreach_type:Mapped[str] = mapped_column(String(64), nullable=False)  # "initial_discovery","rfq","follow_up","check_in","issue_notification"
    status:       Mapped[str] = mapped_column(String(32), default=OutreachStatus.DRAFT, nullable=False)

    subject:      Mapped[Optional[str]] = mapped_column(String(512))
    message_body: Mapped[Optional[str]] = mapped_column(Text)
    sent_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    response_summary: Mapped[Optional[str]] = mapped_column(Text)   # broker's digest of reply

    # Link to the project or deal this outreach relates to (if any)
    project_id:   Mapped[Optional[str]] = mapped_column(ForeignKey("projects.id"))
    deal_id:      Mapped[Optional[str]] = mapped_column(ForeignKey("deals.id"))

    __table_args__ = (
        CheckConstraint("status IN ('draft','sent','responded','no_response','bounced','opted_out')", name="ck_outreach_status"),
    )

    producer: Mapped["Producer"] = relationship(back_populates="outreach_records")


# ═════════════════════════════════════════════════════════════════════════════
# BROKER EVENT LOG
# ═════════════════════════════════════════════════════════════════════════════

class BrokerEvent(Base):
    """
    Immutable audit log of significant broker actions.

    Append-only — no updates, no deletes. Provides a complete history of
    what the broker did and why. event_type is a dot-namespaced string:
      research.run_started       research.producer_discovered
      outreach.sent              outreach.response_received
      negotiation.rfq_issued     negotiation.deal_accepted
      project.phase_started      project.checkin_sent
      issue.detected             issue.resolved
      payment.settlement_tx      payment.confirmed
    """
    __tablename__ = "broker_events"

    id:           Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    event_type:   Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    occurred_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    # Optional foreign keys — only set the ones relevant to this event
    producer_id:    Mapped[Optional[str]] = mapped_column(ForeignKey("producers.id"))
    consumer_id:    Mapped[Optional[str]] = mapped_column(ForeignKey("consumers.id"))
    project_id:     Mapped[Optional[str]] = mapped_column(ForeignKey("projects.id"))
    deal_id:        Mapped[Optional[str]] = mapped_column(ForeignKey("deals.id"))
    issue_id:       Mapped[Optional[str]] = mapped_column(ForeignKey("issues.id"))
    outreach_id:    Mapped[Optional[str]] = mapped_column(ForeignKey("outreach_records.id"))
    research_run_id:Mapped[Optional[str]] = mapped_column(ForeignKey("research_runs.id"))

    summary:  Mapped[str]            = mapped_column(Text, nullable=False)
    detail:   Mapped[Optional[dict]] = mapped_column(JSONB)   # any structured context
    tx_hash:  Mapped[Optional[str]]  = mapped_column(String(128))  # on-chain tx if applicable
