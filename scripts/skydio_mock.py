"""
scripts/skydio_mock.py
───────────────────────
Local mock server for the Skydio Cloud API.

Implements the four endpoints used by SkydioProducer so you can run
a complete Phase 3 demo without a Skydio Cloud subscription.

Usage:
    # Terminal 1 — start the mock
    python scripts/skydio_mock.py

    # Terminal 2 — run the demo against both mocks
    python demo.py --env .env.phase3 --phase 3

Endpoints implemented:
    GET  /api/v0/vehicle
    POST /api/v0/order
    GET  /api/v0/order/{order_id}
    GET  /api/v0/media

Order progression:
    The mock advances order status on each GET /order/{id} poll:
      created → in_transit → flying (50%) → flying (85%) → completed (100%)
    This mimics the real Skydio Cloud polling pattern.
"""

from __future__ import annotations

import time
import uuid
import argparse
import logging
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger("skydio-mock")

app = FastAPI(title="Skydio Cloud API Mock", version="1.0.0")

# ── In-memory state ───────────────────────────────────────────────────────────

_orders: Dict[str, Dict[str, Any]] = {}

# Simulated fleet — always one vehicle available
_FLEET = [
    {
        "serial":    "SIM-X2-001",
        "model":     "Skydio X2",
        "available": True,
        "location":  "Depot A",
    }
]


def _check_auth(authorization: Optional[str]) -> None:
    """Accept any non-empty Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")


def _next_status(order: Dict[str, Any]) -> Dict[str, Any]:
    """Advance order one step along the lifecycle on each poll."""
    current  = order.get("status", "created")
    progress = order.get("progress_percent", 0.0)

    if current == "created":
        order.update(status="in_transit", progress_percent=10.0,
                     status_message="Vehicle dispatched to site")
    elif current == "in_transit":
        order.update(status="flying", progress_percent=50.0,
                     status_message="Autonomous patrol in progress")
    elif current == "flying" and progress < 85:
        order.update(progress_percent=85.0,
                     status_message="Completing final sweep")
    elif current == "flying":
        order.update(status="completed", progress_percent=100.0,
                     status_message="Mission complete — video secured")
    # Once completed, stay completed
    return order


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/v0/vehicle")
def list_vehicles(
    available: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
):
    _check_auth(authorization)
    fleet = _FLEET if available != "true" else [v for v in _FLEET if v["available"]]
    logger.info("GET /vehicle?available=%s → 200  %d vehicle(s)", available, len(fleet))
    return {"vehicles": fleet}


@app.post("/api/v0/order")
async def create_order(request: Request, authorization: Optional[str] = Header(default=None)):
    _check_auth(authorization)
    body = await request.json()
    order_id = f"ord-{uuid.uuid4().hex[:8]}"
    order = {
        "order_id":       order_id,
        "external_id":    body.get("external_id", ""),
        "mission_type":   body.get("mission_type", "surveillance"),
        "status":         "created",
        "progress_percent": 0.0,
        "status_message": "Order received — assigning vehicle",
        "vehicle_serial": _FLEET[0]["serial"],
        "created_at":     time.time(),
        # Stored separately — not returned in status polls
        "_media_url": f"https://media.skydio.com/mock/{order_id}/flight.mp4",
    }
    _orders[order_id] = order
    logger.info("POST /order → 201  id=%s", order_id)
    return JSONResponse(status_code=201, content={k: v for k, v in order.items() if not k.startswith("_")})


@app.get("/api/v0/order/{order_id}")
def get_order(order_id: str, authorization: Optional[str] = Header(default=None)):
    _check_auth(authorization)
    if order_id not in _orders:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    order = _next_status(_orders[order_id])
    logger.info(
        "GET /order/%s → 200  status=%s  progress=%.0f%%",
        order_id, order["status"], order["progress_percent"],
    )
    return {k: v for k, v in order.items() if not k.startswith("_")}


@app.get("/api/v0/media")
def list_media(
    order_id: str = Query(...),
    type: str = Query("video"),
    authorization: Optional[str] = Header(default=None),
):
    _check_auth(authorization)
    order = _orders.get(order_id)
    media: List[Dict[str, Any]] = []
    if order and order.get("status") == "completed":
        media.append({
            "id":           f"med-{order_id}",
            "order_id":     order_id,
            "type":         type,
            "download_url": order["_media_url"],
            "duration_s":   order.get("progress_percent", 0) * 3.6,
            "created_at":   order["created_at"],
        })
    logger.info("GET /media?order_id=%s → 200  %d item(s)", order_id, len(media))
    return {"media": media}


@app.get("/health")
def health():
    return {"status": "ok", "service": "skydio-mock"}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Skydio Cloud API Mock Server")
    parser.add_argument("--port", type=int, default=8082, help="Port to listen on (default: 8082)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    args = parser.parse_args()

    print()
    print("  Skydio Cloud API Mock Server")
    print(f"  Listening on http://{args.host}:{args.port}")
    print()
    print("  Set in your env before running demo.py:")
    print(f"    SKYDIO_API_URL=http://{args.host}:{args.port}/api/v0")
    print()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
