"""
agents/producers/drone_service_producer.py
───────────────────────────────────────────
Simulated producer: Drone Service Provider.

Provides physical aerial surveillance — flight crews, aircraft,
FAA compliance.  Higher price than software-only providers but
offers end-to-end flight operations with no third-party dependency.
"""

from __future__ import annotations

import hashlib
import logging
import time

from shared.cnp_messages import (
    ContractAward, DeliveryConfirmation, ExecutionUpdate,
    ProducerBid, ServiceType, TaskAnnouncement,
)
from agents.interfaces.producer_adapter import ProducerAdapter

logger = logging.getLogger(__name__)


class DroneServiceProducer(ProducerAdapter):

    PRODUCER_ID   = "PROD-DRONE-SVC"
    PRODUCER_NAME = "SkyWatch Operations"

    _BASE_PRICE_PER_DAY  = 620.0
    _MOBILISATION_FEE    = 300.0
    _PAST_PERFORMANCE    = 0.92     # strong flight ops track record
    _WALLET              = "0x5KyWaTcHoPeRaTiOnS00000000000000000002"

    def can_fulfil(self, announcement: TaskAnnouncement) -> bool:
        supported = {ServiceType.DRONE_SURVEILLANCE, ServiceType.DRONE_INSPECTION}
        if announcement.service_type not in supported:
            return False
        days      = (announcement.end_time - announcement.start_time).days or 1
        min_price = self._MOBILISATION_FEE + (self._BASE_PRICE_PER_DAY * days)
        return announcement.budget_ceiling_usdc >= min_price

    def submit_bid(self, announcement: TaskAnnouncement) -> ProducerBid:
        days  = max((announcement.end_time - announcement.start_time).days, 1)
        price = self._MOBILISATION_FEE + (self._BASE_PRICE_PER_DAY * days)

        return ProducerBid(
            announcement_id=       announcement.announcement_id,
            producer_id=           self.PRODUCER_ID,
            producer_name=         self.PRODUCER_NAME,
            price_usdc=            round(price, 2),
            available=             True,
            availability_note=     "2-hour mobilisation from local base",
            past_performance_score=self._PAST_PERFORMANCE,
            capabilities=[
                "Part 107 certified pilots",
                "4K and thermal cameras",
                "FAA LAANC pre-authorisation",
                "live video downlink",
                "physical site security verification",
            ],
            proposal_narrative=(
                f"SkyWatch will deploy a certified flight crew for "
                f"{days}-day site coverage. Full FAA compliance included. "
                f"Deliverable: raw flight footage + daily incident summary."
            ),
            producer_wallet=self._WALLET,
        )

    def accept_award(self, award: ContractAward) -> bool:
        logger.info(f"[{self.PRODUCER_NAME}] Award {award.award_id} accepted ✓")
        self._active_award = award
        self._start_time   = time.time()
        return True

    def get_status(self, award_id: str) -> ExecutionUpdate:
        elapsed  = time.time() - self._start_time
        total    = 12.0
        progress = min(100.0, (elapsed / total) * 100)

        if progress < 20:
            status, notes = "MOBILISING", "Crew en route to site"
        elif progress < 90:
            status, notes = "IN_FLIGHT",  f"Active patrol — {progress:.0f}% complete"
        else:
            status, notes = "COMPLETE",   "Mission complete, footage secured"

        return ExecutionUpdate(
            award_id=    award_id,
            producer_id= self.PRODUCER_ID,
            status=      status,
            progress_pct=progress,
            notes=       notes,
        )

    def confirm_delivery(self, award_id: str) -> DeliveryConfirmation:
        evidence_url  = f"https://skywatch.example/missions/{award_id}/footage.mp4"
        delivery_hash = hashlib.sha256(evidence_url.encode()).hexdigest()

        return DeliveryConfirmation(
            award_id=          award_id,
            producer_id=       self.PRODUCER_ID,
            delivery_evidence= evidence_url,
            delivery_hash=     delivery_hash,
        )
