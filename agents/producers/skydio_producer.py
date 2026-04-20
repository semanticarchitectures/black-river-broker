"""
agents/producers/skydio_producer.py
─────────────────────────────────────
Phase 3 producer: Skydio Cloud API.

Skydio provides autonomous drone hardware and cloud mission management.
Ideal for physical site surveillance — onboard AI obstacle avoidance,
tight waypoint accuracy, and encrypted video downlink.

API reference: https://apidocs.skydio.com
Auth:          SKYDIO_API_TOKEN env var → Authorization: Bearer <token>
Base URL:      https://api.skydio.com/api/v0

CNP ↔ Skydio Cloud mapping
───────────────────────────
  can_fulfil       → GET /vehicle?available=true          (check fleet availability)
  submit_bid       → calculate price from service params
  accept_award     → POST /order                          (create flight order)
  get_status       → GET /order/{order_id}                (poll order status)
  confirm_delivery → GET /media?order_id={id}&type=video  (get media download URL)

Required env vars:
  SKYDIO_API_TOKEN             Skydio Cloud API token
  PRODUCER_SKYDIO_WALLET       on-chain address for payment (Tempo testnet)
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Dict

from shared.cnp_messages import (
    ContractAward,
    DeliveryConfirmation,
    ExecutionUpdate,
    ProducerBid,
    ServiceType,
    TaskAnnouncement,
)
from agents.producers.http_producer_base import HttpProducerBase, ProducerApiError

logger = logging.getLogger(__name__)

# Skydio Cloud order status → CNP status
_SKYDIO_STATUS_MAP = {
    "created":    "PENDING",
    "scheduled":  "PENDING",
    "in_transit": "MOBILISING",
    "flying":     "IN_FLIGHT",
    "landing":    "IN_FLIGHT",
    "completed":  "COMPLETE",
    "failed":     "FAILED",
    "cancelled":  "FAILED",
}

# Pricing constants (Skydio Enterprise tier)
_MOBILISATION_FEE_USD  = 350.0   # crew + vehicle dispatch
_DAILY_RATE_USD        = 700.0   # per-day autonomous flight coverage
_PERFORMANCE_SCORE     = 0.93    # Skydio published safety + delivery stats


class SkydioProducer(HttpProducerBase):
    """
    Real Skydio Cloud API integration.

    Stores _order_id from accept_award for subsequent polling.
    """

    PRODUCER_ID   = "PROD-SKYDIO"
    PRODUCER_NAME = "Skydio"

    _base_url = "https://api.skydio.com/api/v0"

    def __init__(self, dry_run: bool = False) -> None:
        super().__init__(timeout=20.0, dry_run=dry_run)
        self._api_token = os.environ["SKYDIO_API_TOKEN"]
        self._wallet    = os.environ.get(
            "PRODUCER_SKYDIO_WALLET",
            "0x0000000000000000000000000000000000000003",
        )
        self._order_id: str | None = None

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._api_token}"}

    # ── ProducerAdapter lifecycle ─────────────────────────────────────────────

    def can_fulfil(self, announcement: TaskAnnouncement) -> bool:
        supported = {ServiceType.DRONE_SURVEILLANCE, ServiceType.DRONE_INSPECTION}
        if announcement.service_type not in supported:
            return False
        days      = max((announcement.end_time - announcement.start_time).days, 1)
        min_price = _MOBILISATION_FEE_USD + (_DAILY_RATE_USD * days)
        if announcement.budget_ceiling_usdc < min_price:
            return False
        if self.dry_run:
            # Skip live fleet check; budget gate above is sufficient for dry-run
            return True
        try:
            resp = self._get("/vehicle", params={"available": "true"})
            # Skydio returns { "vehicles": [...] } or { "data": [...] }
            vehicles = resp.get("vehicles") or resp.get("data") or []
            return len(vehicles) > 0
        except ProducerApiError as e:
            logger.warning("[Skydio] can_fulfil fleet check failed: %s", e)
            return False

    def submit_bid(self, announcement: TaskAnnouncement) -> ProducerBid:
        days  = max((announcement.end_time - announcement.start_time).days, 1)
        price = _MOBILISATION_FEE_USD + (_DAILY_RATE_USD * days)

        return ProducerBid(
            announcement_id=        announcement.announcement_id,
            producer_id=            self.PRODUCER_ID,
            producer_name=          self.PRODUCER_NAME,
            price_usdc=             round(price, 2),
            available=              True,
            availability_note=      "1-hour mobilisation; autonomous flight, no pilot required on-site",
            past_performance_score= _PERFORMANCE_SCORE,
            capabilities=[
                "autonomous obstacle avoidance",
                "encrypted 4K live downlink",
                "thermal + RGB sensors",
                "GPS-denied indoor navigation",
                "FAA Remote ID compliant",
                "cloud-synced flight logs",
                "AI perimeter breach detection",
            ],
            proposal_narrative=(
                f"Skydio will deploy a fully autonomous drone for {days}-day coverage "
                f"of {announcement.location.description}. The vehicle executes "
                f"{announcement.coverage_frequency.value.lower()} patrol routes with "
                f"onboard AI. Deliverables: encrypted video archive + anomaly event log."
            ),
            producer_wallet=self._wallet,
        )

    def accept_award(self, award: ContractAward) -> bool:
        if self.dry_run:
            self._order_id = f"dry-run-order-{award.award_id}"
            logger.info("[Skydio][DRY-RUN] Award %s accepted (no API call)", award.award_id)
            return True
        try:
            order = self._post("/order", {
                "external_id":  award.award_id,
                "mission_type": "surveillance",
                "location": {
                    "description": award.requirement_id,
                },
                "start_time":   award.awarded_at.isoformat(),
            })
            self._order_id = order.get("order_id") or order.get("id")
            logger.info(
                "[Skydio] Order %s created for award %s",
                self._order_id, award.award_id,
            )
            return True
        except ProducerApiError as e:
            logger.error("[Skydio] accept_award failed: %s", e)
            return False

    def get_status(self, award_id: str) -> ExecutionUpdate:
        if self.dry_run:
            return ExecutionUpdate(
                award_id=award_id, producer_id=self.PRODUCER_ID,
                status="COMPLETE", progress_pct=100.0,
                notes="Skydio dry-run: flight complete",
            )
        if not self._order_id:
            return ExecutionUpdate(
                award_id=award_id,
                producer_id=self.PRODUCER_ID,
                status="PENDING",
                progress_pct=0.0,
                notes="Order not yet created",
            )

        data           = self._get(f"/order/{self._order_id}")
        skydio_status  = data.get("status", "created").lower()
        progress       = float(data.get("progress_percent", 0))
        status         = _SKYDIO_STATUS_MAP.get(skydio_status, "IN_FLIGHT")
        notes          = data.get("status_message") or f"Skydio order status: {skydio_status}"

        return ExecutionUpdate(
            award_id=    award_id,
            producer_id= self.PRODUCER_ID,
            status=      status,
            progress_pct=progress,
            notes=       notes,
        )

    def confirm_delivery(self, award_id: str) -> DeliveryConfirmation:
        if self.dry_run:
            evidence_url  = f"https://skywatch.example/dry-run/{award_id}/footage.mp4"
            return DeliveryConfirmation(
                award_id=award_id, producer_id=self.PRODUCER_ID,
                delivery_evidence=evidence_url,
                delivery_hash=hashlib.sha256(evidence_url.encode()).hexdigest(),
            )
        media = self._get("/media", params={"order_id": self._order_id, "type": "video"})
        items = media.get("media") or media.get("data") or []

        if items:
            evidence_url = items[0].get("download_url", "")
        else:
            evidence_url = (
                f"https://console.skydio.com/media?order_id={self._order_id}"
            )

        delivery_hash = hashlib.sha256(evidence_url.encode()).hexdigest()
        logger.info("[Skydio] Delivery confirmed — media: %s", evidence_url)

        return DeliveryConfirmation(
            award_id=          award_id,
            producer_id=       self.PRODUCER_ID,
            delivery_evidence= evidence_url,
            delivery_hash=     delivery_hash,
        )
