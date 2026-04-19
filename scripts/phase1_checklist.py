"""
phase1_checklist.py
────────────────────
Phase 1 exit checklist for Project Black River.
Verifies 21 conditions that must hold before Phase 1 is declared done.

Run from the black-river project root:
    python /sessions/inspiring-intelligent-archimedes/phase1_checklist.py
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

# ── Load .env.local if present (deploy script output) ────────────────────────
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


# ── 1. Contract source files present ─────────────────────────────────────────
for contract in ["MockUSDC", "AuditLog", "BlackRiverBroker"]:
    p = ROOT / "contracts" / f"{contract}.sol"
    check(f"Contract source: {contract}.sol", p.exists(), str(p) if not p.exists() else "")

# ── 2. Compiled artifacts present ────────────────────────────────────────────
for contract in ["MockUSDC", "AuditLog", "BlackRiverBroker"]:
    p = ROOT / "artifacts" / "contracts" / f"{contract}.sol" / f"{contract}.json"
    check(f"Compiled artifact: {contract}.json", p.exists(), "run compile_local.js" if not p.exists() else "")

# ── 3. ProducerAdapter ABC is abstract ───────────────────────────────────────
try:
    from agents.interfaces.producer_adapter import ProducerAdapter
    abstract_methods = set(ProducerAdapter.__abstractmethods__) if hasattr(ProducerAdapter, "__abstractmethods__") else set()
    required = {"can_fulfil", "submit_bid", "accept_award", "get_status", "confirm_delivery"}
    ok = required.issubset(abstract_methods)
    check("ProducerAdapter ABC has 5 abstract methods", ok,
          f"missing: {required - abstract_methods}" if not ok else "")
except Exception as e:
    check("ProducerAdapter ABC importable", False, str(e))

# ── 4. Producer implementations satisfy the ABC ───────────────────────────────
producer_files = [
    ("agents.producers.drone_ops_producer",     "DroneOpsSoftwareProducer"),
    ("agents.producers.drone_service_producer", "DroneServiceProducer"),
]
for mod_name, cls_name in producer_files:
    try:
        mod = importlib.import_module(mod_name)
        cls = getattr(mod, cls_name)
        # Instantiating a concrete class with no missing abstractmethods means it satisfies the ABC
        instance = cls()
        check(f"ABC satisfied: {cls_name}", True)
    except TypeError as e:
        check(f"ABC satisfied: {cls_name}", False, str(e))
    except Exception as e:
        check(f"ABC satisfied: {cls_name}", False, str(e))

# ── 5. Shared CNP messages have all required stages ───────────────────────────
try:
    from shared.cnp_messages import CNPStage
    required_stages = {"ANNOUNCED", "BIDDING", "EVALUATING", "AWARDED",
                       "EXECUTING", "DELIVERED", "SETTLED", "FAILED"}
    actual_stages = {s.name for s in CNPStage}
    ok = required_stages.issubset(actual_stages)
    check("CNPStage has all 8 required stages", ok,
          f"missing: {required_stages - actual_stages}" if not ok else "")
except Exception as e:
    check("CNPStage importable", False, str(e))

# ── 6. ChainClient has all required methods ───────────────────────────────────
try:
    from agents.broker.chain_client import ChainClient
    required_methods = {
        "log_event", "record_award", "confirm_delivery",
        "settle_payment", "cancel_contract",
    }
    actual = {m for m in dir(ChainClient) if not m.startswith("_")}
    ok = required_methods.issubset(actual)
    check("ChainClient has all required methods", ok,
          f"missing: {required_methods - actual}" if not ok else "")
except Exception as e:
    check("ChainClient importable", False, str(e))

# ── 7. BrokerAgent accepts dry_run param ─────────────────────────────────────
try:
    from agents.broker.broker_graph import BrokerAgent
    sig = inspect.signature(BrokerAgent.__init__)
    ok = "dry_run" in sig.parameters
    check("BrokerAgent.__init__ has dry_run param", ok)
except Exception as e:
    check("BrokerAgent importable", False, str(e))

# ── 8. deploy_local.js present ───────────────────────────────────────────────
p = ROOT / "scripts" / "deploy_local.js"
check("deploy_local.js present", p.exists())

# ── 9. requirements.txt present ──────────────────────────────────────────────
p = ROOT / "requirements.txt"
check("requirements.txt present", p.exists())

# ── 10. demo.py present ───────────────────────────────────────────────────────
p = ROOT / "demo.py"
check("demo.py present", p.exists())

# ── 11. dry-run smoke test ────────────────────────────────────────────────────
try:
    result = subprocess.run(
        [sys.executable, "demo.py", "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    ok = result.returncode == 0 and "SETTLED" in (result.stdout + result.stderr)
    check("demo.py --dry-run exits 0 and reaches SETTLED", ok,
          result.stderr[-200:].replace("\n", " ") if not ok else "")
except subprocess.TimeoutExpired:
    check("demo.py --dry-run exits 0 and reaches SETTLED", False, "TIMEOUT after 60s")
except Exception as e:
    check("demo.py --dry-run exits 0 and reaches SETTLED", False, str(e))

# ── 12. Contract test suite passes ────────────────────────────────────────────
try:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "test/test_contracts.py", "-q", "--tb=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # Look for "X passed" pattern
    out = result.stdout + result.stderr
    m = re.search(r"(\d+) passed", out)
    n_passed = int(m.group(1)) if m else 0
    ok = result.returncode == 0 and n_passed == 26
    check(f"pytest test_contracts.py: 26 tests pass", ok,
          f"{n_passed} passed" + (" " + result.stderr[-100:].replace("\n", " ") if not ok else ""))
except subprocess.TimeoutExpired:
    check("pytest test_contracts.py: 26 tests pass", False, "TIMEOUT after 120s")
except Exception as e:
    check("pytest test_contracts.py: 26 tests pass", False, str(e))

# ── 13. Producer wallet addresses are valid hex ───────────────────────────────
def extract_wallet_from_file(filename: str, env_key: str) -> str:
    """
    Read the wallet address from the producer file.
    First tries os.environ (loaded from .env.local above).
    Falls back to parsing the _WALLET = os.getenv("KEY", "0x...") default literal.
    """
    val = os.environ.get(env_key, "")
    if val and val.startswith("0x") and len(val) == 42:
        return val

    txt = (ROOT / "agents" / "producers" / filename).read_text()
    # Match:  _WALLET = os.getenv("SOME_KEY", "0xABCD...")
    m = re.search(r'_WALLET\s*=\s*os\.getenv\([^,]+,\s*"(0x[0-9a-fA-F]{40})"\)', txt)
    if m:
        return m.group(1)
    # Fallback: bare  _WALLET = "0x..."
    m2 = re.search(r'_WALLET\s*=\s*"(0x[0-9a-fA-F]{40})"', txt)
    if m2:
        return m2.group(1)
    return ""


for fname, env_key in [
    ("drone_ops_producer.py",     "PRODUCER_AEROPLAN_WALLET"),
    ("drone_service_producer.py", "PRODUCER_SKYWATCH_WALLET"),
]:
    wallet = extract_wallet_from_file(fname, env_key)
    ok = bool(wallet and re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet))
    check(f"Producer wallet valid hex ({fname[:30]}…)", ok,
          wallet[:44] if ok else f"got: '{wallet[:44]}'")

# ── 14. AuditLog event structure has required fields ─────────────────────────
try:
    art_path = ROOT / "artifacts" / "contracts" / "AuditLog.sol" / "AuditLog.json"
    art = json.loads(art_path.read_text())
    events = {e["name"]: e for e in art["abi"] if e["type"] == "event"}
    ok = "EventLogged" in events
    if ok:
        fields = {inp["name"] for inp in events["EventLogged"]["inputs"]}
        required_fields = {"stage", "actor", "payloadHash"}
        ok = required_fields.issubset(fields)
        check("AuditLog.EventLogged has required fields", ok,
              f"missing: {required_fields - fields}" if not ok else "")
    else:
        check("AuditLog.EventLogged event exists", False, "EventLogged not in ABI")
except Exception as e:
    check("AuditLog ABI readable", False, str(e))

# ── 15. BlackRiverBroker ABI has required functions ──────────────────────────
try:
    art_path = ROOT / "artifacts" / "contracts" / "BlackRiverBroker.sol" / "BlackRiverBroker.json"
    art = json.loads(art_path.read_text())
    fns = {e["name"] for e in art["abi"] if e["type"] == "function"}
    required_fns = {"createContract", "recordAward", "confirmDelivery", "settlePayment", "cancelContract"}
    ok = required_fns.issubset(fns)
    check("BlackRiverBroker ABI has required functions", ok,
          f"missing: {required_fns - fns}" if not ok else "")
except Exception as e:
    check("BlackRiverBroker ABI readable", False, str(e))

# ── Summary ───────────────────────────────────────────────────────────────────
print()
total   = len(results)
passing = sum(1 for _, ok, _ in results if ok)
failing = total - passing

print("─" * 72)
print(f"  Phase 1 Exit Checklist:  {passing}/{total} passing")
print("─" * 72)

if failing:
    print(f"\n  ⚠️   {failing} check(s) failed — address before shipping.\n")
    sys.exit(1)
else:
    print("\n  🎉  All checks pass — Phase 1 complete!\n")
    sys.exit(0)
