"""
phase2_checklist.py
────────────────────
Phase 2 exit checklist for Project Black River.
Covers all Phase 1 checks plus Phase 2 additions:
  - Tempo testnet deploy script
  - MPP broker server
  - MPP consumer client
  - Phase 2 dependencies in requirements.txt

Run from anywhere:
    python /sessions/inspiring-intelligent-archimedes/phase2_checklist.py
"""

import importlib
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path("/sessions/inspiring-intelligent-archimedes/mnt/outputs/black-river")
sys.path.insert(0, str(ROOT))

# Load .env.local if present
env_local = ROOT / ".env.local"
if env_local.exists():
    for line in env_local.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

PASS = "✓"
FAIL = "✗"
results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    symbol = PASS if ok else FAIL
    print(f"  {symbol}  {name[:52]:<52}  {detail[:60]}" if detail else f"  {symbol}  {name}")


print()
print("  Phase 1 checks")
print("  " + "─" * 68)

# ── Phase 1: Contract source files ───────────────────────────────────────────
for contract in ["MockUSDC", "AuditLog", "BlackRiverBroker"]:
    p = ROOT / "contracts" / f"{contract}.sol"
    check(f"Contract source: {contract}.sol", p.exists())

# ── Phase 1: Compiled artifacts ──────────────────────────────────────────────
for contract in ["MockUSDC", "AuditLog", "BlackRiverBroker"]:
    p = ROOT / "artifacts" / "contracts" / f"{contract}.sol" / f"{contract}.json"
    check(f"Compiled artifact: {contract}.json", p.exists(),
          "run compile_local.js" if not p.exists() else "")

# ── Phase 1: ProducerAdapter ABC ─────────────────────────────────────────────
try:
    from agents.interfaces.producer_adapter import ProducerAdapter
    abstract_methods = set(ProducerAdapter.__abstractmethods__) if hasattr(ProducerAdapter, "__abstractmethods__") else set()
    required = {"can_fulfil", "submit_bid", "accept_award", "get_status", "confirm_delivery"}
    ok = required.issubset(abstract_methods)
    check("ProducerAdapter ABC has 5 abstract methods", ok,
          f"missing: {required - abstract_methods}" if not ok else "")
except Exception as e:
    check("ProducerAdapter ABC importable", False, str(e))

# ── Phase 1: Producers satisfy ABC ───────────────────────────────────────────
for mod_name, cls_name in [
    ("agents.producers.drone_ops_producer",     "DroneOpsSoftwareProducer"),
    ("agents.producers.drone_service_producer", "DroneServiceProducer"),
]:
    try:
        mod = importlib.import_module(mod_name)
        cls = getattr(mod, cls_name)
        cls()
        check(f"ABC satisfied: {cls_name}", True)
    except Exception as e:
        check(f"ABC satisfied: {cls_name}", False, str(e))

# ── Phase 1: CNPStage completeness ───────────────────────────────────────────
try:
    from shared.cnp_messages import CNPStage
    required_stages = {"ANNOUNCED","BIDDING","EVALUATING","AWARDED","EXECUTING","DELIVERED","SETTLED","FAILED"}
    actual = {s.name for s in CNPStage}
    ok = required_stages.issubset(actual)
    check("CNPStage has all 8 required stages", ok,
          f"missing: {required_stages - actual}" if not ok else "")
except Exception as e:
    check("CNPStage importable", False, str(e))

# ── Phase 1: ChainClient methods ─────────────────────────────────────────────
try:
    from agents.broker.chain_client import ChainClient
    required_methods = {"log_event","record_award","confirm_delivery","settle_payment","cancel_contract"}
    actual = {m for m in dir(ChainClient) if not m.startswith("_")}
    ok = required_methods.issubset(actual)
    check("ChainClient has all required methods", ok,
          f"missing: {required_methods - actual}" if not ok else "")
except Exception as e:
    check("ChainClient importable", False, str(e))

# ── Phase 1: BrokerAgent dry_run param ───────────────────────────────────────
try:
    from agents.broker.broker_graph import BrokerAgent
    sig = inspect.signature(BrokerAgent.__init__)
    ok = "dry_run" in sig.parameters
    check("BrokerAgent.__init__ has dry_run param", ok)
    # Phase 2: mpp param must be gone or optional
    mpp_param = sig.parameters.get("mpp")
    mpp_ok = mpp_param is None or mpp_param.default is not inspect.Parameter.empty
    check("BrokerAgent.mpp param removed or optional", mpp_ok,
          "mpp is still a required param" if not mpp_ok else "")
except Exception as e:
    check("BrokerAgent importable", False, str(e))

# ── Phase 1: Key files present ───────────────────────────────────────────────
for fname in ["scripts/deploy_local.js", "requirements.txt", "demo.py"]:
    check(f"{fname} present", (ROOT / fname).exists())

# ── Phase 1: dry-run smoke test ──────────────────────────────────────────────
try:
    result = subprocess.run(
        [sys.executable, "demo.py", "--dry-run"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    ok = result.returncode == 0 and "SETTLED" in (result.stdout + result.stderr)
    check("demo.py --dry-run reaches SETTLED", ok,
          result.stderr[-200:].replace("\n"," ") if not ok else "")
except subprocess.TimeoutExpired:
    check("demo.py --dry-run reaches SETTLED", False, "TIMEOUT")
except Exception as e:
    check("demo.py --dry-run reaches SETTLED", False, str(e))

# ── Phase 1: Contract tests ───────────────────────────────────────────────────
try:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "test/test_contracts.py", "-q", "--tb=no"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    out = result.stdout + result.stderr
    m = re.search(r"(\d+) passed", out)
    n_passed = int(m.group(1)) if m else 0
    ok = result.returncode == 0 and n_passed == 26
    check(f"pytest test_contracts.py: 26 tests pass", ok, f"{n_passed} passed")
except Exception as e:
    check("pytest test_contracts.py", False, str(e))

# ── Phase 1: Producer wallet addresses ───────────────────────────────────────
def extract_wallet(filename: str, env_key: str) -> str:
    val = os.environ.get(env_key, "")
    if val and re.fullmatch(r"0x[0-9a-fA-F]{40}", val):
        return val
    txt = (ROOT / "agents" / "producers" / filename).read_text()
    m = re.search(r'_WALLET\s*=\s*os\.getenv\([^,]+,\s*"(0x[0-9a-fA-F]{40})"\)', txt)
    if m: return m.group(1)
    m2 = re.search(r'_WALLET\s*=\s*"(0x[0-9a-fA-F]{40})"', txt)
    return m2.group(1) if m2 else ""

for fname, env_key in [
    ("drone_ops_producer.py",     "PRODUCER_AEROPLAN_WALLET"),
    ("drone_service_producer.py", "PRODUCER_SKYWATCH_WALLET"),
]:
    wallet = extract_wallet(fname, env_key)
    ok = bool(wallet and re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet))
    check(f"Producer wallet valid hex ({fname[:30]}…)", ok,
          wallet[:44] if ok else f"got: '{wallet[:44]}'")

# ── Phase 1: ABI structure ────────────────────────────────────────────────────
try:
    art = json.loads((ROOT / "artifacts/contracts/AuditLog.sol/AuditLog.json").read_text())
    events = {e["name"]: e for e in art["abi"] if e["type"] == "event"}
    fields = {inp["name"] for inp in events.get("EventLogged", {}).get("inputs", [])}
    ok = {"stage", "actor", "payloadHash"}.issubset(fields)
    check("AuditLog.EventLogged has required fields", ok)
except Exception as e:
    check("AuditLog ABI readable", False, str(e))

try:
    art = json.loads((ROOT / "artifacts/contracts/BlackRiverBroker.sol/BlackRiverBroker.json").read_text())
    fns = {e["name"] for e in art["abi"] if e["type"] == "function"}
    required_fns = {"createContract","recordAward","confirmDelivery","settlePayment","cancelContract"}
    ok = required_fns.issubset(fns)
    check("BlackRiverBroker ABI has required functions", ok,
          f"missing: {required_fns - fns}" if not ok else "")
except Exception as e:
    check("BlackRiverBroker ABI readable", False, str(e))

print()
print("  Phase 2 checks")
print("  " + "─" * 68)

# ── Phase 2: MPP simulator removed from broker_graph ─────────────────────────
try:
    broker_src = (ROOT / "agents/broker/broker_graph.py").read_text()
    ok = "from agents.mpp.mpp_simulator import" not in broker_src
    check("MPPSimulator removed from broker_graph.py", ok,
          "still imported" if not ok else "")
except Exception as e:
    check("broker_graph.py readable", False, str(e))

# ── Phase 2: Tempo testnet deploy scripts ────────────────────────────────────
p = ROOT / "scripts" / "deploy_tempo_testnet.js"
check("scripts/deploy_tempo_testnet.js present", p.exists())
p_sh = ROOT / "scripts" / "deploy_tempo_testnet.sh"
check("scripts/deploy_tempo_testnet.sh present", p_sh.exists())

# ── Phase 2: Tempo testnet env example ───────────────────────────────────────
p = ROOT / ".env.tempo_testnet.example"
check(".env.tempo_testnet.example present", p.exists())
if p.exists():
    content = p.read_text()
    ok = "TEMPO_TESTNET_RPC" in content and ("42431" in content or "42429" in content)
    check(".env.tempo_testnet.example has RPC + chain ID", ok)

# ── Phase 2: MPP broker server ───────────────────────────────────────────────
p = ROOT / "agents/mpp/broker_server.py"
check("agents/mpp/broker_server.py present", p.exists())
if p.exists():
    content = p.read_text()
    checks = {
        "FastAPI app": "FastAPI(" in content,
        "/procure endpoint": '"/procure"' in content or "'/procure'" in content,
        "pympp integration": "from mpp" in content or "import mpp" in content,
        "health endpoint": '"/health"' in content or "'/health'" in content,
    }
    for label, ok in checks.items():
        check(f"  broker_server: {label}", ok)

# ── Phase 2: MPP consumer client ─────────────────────────────────────────────
p = ROOT / "agents/consumer/mpp_consumer.py"
check("agents/consumer/mpp_consumer.py present", p.exists())
if p.exists():
    content = p.read_text()
    checks = {
        "pympp Client": "mpp.client" in content or "from mpp" in content,
        "402 handling": "run_mpp" in content or "Client(" in content,
        "dry-run ungated path": "run_ungated" in content or "dry_run" in content,
    }
    for label, ok in checks.items():
        check(f"  mpp_consumer: {label}", ok)

# ── Phase 2: requirements.txt has Phase 2 deps ───────────────────────────────
try:
    req_txt = (ROOT / "requirements.txt").read_text()
    for pkg in ["fastapi", "uvicorn", "httpx", "pympp"]:
        check(f"requirements.txt has {pkg}", pkg in req_txt)
except Exception as e:
    check("requirements.txt readable", False, str(e))

# ── Phase 2: demo.py supports --env flag ─────────────────────────────────────
try:
    demo_src = (ROOT / "demo.py").read_text()
    ok = "--env" in demo_src
    check("demo.py supports --env flag", ok)
    ok2 = "TEMPO_TESTNET_RPC" in demo_src or "tempo" in demo_src.lower()
    check("demo.py is Tempo-aware", ok2)
except Exception as e:
    check("demo.py readable", False, str(e))

# ── Phase 2: deploy script is Tempo-specific (not Base Sepolia) ──────────────
try:
    script = (ROOT / "scripts/deploy_tempo_testnet.js").read_text()
    ok = ("42429" in script or "42431" in script) and "tempo.xyz" in script
    check("deploy_tempo_testnet.js targets Tempo chain", ok)
    check("deploy script targets Tempo (not Base Sepolia)", "testnet.tempo.xyz" in script)
except Exception as e:
    check("deploy_tempo_testnet.js readable", False, str(e))

# ── Phase 2: shell deploy script is Tempo-specific ───────────────────────────
try:
    sh = (ROOT / "scripts/deploy_tempo_testnet.sh").read_text()
    ok = "42431" in sh and "tempo.xyz" in sh
    check("deploy_tempo_testnet.sh targets Tempo chain 42431", ok)
    ok2 = "--tempo.fee-token" in sh or "fee-token" in sh
    check("deploy_tempo_testnet.sh uses tempo fee-token flag", ok2)
except Exception as e:
    check("deploy_tempo_testnet.sh readable", False, str(e))

# ── Phase 2: pytempo in requirements.txt ─────────────────────────────────────
try:
    req_txt = (ROOT / "requirements.txt").read_text()
    check("requirements.txt has pytempo", "pytempo" in req_txt)
except Exception as e:
    check("requirements.txt readable (pytempo check)", False, str(e))

# ── Summary ───────────────────────────────────────────────────────────────────
print()
total   = len(results)
passing = sum(1 for _, ok, _ in results if ok)
failing = total - passing

print("─" * 72)
print(f"  Phase 2 Exit Checklist:  {passing}/{total} passing")
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
    print("\n  🎉  All checks pass — Phase 2 complete!\n")
    sys.exit(0)
