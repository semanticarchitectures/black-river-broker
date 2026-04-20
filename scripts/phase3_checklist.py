"""
phase3_checklist.py
────────────────────
Phase 3 exit checklist for Project Black River.
Verifies all Phase 2 checks still pass, plus Phase 3 additions:
  - HttpProducerBase infrastructure
  - DroneDeployProducer (real DroneDeploy API)
  - SkydioProducer (real Skydio Cloud API)
  - Phase 3 dependencies in requirements.txt
  - Phase 3 test suite passing
  - demo.py --phase 3 flag present
  - .env.phase3.example present and well-formed

Run from anywhere:
    python scripts/phase3_checklist.py
"""

import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env.local if present so imports work
env_local = ROOT / ".env.local"
if env_local.exists():
    for line in env_local.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# Stub out partner API keys so imports don't fail
os.environ.setdefault("DRONEDEPLOY_API_KEY", "checklist-stub-key")
os.environ.setdefault("SKYDIO_API_TOKEN",    "checklist-stub-token")

PASS = "✓"
FAIL = "✗"
results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    symbol = PASS if ok else FAIL
    line = f"  {symbol}  {name[:52]:<52}  {detail[:60]}" if detail else f"  {symbol}  {name}"
    print(line)


# ── Phase 2 regression: key files and imports still work ─────────────────────

print()
print("  Phase 2 regression checks")
print("  " + "─" * 68)

for fname in [
    "agents/interfaces/producer_adapter.py",
    "agents/broker/broker_graph.py",
    "agents/broker/chain_client.py",
    "agents/mpp/broker_server.py",
    "agents/consumer/mpp_consumer.py",
    "scripts/deploy_tempo_testnet.js",
    "scripts/deploy_tempo_testnet.sh",
    ".env.tempo_testnet.example",
]:
    check(f"{fname} present", (ROOT / fname).exists())

try:
    from shared.cnp_messages import CNPStage, ServiceType, ProducerBid, ContractAward
    check("shared.cnp_messages importable", True)
except Exception as e:
    check("shared.cnp_messages importable", False, str(e))

try:
    from agents.interfaces.producer_adapter import ProducerAdapter
    required = {"can_fulfil", "submit_bid", "accept_award", "get_status", "confirm_delivery"}
    ok = required.issubset(ProducerAdapter.__abstractmethods__)
    check("ProducerAdapter ABC has 5 abstract methods", ok)
except Exception as e:
    check("ProducerAdapter ABC importable", False, str(e))

try:
    result = subprocess.run(
        [sys.executable, "demo.py", "--dry-run"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    ok = result.returncode == 0 and "SETTLED" in (result.stdout + result.stderr)
    check("demo.py --dry-run (Phase 2) reaches SETTLED", ok,
          result.stderr[-200:].replace("\n", " ") if not ok else "")
except subprocess.TimeoutExpired:
    check("demo.py --dry-run reaches SETTLED", False, "TIMEOUT")
except Exception as e:
    check("demo.py --dry-run reaches SETTLED", False, str(e))

# ── Phase 3: new files ────────────────────────────────────────────────────────

print()
print("  Phase 3 checks")
print("  " + "─" * 68)

for fname in [
    "agents/producers/http_producer_base.py",
    "agents/producers/dronedeploy_producer.py",
    "agents/producers/skydio_producer.py",
    "test/test_phase3_adapters.py",
    ".env.phase3.example",
]:
    check(f"{fname} present", (ROOT / fname).exists())

# ── Phase 3: HttpProducerBase ─────────────────────────────────────────────────

try:
    from agents.producers.http_producer_base import (
        HttpProducerBase, ProducerApiError, ProducerRateLimitError,
        ProducerUnavailableError, ProducerAuthError,
    )
    check("HttpProducerBase importable", True)
    check("ProducerApiError hierarchy present",
          issubclass(ProducerRateLimitError, ProducerApiError) and
          issubclass(ProducerUnavailableError, ProducerApiError) and
          issubclass(ProducerAuthError, ProducerApiError))
    check("HttpProducerBase subclasses ProducerAdapter",
          issubclass(HttpProducerBase, ProducerAdapter))
except Exception as e:
    check("HttpProducerBase importable", False, str(e))

# ── Phase 3: DroneDeployProducer ──────────────────────────────────────────────

try:
    from agents.producers.dronedeploy_producer import DroneDeployProducer
    p = DroneDeployProducer()
    check("DroneDeployProducer instantiates", True)
    check("DroneDeployProducer satisfies ProducerAdapter ABC",
          not bool(DroneDeployProducer.__abstractmethods__))
    check("DroneDeployProducer.PRODUCER_ID set",
          bool(DroneDeployProducer.PRODUCER_ID) and DroneDeployProducer.PRODUCER_ID != "NotImplemented")
    check("DroneDeployProducer._base_url targets dronedeploy.com",
          "dronedeploy.com" in DroneDeployProducer._base_url)
except Exception as e:
    check("DroneDeployProducer importable", False, str(e))

# ── Phase 3: SkydioProducer ───────────────────────────────────────────────────

try:
    from agents.producers.skydio_producer import SkydioProducer
    p2 = SkydioProducer()
    check("SkydioProducer instantiates", True)
    check("SkydioProducer satisfies ProducerAdapter ABC",
          not bool(SkydioProducer.__abstractmethods__))
    check("SkydioProducer.PRODUCER_ID set",
          bool(SkydioProducer.PRODUCER_ID) and SkydioProducer.PRODUCER_ID != "NotImplemented")
    check("SkydioProducer._base_url targets skydio.com",
          "skydio.com" in SkydioProducer._base_url)
except Exception as e:
    check("SkydioProducer importable", False, str(e))

# ── Phase 3: producers/__init__.py registry ───────────────────────────────────

try:
    import agents.producers as producers_pkg
    ok_dd = hasattr(producers_pkg, "DroneDeployProducer")
    ok_sk = hasattr(producers_pkg, "SkydioProducer")
    check("DroneDeployProducer in producers __init__", ok_dd)
    check("SkydioProducer in producers __init__", ok_sk)
except Exception as e:
    check("producers __init__ importable", False, str(e))

# ── Phase 3: requirements.txt ─────────────────────────────────────────────────

try:
    req = (ROOT / "requirements.txt").read_text()
    check("requirements.txt has tenacity",  "tenacity" in req)
    check("requirements.txt has respx",     "respx" in req)
except Exception as e:
    check("requirements.txt readable", False, str(e))

# ── Phase 3: demo.py --phase flag ────────────────────────────────────────────

try:
    demo_src = (ROOT / "demo.py").read_text()
    check("demo.py has --phase flag",       "--phase" in demo_src)
    check("demo.py imports DroneDeployProducer", "DroneDeployProducer" in demo_src)
    check("demo.py imports SkydioProducer",  "SkydioProducer" in demo_src)
except Exception as e:
    check("demo.py readable", False, str(e))

# ── Phase 3: .env.phase3.example ─────────────────────────────────────────────

try:
    example = (ROOT / ".env.phase3.example").read_text()
    check(".env.phase3.example has DRONEDEPLOY_API_KEY",       "DRONEDEPLOY_API_KEY" in example)
    check(".env.phase3.example has SKYDIO_API_TOKEN",           "SKYDIO_API_TOKEN" in example)
    check(".env.phase3.example has PRODUCER_DRONEDEPLOY_WALLET","PRODUCER_DRONEDEPLOY_WALLET" in example)
    check(".env.phase3.example has PRODUCER_SKYDIO_WALLET",     "PRODUCER_SKYDIO_WALLET" in example)
except Exception as e:
    check(".env.phase3.example readable", False, str(e))

# ── Phase 3: test suite passes ────────────────────────────────────────────────

try:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "test/test_phase3_adapters.py", "-q", "--tb=short"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    out = result.stdout + result.stderr
    m = re.search(r"(\d+) passed", out)
    n_passed = int(m.group(1)) if m else 0
    ok = result.returncode == 0
    check(f"pytest test_phase3_adapters.py: {n_passed} tests pass", ok,
          out[-300:].replace("\n", " ") if not ok else f"{n_passed} passed")
except subprocess.TimeoutExpired:
    check("pytest test_phase3_adapters.py", False, "TIMEOUT")
except Exception as e:
    check("pytest test_phase3_adapters.py", False, str(e))

# ── Phase 3: dry-run with --phase 3 reaches SETTLED ─────────────────────────

try:
    result = subprocess.run(
        [sys.executable, "demo.py", "--dry-run", "--phase", "3"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    ok = result.returncode == 0 and "SETTLED" in (result.stdout + result.stderr)
    check("demo.py --dry-run --phase 3 reaches SETTLED", ok,
          result.stderr[-300:].replace("\n", " ") if not ok else "")
except subprocess.TimeoutExpired:
    check("demo.py --dry-run --phase 3 reaches SETTLED", False, "TIMEOUT")
except Exception as e:
    check("demo.py --dry-run --phase 3 reaches SETTLED", False, str(e))

# ── Summary ───────────────────────────────────────────────────────────────────

print()
total   = len(results)
passing = sum(1 for _, ok, _ in results if ok)
failing = total - passing

print("─" * 72)
print(f"  Phase 3 Exit Checklist:  {passing}/{total} passing")
print("─" * 72)

if failing:
    print(f"\n  ⚠️   {failing} check(s) failed.\n")
    for name, ok, detail in results:
        if not ok:
            print(f"       ✗  {name}")
            if detail:
                print(f"            {detail}")
    print()
    sys.exit(1)
else:
    print("\n  🎉  All checks pass — Phase 3 complete!\n")
    sys.exit(0)
