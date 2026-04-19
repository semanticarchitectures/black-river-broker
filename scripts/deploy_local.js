/**
 * scripts/deploy_local.js
 * Pure Node.js / ethers v6 deploy script — no Hardhat compile step.
 * Reads compiled artifacts from artifacts/contracts/ (produced by compile_local.js).
 *
 * Usage:  node scripts/deploy_local.js
 *
 * Writes contract addresses + keys to .env.local for demo.py.
 *
 * Hardhat default accounts (test mnemonic):
 *   [0]  0xf39F…2266  owner / deployer
 *   [1]  0x7099…79C8  broker wallet  (also acts as consumer in Phase 1)
 *   [2]  0x3C44…3BC   spare
 *   [3]  0x90F7…906   AeroPlan Systems producer wallet
 *   [4]  0x15d3…A65   SkyWatch Operations producer wallet
 */

const { ethers } = require("ethers");
const fs   = require("fs");
const path = require("path");

const RPC_URL     = "http://127.0.0.1:8545";
const ARTIFACTS   = path.join(__dirname, "..", "artifacts", "contracts");
const ENV_LOCAL   = path.join(__dirname, "..", ".env.local");

// Hardhat default private keys (public knowledge — local dev only)
const KEYS = [
  "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80", // [0] owner
  "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d", // [1] broker
  "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a", // [2] spare
  "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6", // [3] aeroplan
  "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926b", // [4] skywatch
];

function loadArtifact(name) {
  const p = path.join(ARTIFACTS, `${name}.sol`, `${name}.json`);
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

const usdc   = n => BigInt(n) * 10n ** 6n;

// Nonce trackers — incremented after each successful send, avoiding
// any RPC round-trips that could race with Hardhat's instant autominer.
const nonces = {};

async function send(wallet, provider, txRequest) {
  if (nonces[wallet.address] === undefined) {
    nonces[wallet.address] = await provider.getTransactionCount(wallet.address, "latest");
  }
  const feeData = await provider.getFeeData();
  const tx = await wallet.sendTransaction({
    nonce: nonces[wallet.address]++,
    gasLimit: 5_000_000n,
    maxFeePerGas: feeData.maxFeePerGas,
    maxPriorityFeePerGas: feeData.maxPriorityFeePerGas,
    ...txRequest,
  });
  return tx.wait();
}

async function main() {
  const provider = new ethers.JsonRpcProvider(RPC_URL);
  const signers  = KEYS.map(k => new ethers.Wallet(k, provider));
  const [owner, broker, , aeroplan, skywatch] = signers;

  console.log("Deploying to Hardhat local node…");
  console.log(`  owner   : ${owner.address}`);
  console.log(`  broker  : ${broker.address}`);
  console.log(`  aeroplan: ${aeroplan.address}`);
  console.log(`  skywatch: ${skywatch.address}`);
  console.log();

  // ── Deploy MockUSDC ───────────────────────────────────────────────────────
  const MockUSDCArt = loadArtifact("MockUSDC");
  const MockUSDCFac = new ethers.ContractFactory(MockUSDCArt.abi, MockUSDCArt.bytecode, owner);
  const deployMUSDC = await MockUSDCFac.getDeployTransaction(owner.address);
  const rcpt1       = await send(owner, provider, deployMUSDC);
  const usdcAddr    = rcpt1.contractAddress;
  const mockUSDC    = new ethers.Contract(usdcAddr, MockUSDCArt.abi, owner);
  console.log(`  MockUSDC          → ${usdcAddr}`);

  // ── Deploy AuditLog ───────────────────────────────────────────────────────
  const AuditLogArt = loadArtifact("AuditLog");
  const AuditLogFac = new ethers.ContractFactory(AuditLogArt.abi, AuditLogArt.bytecode, owner);
  const deployAL    = await AuditLogFac.getDeployTransaction();
  const rcpt2       = await send(owner, provider, deployAL);
  const auditAddr   = rcpt2.contractAddress;
  const auditLog    = new ethers.Contract(auditAddr, AuditLogArt.abi, owner);
  console.log(`  AuditLog          → ${auditAddr}`);

  // ── Deploy BlackRiverBroker ───────────────────────────────────────────────
  const BrokerArt = loadArtifact("BlackRiverBroker");
  const BrokerFac = new ethers.ContractFactory(BrokerArt.abi, BrokerArt.bytecode, owner);
  const deployBR  = await BrokerFac.getDeployTransaction(usdcAddr, auditAddr, broker.address);
  const rcpt3     = await send(owner, provider, deployBR);
  const brokerAddr = rcpt3.contractAddress;
  const brokerC   = new ethers.Contract(brokerAddr, BrokerArt.abi, owner);
  console.log(`  BlackRiverBroker  → ${brokerAddr}`);
  console.log();

  // ── Authorise broker wallet + contract to write AuditLog ─────────────────
  const setAuth1 = await auditLog.setAuthorised.populateTransaction(brokerAddr, true);
  await send(owner, provider, setAuth1);
  const setAuth2 = await auditLog.setAuthorised.populateTransaction(broker.address, true);
  await send(owner, provider, setAuth2);
  console.log("  AuditLog: broker wallet + contract authorised ✓");

  // ── Fund broker wallet with USDC (acts as consumer in Phase 1) ───────────
  const MINT_AMOUNT = usdc(100_000);
  const mintTx = await mockUSDC.mint.populateTransaction(broker.address, MINT_AMOUNT);
  await send(owner, provider, mintTx);
  const bal = await mockUSDC.balanceOf(broker.address);
  console.log(`  Minted ${Number(bal) / 1e6} USDC to broker wallet ✓`);

  // ── Broker approves broker contract to pull USDC ──────────────────────────
  const approveTx = await mockUSDC.connect(broker).approve.populateTransaction(brokerAddr, MINT_AMOUNT);
  await send(broker, provider, approveTx);
  console.log(`  USDC approval granted: broker → broker contract ✓`);
  console.log();

  // ── Write .env.local ──────────────────────────────────────────────────────
  const envContent = [
    `# Auto-generated by deploy_local.js — do not edit manually`,
    `HARDHAT_RPC=http://127.0.0.1:8545`,
    `BROKER_WALLET=${broker.address}`,
    `DEPLOYER_PRIVATE_KEY=${KEYS[1]}`,
    `MOCK_USDC_ADDRESS=${usdcAddr}`,
    `AUDIT_LOG_ADDRESS=${auditAddr}`,
    `BROKER_CONTRACT_ADDRESS=${brokerAddr}`,
    `PRODUCER_AEROPLAN_WALLET=${aeroplan.address}`,
    `PRODUCER_SKYWATCH_WALLET=${skywatch.address}`,
  ].join("\n") + "\n";

  fs.writeFileSync(ENV_LOCAL, envContent);
  console.log(`  .env.local written ✓`);
  console.log();
  console.log("Deploy complete. Run:  python demo.py");
}

main().catch(e => { console.error(e); process.exit(1); });
