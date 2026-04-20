"""
scripts/dronedeploy_mock.py
────────────────────────────
Local mock server for the DroneDeploy REST API.

Implements the five endpoints used by DroneDeployProducer so you can run
a complete Phase 3 demo without a paid DroneDeploy subscription.

Usage:
    # Terminal 1 — start the mock
    python scripts/dronedeploy_mock.py

    # Terminal 2 — run the demo against the mock
    DRONEDEPLOY_API_URL=http://localhost:8081/external/v1 \\
    python demo.py --env .env.phase3 --phase 3

Endpoints implemented:
    GET  /external/v1/user
    POST /external/v1/projects
    POST /external/v1/projects/{project_id}/missions
    GET  /external/v1/missions/{mission_id}
    GET  /external/v1/exports

Mission progression:
    The mock advances mission status on each GET /missions/{id} poll:
      PENDING → processing (25%) → processing (60%) → complete (100%)
    This mimics the real DroneDeploy webhook-less polling pattern.
"""

from __future__ import annotations

import time
import uuid
import argparse
import logging
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger("dd-mock")

app = FastAPI(title="DroneDeploy API Mock", version="1.0.0")

# ── In-memory state ───────────────────────────────────────────────────────────

_projects:  Dict[str, Dict[str, Any]] = {}
_missions:  Dict[str, Dict[str, Any]] = {}


def _check_auth(authorization: Optional[str]) -> None:
    """Accept any non-empty Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")


def _next_status(mission: Dict[str, Any]) -> Dict[str, Any]:
    """Advance mission one step along the lifecycle on each poll."""
    current = mission.get("status", "pending")
    progress = mission.get("progress", 0.0)

    if current == "pending":
        mission.update(status="processing", progress=25.0, status_note="Uploading flight plan")
    elif current == "processing" and progress < 60:
        mission.update(progress=60.0, status_note="Drone in transit to site")
    elif current == "processing" and progress < 100:
        mission.update(progress=100.0, status="complete", status_note="Mission complete — processing imagery")
    # Once complete, stay complete
    return mission


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/external/v1/user")
def get_user(authorization: Optional[str] = Header(default=None)):
    _check_auth(authorization)
    logger.info("GET /user → 200")
    return {
        "id":    "mock-user-001",
        "email": "demo@blackriver.local",
        "plan":  "enterprise",
        "name":  "Black River Demo",
    }


@app.post("/external/v1/projects")
async def create_project(request: Request, authorization: Optional[str] = Header(default=None)):
    _check_auth(authorization)
    body = await request.json()
    project_id = f"proj-{uuid.uuid4().hex[:8]}"
    project = {
        "id":          project_id,
        "name":        body.get("name", "Unnamed"),
        "description": body.get("description", ""),
        "created_at":  time.time(),
    }
    _projects[project_id] = project
    logger.info("POST /projects → 201  id=%s", project_id)
    return JSONResponse(status_code=201, content=project)


@app.post("/external/v1/projects/{project_id}/missions")
async def create_mission(
    project_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    _check_auth(authorization)
    if project_id not in _projects:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    body = await request.json()
    mission_id = f"msn-{uuid.uuid4().hex[:8]}"
    mission = {
        "id":         mission_id,
        "project_id": project_id,
        "name":       body.get("name", "Unnamed Mission"),
        "award_id":   body.get("award_id", ""),
        "status":     "pending",
        "progress":   0.0,
        "status_note":"Awaiting flight crew assignment",
        "created_at": time.time(),
        # Store mock export URL for later
        "_export_url": f"https://storage.dronedeploy.com/mock/{mission_id}/report.zip",
    }
    _missions[mission_id] = mission
    logger.info("POST /projects/%s/missions → 201  id=%s", project_id, mission_id)
    return JSONResponse(status_code=201, content={k: v for k, v in mission.items() if not k.startswith("_")})


@app.get("/external/v1/missions/{mission_id}")
def get_mission(mission_id: str, authorization: Optional[str] = Header(default=None)):
    _check_auth(authorization)
    if mission_id not in _missions:
        raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found")
    mission = _next_status(_missions[mission_id])
    logger.info(
        "GET /missions/%s → 200  status=%s  progress=%.0f%%",
        mission_id, mission["status"], mission["progress"],
    )
    return {k: v for k, v in mission.items() if not k.startswith("_")}


@app.get("/external/v1/exports")
def list_exports(
    project_id: str = Query(...),
    status: str = Query("complete"),
    authorization: Optional[str] = Header(default=None),
):
    _check_auth(authorization)
    # Find missions belonging to this project that are complete
    exports = []
    for msn in _missions.values():
        if msn.get("project_id") == project_id and msn.get("status") == "complete":
            exports.append({
                "id":           f"exp-{msn['id']}",
                "mission_id":   msn["id"],
                "project_id":   project_id,
                "status":       "complete",
                "download_url": msn["_export_url"],
                "type":         "orthomosaic",
                "created_at":   msn["created_at"],
            })
    logger.info("GET /exports?project_id=%s → 200  %d export(s)", project_id, len(exports))
    return {"data": exports}


@app.get("/health")
def health():
    return {"status": "ok", "service": "dronedeploy-mock"}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DroneDeploy API Mock Server")
    parser.add_argument("--port", type=int, default=8081, help="Port to listen on (default: 8081)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    args = parser.parse_args()

    print()
    print("  DroneDeploy API Mock Server")
    print(f"  Listening on http://{args.host}:{args.port}")
    print()
    print("  Set in your env before running demo.py:")
    print(f"    DRONEDEPLOY_API_URL=http://{args.host}:{args.port}/external/v1")
    print()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
