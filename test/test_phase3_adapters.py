"""
test/test_phase3_adapters.py
────────────────────────────
Phase 3 integration tests for real producer adapters.

Uses `respx` to intercept httpx calls — no real network required.
Each producer is exercised through the full CNP lifecycle:
  can_fulfil → submit_bid → accept_award → get_status → confirm_delivery

Run:
    pytest test/test_phase3_adapters.py -v
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
import respx
import httpx

from shared.cnp_messages import (
    ContractAward,
    CoverageFrequency,
    EvaluationCriteria,
    GeoLocation,
    ServiceType,
    TaskAnnouncement,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("DRONEDEPLOY_API_KEY",           "test-dd-key")
    monkeypatch.setenv("PRODUCER_DRONEDEPLOY_WALLET",   "0x" + "A" * 40)
    monkeypatch.setenv("SKYDIO_API_TOKEN",               "test-skydio-token")
    monkeypatch.setenv("PRODUCER_SKYDIO_WALLET",         "0x" + "B" * 40)


@pytest.fixture
def announcement() -> TaskAnnouncement:
    now = datetime.utcnow()
    return TaskAnnouncement(
        requirement_id=     "REQ-TEST0001",
        service_type=       ServiceType.DRONE_SURVEILLANCE,
        description=        "Construction site monitoring",
        location=           GeoLocation(latitude=44.47, longitude=-73.21, description="Burlington, VT"),
        start_time=         now + timedelta(hours=4),
        end_time=           now + timedelta(days=3, hours=4),
        coverage_frequency= CoverageFrequency.DAILY,
        budget_ceiling_usdc=5000.0,
        criteria=           EvaluationCriteria(price_weight=0.4, availability_weight=0.35, past_performance_weight=0.25),
        bid_deadline=       now + timedelta(hours=2),
        broker_wallet=      "0x" + "C" * 40,
    )


@pytest.fixture
def award(announcement: TaskAnnouncement) -> ContractAward:
    return ContractAward(
        requirement_id=  announcement.requirement_id,
        announcement_id= announcement.announcement_id,
        winning_bid_id=  "BID-FAKE0001",
        producer_id=     "PROD-TEST",
        producer_wallet= "0x" + "A" * 40,
        price_usdc=      1400.0,
        contract_address="0x" + "D" * 40,
        tx_hash=         "0xabcd1234",
    )


# ── DroneDeploy ───────────────────────────────────────────────────────────────

class TestDroneDeployProducer:

    def test_can_fulfil_returns_true_when_api_key_valid(self, announcement):
        from agents.producers.dronedeploy_producer import DroneDeployProducer
        with respx.mock:
            respx.get("https://api.dronedeploy.com/external/v1/user").mock(
                return_value=httpx.Response(200, json={"id": "user-123", "email": "test@dd.com"})
            )
            p = DroneDeployProducer()
            assert p.can_fulfil(announcement) is True

    def test_can_fulfil_returns_false_on_auth_error(self, announcement):
        from agents.producers.dronedeploy_producer import DroneDeployProducer
        with respx.mock:
            respx.get("https://api.dronedeploy.com/external/v1/user").mock(
                return_value=httpx.Response(401, json={"error": "Unauthorized"})
            )
            p = DroneDeployProducer()
            assert p.can_fulfil(announcement) is False

    def test_can_fulfil_returns_false_below_budget(self, announcement):
        from agents.producers.dronedeploy_producer import DroneDeployProducer
        cheap = announcement.model_copy(update={"budget_ceiling_usdc": 10.0})
        p = DroneDeployProducer()
        # Budget check is local — no HTTP call expected
        assert p.can_fulfil(cheap) is False

    def test_submit_bid_price_matches_formula(self, announcement):
        from agents.producers.dronedeploy_producer import (
            DroneDeployProducer, _PLATFORM_FEE_USD, _DAILY_RATE_USD
        )
        p    = DroneDeployProducer()
        bid  = p.submit_bid(announcement)
        days = max((announcement.end_time - announcement.start_time).days, 1)
        assert bid.price_usdc == round(_PLATFORM_FEE_USD + _DAILY_RATE_USD * days, 2)
        assert bid.producer_id == DroneDeployProducer.PRODUCER_ID
        assert bid.announcement_id == announcement.announcement_id
        assert bid.past_performance_score > 0.0
        assert len(bid.capabilities) >= 4

    def test_accept_award_creates_project_and_mission(self, award):
        from agents.producers.dronedeploy_producer import DroneDeployProducer
        with respx.mock:
            respx.post("https://api.dronedeploy.com/external/v1/projects").mock(
                return_value=httpx.Response(201, json={"id": "proj-abc"})
            )
            respx.post("https://api.dronedeploy.com/external/v1/projects/proj-abc/missions").mock(
                return_value=httpx.Response(201, json={"id": "mission-xyz"})
            )
            p  = DroneDeployProducer()
            ok = p.accept_award(award)
        assert ok is True
        assert p._project_id == "proj-abc"
        assert p._mission_id == "mission-xyz"

    def test_accept_award_returns_false_on_server_error(self, award):
        from agents.producers.dronedeploy_producer import DroneDeployProducer
        with respx.mock:
            respx.post("https://api.dronedeploy.com/external/v1/projects").mock(
                return_value=httpx.Response(503, json={"error": "Service unavailable"})
            )
            p  = DroneDeployProducer()
            ok = p.accept_award(award)
        assert ok is False

    def test_get_status_maps_complete(self, award):
        from agents.producers.dronedeploy_producer import DroneDeployProducer
        with respx.mock:
            respx.get("https://api.dronedeploy.com/external/v1/missions/mission-xyz").mock(
                return_value=httpx.Response(200, json={
                    "status": "complete",
                    "progress": 100.0,
                    "status_note": "Mission finished",
                })
            )
            p = DroneDeployProducer()
            p._mission_id = "mission-xyz"
            update = p.get_status(award.award_id)
        assert update.status == "COMPLETE"
        assert update.progress_pct == 100.0

    def test_get_status_maps_in_progress(self, award):
        from agents.producers.dronedeploy_producer import DroneDeployProducer
        with respx.mock:
            respx.get("https://api.dronedeploy.com/external/v1/missions/mission-xyz").mock(
                return_value=httpx.Response(200, json={"status": "in_progress", "progress": 55.0})
            )
            p = DroneDeployProducer()
            p._mission_id = "mission-xyz"
            update = p.get_status(award.award_id)
        assert update.status == "IN_FLIGHT"

    def test_get_status_before_accept_returns_pending(self, award):
        from agents.producers.dronedeploy_producer import DroneDeployProducer
        p      = DroneDeployProducer()
        update = p.get_status(award.award_id)
        assert update.status == "PENDING"

    def test_confirm_delivery_returns_export_url(self, award):
        from agents.producers.dronedeploy_producer import DroneDeployProducer
        with respx.mock:
            respx.get(
                "https://api.dronedeploy.com/external/v1/exports",
                params={"project_id": "proj-abc", "status": "complete"},
            ).mock(return_value=httpx.Response(
                200, json={"data": [{"download_url": "https://s3.example/file.zip"}]}
            ))
            p = DroneDeployProducer()
            p._project_id = "proj-abc"
            conf = p.confirm_delivery(award.award_id)
        assert conf.delivery_evidence == "https://s3.example/file.zip"
        assert len(conf.delivery_hash) == 64

    def test_confirm_delivery_falls_back_gracefully(self, award):
        from agents.producers.dronedeploy_producer import DroneDeployProducer
        with respx.mock:
            respx.get(
                "https://api.dronedeploy.com/external/v1/exports",
                params={"project_id": "proj-abc", "status": "complete"},
            ).mock(return_value=httpx.Response(200, json={"data": []}))
            p = DroneDeployProducer()
            p._project_id = "proj-abc"
            conf = p.confirm_delivery(award.award_id)
        assert "proj-abc" in conf.delivery_evidence
        assert conf.delivery_hash

    def test_satisfies_producer_adapter_abc(self):
        from agents.interfaces.producer_adapter import ProducerAdapter
        from agents.producers.dronedeploy_producer import DroneDeployProducer
        assert issubclass(DroneDeployProducer, ProducerAdapter)
        assert not DroneDeployProducer.__abstractmethods__


# ── Skydio ────────────────────────────────────────────────────────────────────

class TestSkydioProducer:

    def test_can_fulfil_returns_true_when_fleet_available(self, announcement):
        from agents.producers.skydio_producer import SkydioProducer
        with respx.mock:
            respx.get(
                "https://api.skydio.com/api/v0/vehicle",
                params={"available": "true"},
            ).mock(return_value=httpx.Response(
                200, json={"vehicles": [{"id": "v1", "available": True}]}
            ))
            p = SkydioProducer()
            assert p.can_fulfil(announcement) is True

    def test_can_fulfil_returns_false_when_no_fleet(self, announcement):
        from agents.producers.skydio_producer import SkydioProducer
        with respx.mock:
            respx.get(
                "https://api.skydio.com/api/v0/vehicle",
                params={"available": "true"},
            ).mock(return_value=httpx.Response(200, json={"vehicles": []}))
            p = SkydioProducer()
            assert p.can_fulfil(announcement) is False

    def test_can_fulfil_returns_false_below_budget(self, announcement):
        from agents.producers.skydio_producer import SkydioProducer
        cheap = announcement.model_copy(update={"budget_ceiling_usdc": 50.0})
        p = SkydioProducer()
        assert p.can_fulfil(cheap) is False

    def test_submit_bid_price_matches_formula(self, announcement):
        from agents.producers.skydio_producer import (
            SkydioProducer, _MOBILISATION_FEE_USD, _DAILY_RATE_USD
        )
        p    = SkydioProducer()
        bid  = p.submit_bid(announcement)
        days = max((announcement.end_time - announcement.start_time).days, 1)
        assert bid.price_usdc == round(_MOBILISATION_FEE_USD + _DAILY_RATE_USD * days, 2)
        assert bid.producer_id == SkydioProducer.PRODUCER_ID
        assert bid.past_performance_score > 0.0

    def test_accept_award_creates_order(self, award):
        from agents.producers.skydio_producer import SkydioProducer
        with respx.mock:
            respx.post("https://api.skydio.com/api/v0/order").mock(
                return_value=httpx.Response(201, json={"order_id": "order-123"})
            )
            p  = SkydioProducer()
            ok = p.accept_award(award)
        assert ok is True
        assert p._order_id == "order-123"

    def test_accept_award_returns_false_on_failure(self, award):
        from agents.producers.skydio_producer import SkydioProducer
        with respx.mock:
            respx.post("https://api.skydio.com/api/v0/order").mock(
                return_value=httpx.Response(500, json={"error": "Internal error"})
            )
            p  = SkydioProducer()
            ok = p.accept_award(award)
        assert ok is False

    def test_get_status_maps_completed(self, award):
        from agents.producers.skydio_producer import SkydioProducer
        with respx.mock:
            respx.get("https://api.skydio.com/api/v0/order/order-123").mock(
                return_value=httpx.Response(200, json={
                    "status": "completed",
                    "progress_percent": 100.0,
                    "status_message": "Flight complete",
                })
            )
            p = SkydioProducer()
            p._order_id = "order-123"
            update = p.get_status(award.award_id)
        assert update.status == "COMPLETE"

    def test_get_status_maps_flying(self, award):
        from agents.producers.skydio_producer import SkydioProducer
        with respx.mock:
            respx.get("https://api.skydio.com/api/v0/order/order-123").mock(
                return_value=httpx.Response(200, json={"status": "flying", "progress_percent": 60.0})
            )
            p = SkydioProducer()
            p._order_id = "order-123"
            update = p.get_status(award.award_id)
        assert update.status == "IN_FLIGHT"

    def test_confirm_delivery_returns_media_url(self, award):
        from agents.producers.skydio_producer import SkydioProducer
        with respx.mock:
            respx.get(
                "https://api.skydio.com/api/v0/media",
                params={"order_id": "order-123", "type": "video"},
            ).mock(return_value=httpx.Response(
                200, json={"media": [{"download_url": "https://cdn.skydio.com/video.mp4"}]}
            ))
            p = SkydioProducer()
            p._order_id = "order-123"
            conf = p.confirm_delivery(award.award_id)
        assert conf.delivery_evidence == "https://cdn.skydio.com/video.mp4"
        assert len(conf.delivery_hash) == 64

    def test_confirm_delivery_fallback_url(self, award):
        from agents.producers.skydio_producer import SkydioProducer
        with respx.mock:
            respx.get(
                "https://api.skydio.com/api/v0/media",
                params={"order_id": "order-123", "type": "video"},
            ).mock(return_value=httpx.Response(200, json={"media": []}))
            p = SkydioProducer()
            p._order_id = "order-123"
            conf = p.confirm_delivery(award.award_id)
        assert "order-123" in conf.delivery_evidence

    def test_satisfies_producer_adapter_abc(self):
        from agents.interfaces.producer_adapter import ProducerAdapter
        from agents.producers.skydio_producer import SkydioProducer
        assert issubclass(SkydioProducer, ProducerAdapter)
        assert not SkydioProducer.__abstractmethods__


# ── HttpProducerBase error handling ───────────────────────────────────────────

class TestHttpProducerBaseErrors:

    def test_rate_limit_caught_and_returns_false(self, announcement):
        from agents.producers.dronedeploy_producer import DroneDeployProducer
        with respx.mock:
            # All 4 attempts return 429; can_fulfil catches ProducerApiError → False
            respx.get("https://api.dronedeploy.com/external/v1/user").mock(
                return_value=httpx.Response(429, json={"error": "rate limited"})
            )
            p = DroneDeployProducer()
            assert p.can_fulfil(announcement) is False

    def test_auth_error_causes_accept_award_false(self, award):
        from agents.producers.dronedeploy_producer import DroneDeployProducer
        with respx.mock:
            respx.post("https://api.dronedeploy.com/external/v1/projects").mock(
                return_value=httpx.Response(403, json={"error": "Forbidden"})
            )
            p  = DroneDeployProducer()
            ok = p.accept_award(award)
        assert ok is False
