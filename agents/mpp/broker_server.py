"""
agents/mpp/broker_server.py
────────────────────────────
Black River MPP Broker Server — Phase 2.

Exposes the broker agent as an HTTP service using the Machine Payments
Protocol (MPP).  Consumer agents:
  1. POST /procure  →  receive HTTP 402 with WWW-Authenticate: Payment
  2. Pay the quoted USDC amount on-chain to the broker wallet
  3. Retry POST /procure with Authorization: Payment proof
  4. Receive the procurement result (winner, tx hash, delivery evidence)

The pympp FastAPI middleware handles all 402 challenge / credential
verification logic.  The broker graph and ChainClient run as normal.

Usage:
  # Start the server (reads .env.tempo_testnet by default)
  python -m agents.mpp.broker_server

  # Point at a specific env file
  python -m agents.mpp.broker_server --env .env.tempo_testnet

  # Use a custom port
  python -m agents.mpp.broker_server --port 8080
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── Request / Response models ─────────────────────────────────────────────────

class ProcureRequest(BaseModel):
    """
    Consumer's procurement request submitted to POST /procure.
    Mirrors ServiceRequirement but uses plain JSON-serialisable types.
    """
    description:          str
    location_description: str
    location_lat:         float  = 0.0
    location_lon:         float  = 0.0
    service_type:         str    = "DRONE_SURVEILLANCE"
    start_date:           str    # ISO 8601 date  e.g. "2026-05-01"
    end_date:             str    # ISO 8601 date  e.g. "2026-05-04"
    coverage_frequency:   str    = "DAILY"
    budget_ceiling_usdc:  float  = 1000.0
    price_weight:         float  = 0.5
    availability_weight:  float  = 0.3
    past_performance_weight: float = 0.2


class ProcureResponse(BaseModel):
    """Returned on successful settlement."""
    requirement_id: str
    winner_id:      str
    winner_name:    str
    amount_usdc:    float
    tx_hash:        str
    memo:           str
    settled_at:     str
    audit_events:   int


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(dry_run: bool = False) -> FastAPI:
    """
    Build and return the FastAPI application.

    If pympp is installed and BROKER_PRIVATE_KEY is set, the /procure
    endpoint is gated behind an MPP payment challenge.

    If pympp is NOT installed (e.g. during local Hardhat testing without
    a Tempo wallet), the endpoint runs without payment gating and logs a
    warning — useful for integration testing before testnet deploy.
    """
    from shared.cnp_messages import (
        CNPStage, CoverageFrequency, EvaluationCriteria,
        Location, ServiceRequirement, ServiceType,
    )
    from agents.broker.broker_graph import BrokerAgent
    from agents.broker.chain_client import ChainClient
    from agents.consumer.consumer_simulator import ConsumerSimulator
    from agents.producers.drone_ops_producer import DroneOpsSoftwareProducer
    from agents.producers.drone_service_producer import DroneServiceProducer

    app = FastAPI(
        title="Black River Broker",
        description="Autonomous procurement broker — Machine Payments Protocol",
        version="2.0.0",
    )

    broker_wallet = os.getenv("BROKER_WALLET", "")
    broker_key    = os.getenv("BROKER_PRIVATE_KEY") or os.getenv("DEPLOYER_PRIVATE_KEY", "")
    rpc_url       = (
        os.getenv("TEMPO_TESTNET_RPC")
        or os.getenv("HARDHAT_RPC", "http://127.0.0.1:8545")
    )

    # ── Try to wire up pympp payment middleware ───────────────────────────────
    mpp_enabled = False
    mpp_server  = None
    try:
        from mpp.server import Mpp
        from mpp.methods.tempo import tempo, ChargeIntent

        if broker_key and broker_wallet and not dry_run:
            mpp_server = Mpp.create(
                method=tempo(
                    intents={"charge": ChargeIntent()},
                    recipient=broker_wallet,
                    rpc_url=rpc_url,
                ),
            )
            mpp_enabled = True
            logger.info(f"[MPP] Payment gating enabled — recipient {broker_wallet}")
        else:
            logger.warning("[MPP] Payment gating DISABLED (dry_run or missing wallet config)")
    except ImportError:
        logger.warning(
            "[MPP] pympp not installed — running without payment gating. "
            "Install with: pip install 'pympp[tempo]'"
        )

    # ── Shared broker components ──────────────────────────────────────────────
    producers = [DroneOpsSoftwareProducer(), DroneServiceProducer()]

    chain = ChainClient(
        rpc_url=              rpc_url,
        broker_wallet=        broker_wallet,
        private_key=          broker_key,
        audit_log_addr=       os.getenv("AUDIT_LOG_ADDRESS", ""),
        broker_contract_addr= os.getenv("BROKER_CONTRACT_ADDRESS", ""),
        usdc_addr=            os.getenv("MOCK_USDC_ADDRESS", ""),
        dry_run=              dry_run,
    )

    broker = BrokerAgent(
        producers=     producers,
        consumer=      ConsumerSimulator(),   # used only for on_stage_update callbacks
        chain=         chain,
        broker_wallet= broker_wallet,
        dry_run=       dry_run,
    )

    # ── Helper: build ServiceRequirement from ProcureRequest ─────────────────

    def _build_requirement(req: ProcureRequest) -> ServiceRequirement:
        start = datetime.fromisoformat(req.start_date)
        end   = datetime.fromisoformat(req.end_date)
        return ServiceRequirement(
            description=req.description,
            location=Location(
                description=req.location_description,
                lat=req.location_lat,
                lon=req.location_lon,
            ),
            service_type=ServiceType[req.service_type],
            start_time=start,
            end_time=end,
            coverage_frequency=CoverageFrequency[req.coverage_frequency],
            budget_ceiling_usdc=req.budget_ceiling_usdc,
            criteria=EvaluationCriteria(
                price_weight=req.price_weight,
                availability_weight=req.availability_weight,
                past_performance_weight=req.past_performance_weight,
            ),
        )

    # ── Core procurement handler ──────────────────────────────────────────────

    async def _run_procurement(req: ProcureRequest) -> JSONResponse:
        try:
            requirement = _build_requirement(req)
        except (KeyError, ValueError) as e:
            return JSONResponse(status_code=422, content={"error": str(e)})

        try:
            result = broker.run(requirement)
        except Exception as e:
            logger.exception("[SERVER] Broker graph error")
            return JSONResponse(status_code=500, content={"error": str(e)})

        if result["stage"] != CNPStage.SETTLED:
            return JSONResponse(
                status_code=502,
                content={
                    "error":   "Procurement failed",
                    "reason":  result.get("failure_reason", "unknown"),
                    "stage":   result["stage"].value,
                },
            )

        award      = result["award"]
        settlement = result["settlement"]
        return JSONResponse(content=ProcureResponse(
            requirement_id=requirement.requirement_id,
            winner_id=     award.producer_id,
            winner_name=   award.producer_id,
            amount_usdc=   settlement.amount_usdc,
            tx_hash=       settlement.tx_hash,
            memo=          settlement.memo,
            settled_at=    settlement.settled_at.isoformat(),
            audit_events=  len(result["audit_events"]),
        ).model_dump())

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {
            "status":      "ok",
            "mpp_enabled": mpp_enabled,
            "dry_run":     dry_run,
            "network":     rpc_url,
            "broker":      broker_wallet or "(not set)",
        }

    @app.get("/info")
    async def info() -> Dict[str, Any]:
        """MPP service discovery endpoint."""
        return {
            "name":        "Black River Autonomous Broker",
            "description": "Procures drone surveillance services via CNP auction",
            "version":     "2.0.0",
            "endpoints": {
                "procure": {
                    "method":      "POST",
                    "path":        "/procure",
                    "payment":     "MPP / TIP-20 USDC on Tempo testnet",
                    "recipient":   broker_wallet or "(not set)",
                    "currency":    "USDC",
                    "amount_note": "Amount equals budget_ceiling_usdc from request body",
                },
            },
        }

    if mpp_enabled and mpp_server is not None:
        # ── Payment-gated endpoint ────────────────────────────────────────────
        @app.post("/procure")
        @mpp_server.pay(amount_key="budget_ceiling_usdc")
        async def procure_paid(
            req:        ProcureRequest,
            credential: Any,   # mpp.Credential — injected by pympp middleware
            receipt:    Any,   # mpp.Receipt    — injected by pympp middleware
        ) -> JSONResponse:
            logger.info(
                f"[SERVER] /procure  payer={getattr(credential, 'source', '?')}  "
                f"amount={req.budget_ceiling_usdc} USDC"
            )
            return await _run_procurement(req)

    else:
        # ── Ungated endpoint (dev / dry-run) ──────────────────────────────────
        @app.post("/procure")
        async def procure_ungated(req: ProcureRequest) -> JSONResponse:
            logger.info(
                f"[SERVER] /procure (no payment gate)  "
                f"budget={req.budget_ceiling_usdc} USDC"
            )
            return await _run_procurement(req)

    return app


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Black River MPP Broker Server")
    parser.add_argument("--env",     metavar="FILE", default=None,
                        help="Env file to load (default: .env.tempo_testnet then .env.local)")
    parser.add_argument("--port",    type=int, default=None,
                        help="Port to listen on (default: MPP_SERVER_PORT env var, or 8000)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Disable on-chain writes and MPP payment gating")
    args = parser.parse_args()

    # Load environment
    if args.env:
        load_dotenv(args.env, override=True)
    load_dotenv(".env.tempo_testnet", override=False)
    load_dotenv(".env.local",         override=False)
    load_dotenv(".env",               override=False)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    port = args.port or int(os.getenv("MPP_SERVER_PORT", "8000"))
    app  = create_app(dry_run=args.dry_run)

    print(f"\n  Black River MPP Broker Server")
    print(f"  http://localhost:{port}/health")
    print(f"  http://localhost:{port}/info")
    print(f"  POST http://localhost:{port}/procure\n")

    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
