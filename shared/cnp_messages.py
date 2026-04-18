"""
shared/cnp_messages.py
──────────────────────
Contract Net Protocol message types for the Black River broker.

These Pydantic models are the canonical schema for all inter-agent
communication.  Both the real partner adapters (Phase 3) and the
simulated adapters (Phase 1) must produce and consume these types —
that is the architectural discipline that makes simulation-first work.

CNP Stages
──────────
  ANNOUNCED   → TaskAnnouncement broadcast to all registered producers
  BIDDING     → Producers submit ProducerBid responses
  EVALUATING  → Broker scores bids into BidEvaluation
  AWARDED     → ContractAward sent to winning producer
  EXECUTING   → ExecutionUpdate polled from winning producer
  DELIVERED   → DeliveryConfirmation received; payment triggered
  SETTLED     → PaymentSettlement recorded; contract complete
  FAILED      → Terminal failure at any stage
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Enumerations ──────────────────────────────────────────────────────────

class CNPStage(str, Enum):
    ANNOUNCED  = "ANNOUNCED"
    BIDDING    = "BIDDING"
    EVALUATING = "EVALUATING"
    AWARDED    = "AWARDED"
    EXECUTING  = "EXECUTING"
    DELIVERED  = "DELIVERED"
    SETTLED    = "SETTLED"
    FAILED     = "FAILED"


class ServiceType(str, Enum):
    DRONE_SURVEILLANCE        = "DRONE_SURVEILLANCE"
    DRONE_INSPECTION          = "DRONE_INSPECTION"
    GROUND_SURVEILLANCE       = "GROUND_SURVEILLANCE"
    SATELLITE_IMAGING         = "SATELLITE_IMAGING"


class CoverageFrequency(str, Enum):
    CONTINUOUS   = "CONTINUOUS"    # live feed
    HOURLY       = "HOURLY"
    TWICE_DAILY  = "TWICE_DAILY"
    DAILY        = "DAILY"
    ON_DEMAND    = "ON_DEMAND"


# ── Shared sub-models ─────────────────────────────────────────────────────

class GeoLocation(BaseModel):
    latitude:    float
    longitude:   float
    description: Optional[str] = None


class EvaluationCriteria(BaseModel):
    """Weights must sum to 1.0.  Broker validates before announcing."""
    price_weight:           float = Field(0.5,  ge=0.0, le=1.0)
    availability_weight:    float = Field(0.3,  ge=0.0, le=1.0)
    past_performance_weight:float = Field(0.2,  ge=0.0, le=1.0)

    def validate_weights(self) -> bool:
        total = (self.price_weight +
                 self.availability_weight +
                 self.past_performance_weight)
        return abs(total - 1.0) < 1e-6


# ── CNP Message Types ─────────────────────────────────────────────────────

class ServiceRequirement(BaseModel):
    """
    Posted by a Consumer Agent to initiate the procurement cycle.
    The broker converts this into a TaskAnnouncement.
    """
    requirement_id:    str = Field(default_factory=lambda: f"REQ-{uuid4().hex[:8].upper()}")
    consumer_id:       str
    service_type:      ServiceType
    description:       str                      # natural language description
    location:          GeoLocation
    start_time:        datetime
    end_time:          datetime
    coverage_frequency:CoverageFrequency
    budget_ceiling_usdc: float                  # max spend in USDC
    criteria:          EvaluationCriteria = Field(default_factory=EvaluationCriteria)
    metadata:          Dict[str, Any] = Field(default_factory=dict)
    created_at:        datetime = Field(default_factory=datetime.utcnow)


class TaskAnnouncement(BaseModel):
    """
    Broker → all registered Producers.
    Broadcast when procurement cycle begins.
    """
    announcement_id:     str = Field(default_factory=lambda: f"ANN-{uuid4().hex[:8].upper()}")
    requirement_id:      str
    service_type:        ServiceType
    description:         str
    location:            GeoLocation
    start_time:          datetime
    end_time:            datetime
    coverage_frequency:  CoverageFrequency
    budget_ceiling_usdc: float
    criteria:            EvaluationCriteria
    bid_deadline:        datetime               # producers must respond by this time
    broker_wallet:       str                    # on-chain address for contract
    announced_at:        datetime = Field(default_factory=datetime.utcnow)


class ProducerBid(BaseModel):
    """
    Producer → Broker response to a TaskAnnouncement.
    """
    bid_id:            str = Field(default_factory=lambda: f"BID-{uuid4().hex[:8].upper()}")
    announcement_id:   str
    producer_id:       str
    producer_name:     str
    price_usdc:        float                    # total price for the engagement
    available:         bool                     # can the producer fulfill the requirement?
    availability_note: Optional[str] = None     # e.g. "2-hour setup time required"
    past_performance_score: float = Field(..., ge=0.0, le=1.0)  # 0–1 normalised
    capabilities:      List[str] = Field(default_factory=list)   # e.g. ["4K video", "thermal"]
    proposal_narrative:str = ""                 # free-text proposal details
    producer_wallet:   str                      # on-chain address for payment
    submitted_at:      datetime = Field(default_factory=datetime.utcnow)


class BidScore(BaseModel):
    """Weighted score for a single bid."""
    bid_id:                  str
    producer_id:             str
    price_score:             float   # normalised 0–1 (lower price → higher score)
    availability_score:      float   # 1.0 if available, 0.0 if not
    past_performance_score:  float
    weighted_total:          float
    rationale:               str     # LLM-generated explanation


class BidEvaluation(BaseModel):
    """
    Broker internal — result of the evaluate_bids node.
    """
    requirement_id: str
    scores:         List[BidScore]
    winner_bid_id:  str
    evaluated_at:   datetime = Field(default_factory=datetime.utcnow)


class ContractAward(BaseModel):
    """
    Broker → winning Producer.
    Also written on-chain to the BlackRiverBroker contract.
    """
    award_id:          str = Field(default_factory=lambda: f"AWD-{uuid4().hex[:8].upper()}")
    requirement_id:    str
    announcement_id:   str
    winning_bid_id:    str
    producer_id:       str
    producer_wallet:   str
    price_usdc:        float
    contract_address:  Optional[str] = None     # on-chain contract address
    tx_hash:           Optional[str] = None     # award transaction hash
    awarded_at:        datetime = Field(default_factory=datetime.utcnow)


class ExecutionUpdate(BaseModel):
    """
    Producer → Broker status poll during EXECUTING stage.
    """
    award_id:     str
    producer_id:  str
    status:       str           # e.g. "IN_FLIGHT", "DELAYED", "COMPLETE"
    progress_pct: float = Field(0.0, ge=0.0, le=100.0)
    notes:        Optional[str] = None
    updated_at:   datetime = Field(default_factory=datetime.utcnow)


class DeliveryConfirmation(BaseModel):
    """
    Producer → Broker when service is complete.
    Broker verifies before triggering payment.
    """
    award_id:          str
    producer_id:       str
    delivery_evidence: str      # URL or IPFS hash of deliverable (e.g. video, report)
    delivery_hash:     str      # keccak256 of delivery evidence for on-chain verification
    confirmed_at:      datetime = Field(default_factory=datetime.utcnow)


class PaymentSettlement(BaseModel):
    """
    Broker records when MPP payment completes on-chain.
    """
    award_id:          str
    producer_wallet:   str
    amount_usdc:       float
    tx_hash:           str
    memo:              str      # TIP-20 memo — carries award_id + requirement_id
    settled_at:        datetime = Field(default_factory=datetime.utcnow)


# ── Audit event (written to chain at each stage transition) ───────────────

class AuditEvent(BaseModel):
    """
    Written to the on-chain AuditLog contract at every CNP stage transition.
    In Phase 1, this is written to the local Hardhat node.
    In Phase 2+, it is written to Tempo testnet/mainnet.
    """
    event_id:       str = Field(default_factory=lambda: f"EVT-{uuid4().hex[:8].upper()}")
    requirement_id: str
    stage:          CNPStage
    actor:          str         # broker, consumer_id, or producer_id
    summary:        str         # human-readable description of the event
    payload_hash:   str         # keccak256 of the triggering message payload
    tx_hash:        Optional[str] = None    # populated after on-chain write
    block_number:   Optional[int] = None
    timestamp:      datetime = Field(default_factory=datetime.utcnow)
