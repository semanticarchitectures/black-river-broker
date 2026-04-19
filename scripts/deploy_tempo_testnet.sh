#!/usr/bin/env bash
# scripts/deploy_tempo_testnet.sh
# Deploy Black River contracts to the Tempo Moderato testnet using
# tempo-foundry's `cast` — the only tool that supports Tempo's stablecoin
# fee token in transactions.
#
# Prerequisites
# ─────────────
#  1. Install tempo-foundry:
#       curl -L https://foundry.paradigm.xyz | bash
#       foundryup -n tempo
#
#  2. Copy .env.tempo_testnet.example → .env.tempo_testnet and fill in:
#       DEPLOYER_PRIVATE_KEY, BROKER_PRIVATE_KEY, CONSUMER_PRIVATE_KEY
#
#  3. Run from the project root:
#       bash scripts/deploy_tempo_testnet.sh
#
# Tempo Moderato Testnet
# ──────────────────────
#   RPC      : https://rpc.moderato.tempo.xyz
#   Chain ID : 42431
#   Explorer : https://explore.moderato.tempo.xyz
#   Faucet   : https://faucet.tempo.xyz
#   Gas token: AlphaUSD  0x20c0000000000000000000000000000000000001

set -euo pipefail

# ── Load env file ─────────────────────────────────────────────────────────────
ENV_FILE=".env.tempo_testnet"
if [ ! -f "$ENV_FILE" ]; then
  echo "✗  $ENV_FILE not found."
  echo "   Copy .env.tempo_testnet.example → .env.tempo_testnet and fill in your keys."
  exit 1
fi

# Parse the env file with Python — safer than `source` because it handles
# placeholder values that contain bash special characters (< > ( ) etc.)
# and works reliably on macOS bash 3.2.
eval "$(python3 - "$ENV_FILE" <<'PYEOF'
import sys, pathlib
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    s = line.strip()
    if not s or s.startswith('#') or '=' not in s:
        continue
    key, _, val = s.partition('=')
    key = key.strip()
    val = val.strip()
    # Single-quote the value so bash sees it as a literal string.
    # Escape any single-quotes that may be inside the value.
    val_safe = val.replace("'", "'\"'\"'")
    print(f"export {key}='{val_safe}'")
PYEOF
)"

# ── Defaults ──────────────────────────────────────────────────────────────────
RPC="${TEMPO_TESTNET_RPC:-https://rpc.moderato.tempo.xyz}"

# AlphaUSD — the default fee token on Tempo testnet
# (any TIP-20 stablecoin works; AlphaUSD is provided by the faucet)
FEE_TOKEN="0x20c0000000000000000000000000000000000001"

DEPLOYER_KEY="${DEPLOYER_PRIVATE_KEY:-}"
BROKER_KEY="${BROKER_PRIVATE_KEY:-$DEPLOYER_KEY}"
CONSUMER_KEY="${CONSUMER_PRIVATE_KEY:-}"

# ── Validate ──────────────────────────────────────────────────────────────────
if [ -z "$DEPLOYER_KEY" ] || [ "$DEPLOYER_KEY" = "0x<your-deployer-private-key>" ]; then
  echo "✗  DEPLOYER_PRIVATE_KEY not set in $ENV_FILE"
  exit 1
fi

if ! command -v cast &>/dev/null; then
  echo "✗  'cast' not found. Install tempo-foundry:"
  echo "     curl -L https://foundry.paradigm.xyz | bash"
  echo "     foundryup -n tempo"
  exit 1
fi

ARTIFACTS="artifacts/contracts"

# ── Helper: deploy a contract by embedding bytecode + ABI-encoded constructor args
deploy() {
  local name="$1"
  shift
  local json_path="$ARTIFACTS/$name.sol/$name.json"

  if [ ! -f "$json_path" ]; then
    echo "✗  Artifact not found: $json_path"
    echo "   Run: node scripts/compile_local.js"
    exit 1
  fi

  # Extract bytecode
  local bytecode
  bytecode=$(python3 -c "import json,sys; d=json.load(open('$json_path')); print(d['bytecode'])")

  # Append ABI-encoded constructor args if provided
  local calldata="$bytecode"
  if [ $# -gt 0 ]; then
    local encoded
    encoded=$(cast abi-encode "constructor($1)" "${@:2}")
    # Remove 0x prefix before appending
    calldata="${bytecode}${encoded:2}"
  fi

  local address
  address=$(cast send \
    --rpc-url "$RPC" \
    --private-key "$DEPLOYER_KEY" \
    --tempo.fee-token "$FEE_TOKEN" \
    --create "$calldata" \
    --json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('contractAddress',''))")

  if [ -z "$address" ]; then
    echo "  ✗  Deployment failed for $name"
    exit 1
  fi
  echo "$address"
}

# ── Helper: send a contract call ──────────────────────────────────────────────
send_tx() {
  local from_key="$1"
  local to="$2"
  local sig="$3"
  shift 3
  local out
  out=$(cast send \
    --rpc-url "$RPC" \
    --private-key "$from_key" \
    --tempo.fee-token "$FEE_TOKEN" \
    "$to" "$sig" "$@" \
    --json 2>&1)
  if ! echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); print('    ✓ tx ' + d.get('transactionHash','')[:20] + '…')" 2>/dev/null; then
    echo "  ✗  Transaction failed:"
    echo "$out" | head -5
    exit 1
  fi
}

# ── Helper: fund an address with AlphaUSD via testnet RPC ────────────────────
fund_alpha() {
  local addr="$1"
  local label="$2"
  local bal
  bal=$(cast call "$FEE_TOKEN" "balanceOf(address)(uint256)" "$addr" --rpc-url "$RPC" 2>/dev/null || echo "0")
  if [ "$bal" = "0" ]; then
    echo "  Funding $label ($addr) with AlphaUSD…"
    cast rpc tempo_fundAddress "$addr" --rpc-url "$RPC" >/dev/null 2>&1 || true
    bal=$(cast call "$FEE_TOKEN" "balanceOf(address)(uint256)" "$addr" --rpc-url "$RPC" 2>/dev/null || echo "0")
    echo "    AlphaUSD balance: $bal units"
  else
    echo "  $label AlphaUSD balance: $bal units ✓"
  fi
}

# ── Derive addresses from private keys ────────────────────────────────────────
DEPLOYER_ADDR=$(cast wallet address --private-key "$DEPLOYER_KEY")
BROKER_ADDR=$(cast wallet address --private-key "$BROKER_KEY")

echo ""
echo "  Tempo Moderato Testnet Deploy"
echo "  RPC      : $RPC"
echo "  Fee token: $FEE_TOKEN (AlphaUSD)"
echo ""
echo "  deployer : $DEPLOYER_ADDR"
echo "  broker   : $BROKER_ADDR"

CONSUMER_ADDR=""
if [ -n "$CONSUMER_KEY" ] && [ "$CONSUMER_KEY" != "0x<your-consumer-private-key>" ]; then
  CONSUMER_ADDR=$(cast wallet address --private-key "$CONSUMER_KEY")
  echo "  consumer : $CONSUMER_ADDR"
fi
echo ""

# ── Fund all wallets with AlphaUSD gas token ─────────────────────────────────
# Every wallet that signs a transaction needs AlphaUSD.
# tempo_fundAddress is a testnet-only RPC method — safe to call unconditionally.
echo "  Checking / funding AlphaUSD balances…"
fund_alpha "$DEPLOYER_ADDR" "deployer"
fund_alpha "$BROKER_ADDR"   "broker"
[ -n "$CONSUMER_ADDR" ] && fund_alpha "$CONSUMER_ADDR" "consumer"
echo ""

# Hard stop if deployer still has nothing (faucet may be down)
ALPHA_BAL=$(cast call "$FEE_TOKEN" "balanceOf(address)(uint256)" "$DEPLOYER_ADDR" --rpc-url "$RPC" 2>/dev/null || echo "0")
if [ "$ALPHA_BAL" = "0" ]; then
  echo "  ✗  Deployer has no AlphaUSD after funding attempt."
  echo "     Visit https://faucet.tempo.xyz or run:"
  echo "     cast rpc tempo_fundAddress $DEPLOYER_ADDR --rpc-url $RPC"
  exit 1
fi

# ── Deploy MockUSDC ───────────────────────────────────────────────────────────
echo "  Deploying MockUSDC…"
USDC_ADDR=$(deploy "MockUSDC" "address" "$DEPLOYER_ADDR")
echo "  MockUSDC          → $USDC_ADDR"

# ── Deploy AuditLog ───────────────────────────────────────────────────────────
echo "  Deploying AuditLog…"
AUDIT_ADDR=$(deploy "AuditLog")
echo "  AuditLog          → $AUDIT_ADDR"

# ── Deploy BlackRiverBroker ───────────────────────────────────────────────────
echo "  Deploying BlackRiverBroker…"
BROKER_CONTRACT=$(deploy "BlackRiverBroker" "address,address,address" "$USDC_ADDR" "$AUDIT_ADDR" "$BROKER_ADDR")
echo "  BlackRiverBroker  → $BROKER_CONTRACT"
echo ""

# ── Authorise AuditLog writers ────────────────────────────────────────────────
echo "  Authorising AuditLog writers…"
send_tx "$DEPLOYER_KEY" "$AUDIT_ADDR" "setAuthorised(address,bool)" "$BROKER_CONTRACT" true
send_tx "$DEPLOYER_KEY" "$AUDIT_ADDR" "setAuthorised(address,bool)" "$BROKER_ADDR" true
echo "  AuditLog authorised ✓"

# ── Mint MockUSDC to broker ───────────────────────────────────────────────────
MINT_100K="100000000000"   # 100,000 USDC with 6 decimals
echo ""
echo "  Minting MockUSDC to broker…"
send_tx "$DEPLOYER_KEY" "$USDC_ADDR" "mint(address,uint256)" "$BROKER_ADDR" "$MINT_100K"
echo "  Minted 100,000 MockUSDC to broker ✓"

# ── Broker approves broker contract ──────────────────────────────────────────
echo "  Setting USDC allowance…"
send_tx "$BROKER_KEY" "$USDC_ADDR" "approve(address,uint256)" "$BROKER_CONTRACT" "$MINT_100K"
echo "  USDC approval: broker → broker contract ✓"

# ── Mint MockUSDC to consumer (if configured) ─────────────────────────────────
MINT_10K="10000000000"   # 10,000 USDC with 6 decimals
if [ -n "$CONSUMER_ADDR" ]; then
  echo "  Minting MockUSDC to consumer…"
  send_tx "$DEPLOYER_KEY" "$USDC_ADDR" "mint(address,uint256)" "$CONSUMER_ADDR" "$MINT_10K"
  echo "  Minted 10,000 MockUSDC to consumer ✓"
fi

# ── Write .env.tempo_testnet ──────────────────────────────────────────────────
# Strip any previously written block, then append fresh values
python3 - <<PYEOF
import re, pathlib

env_file = pathlib.Path("$ENV_FILE")
lines = env_file.read_text().splitlines()

# Remove lines we're about to write
skip_keys = {
  "BROKER_WALLET","CONSUMER_WALLET","MOCK_USDC_ADDRESS",
  "AUDIT_LOG_ADDRESS","BROKER_CONTRACT_ADDRESS",
  "PRODUCER_AEROPLAN_WALLET","PRODUCER_SKYWATCH_WALLET",
}
kept = [l for l in lines
        if not any(l.startswith(k + "=") for k in skip_keys)
        and "Written by deploy_tempo" not in l]

new_block = [
  "",
  "# ── Written by deploy_tempo_testnet.sh ─────────────────────────────────",
  "BROKER_WALLET=$BROKER_ADDR",
  "CONSUMER_WALLET=$CONSUMER_ADDR",
  "MOCK_USDC_ADDRESS=$USDC_ADDR",
  "AUDIT_LOG_ADDRESS=$AUDIT_ADDR",
  "BROKER_CONTRACT_ADDRESS=$BROKER_CONTRACT",
  "PRODUCER_AEROPLAN_WALLET=",
  "PRODUCER_SKYWATCH_WALLET=",
]

env_file.write_text("\n".join(kept + new_block) + "\n")
print("  .env.tempo_testnet updated ✓")
PYEOF

echo ""
echo "  ─────────────────────────────────────────────────────────────────"
echo "  Deploy complete!"
echo "  Explorer: https://explore.moderato.tempo.xyz/address/$BROKER_CONTRACT"
echo ""
echo "  Next steps:"
echo "    python demo.py --env .env.tempo_testnet"
echo "    python -m agents.mpp.broker_server --env .env.tempo_testnet"
echo "  ─────────────────────────────────────────────────────────────────"
echo ""
