"""
agents/consumer/consumer_simulator.py
──────────────────────────────────────
Phase 1 consumer agent simulator.

Returns a pre-built ServiceRequirement for the demo scenario:
a real estate developer monitoring a construction site.

Phase 4: replace with a ConsumerUI class that collects requirements
from a web interface.  The interface (ConsumerAdapter) stays identical.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from shared.cnp_messages import (
    AuditEvent, CNPStage, ContractAward, CoverageFrequency,
    EvaluationCriteria, GeoLocation, PaymentSettlement,
    ServiceRequirement, ServiceType,
)
from agents.interfaces.consumer_adapter import ConsumerAdapter

logger = logging.getLogger(__name__)


class ConsumerSimulator(ConsumerAdapter):

    CONSUMER_ID   = "CONS-RE-DEV-001"
    CONSUMER_NAME = "Granite Peak Development"

    def __init__(self, requirement: ServiceRequirement | None = None):
        self._requirement = requirement or self._default_requirement()
        self._events: list[AuditEvent] = []

    @staticmethod
    def _default_requirement() -> ServiceRequirement:
        """
        Default demo scenario: construction site monitoring.
        Adjust parameters here to create different demo scenarios.
        """
        now = datetime.utcnow()
        return ServiceRequirement(
            consumer_id=  ConsumerSimulator.CONSUMER_ID,
            service_type= ServiceType.DRONE_SURVEILLANCE,
            description=(
                "Monitor progress at our River District mixed-use construction site. "
                "We need aerial coverage to verify contractor milestone completion, "
                "identify safety compliance issues, and provide weekly progress "
                "documentation for our project financing partners."
            ),
            location=GeoLocation(
                latitude=   44.4759,
                longitude= -73.2121,
                description="River District Site, Burlington, VT",
            ),
            start_time=          now + timedelta(hours=24),
            end_time=            now + timedelta(days=3),
            coverage_frequency=  CoverageFrequency.TWICE_DAILY,
            budget_ceiling_usdc= 3000.0,
            criteria=EvaluationCriteria(
                price_weight=           0.4,
                availability_weight=    0.35,
                past_performance_weight=0.25,
            ),
        )

    # ── ConsumerAdapter implementation ────────────────────────────────────

    def get_requirement(self) -> ServiceRequirement:
        logger.info(
            f"[{self.CONSUMER_NAME}] Posting requirement {self._requirement.requirement_id}: "
            f"{self._requirement.description[:60]}..."
        )
        return self._requirement

    def on_stage_update(self, event: AuditEvent) -> None:
        self._events.append(event)
        icon = {
            CNPStage.ANNOUNCED:  "📢",
            CNPStage.BIDDING:    "📨",
            CNPStage.EVALUATING: "⚖️ ",
            CNPStage.AWARDED:    "🏆",
            CNPStage.EXECUTING:  "🚁",
            CNPStage.DELIVERED:  "📦",
            CNPStage.SETTLED:    "💳",
            CNPStage.FAILED:     "❌",
        }.get(event.stage, "•")
        logger.info(f"  {icon}  [{event.stage.value}] {event.summary}")

    def on_award(self, award: ContractAward) -> None:
        logger.info(
            f"[{self.CONSUMER_NAME}] Contract awarded to producer "
            f"{award.producer_id} for ${award.price_usdc:.2f} USDC"
        )

    def on_settlement(self, settlement: PaymentSettlement) -> None:
        logger.info(
            f"[{self.CONSUMER_NAME}] Payment settled — "
            f"${settlement.amount_usdc:.2f} USDC  tx={settlement.tx_hash[:18]}..."
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    @property
    def events(self) -> list[AuditEvent]:
        return list(self._events)
