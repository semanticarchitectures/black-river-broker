/**
 * scripts/deploy_tempo_testnet.js
 * Deploy Black River contracts to the Tempo testnet.
 *
 * Prerequisites
 * ─────────────
 *  1. Copy .env.tempo_testnet.example → .env.tempo_testnet and fill in:
 *       DEPLOYER_PRIVATE_KEY   – funded with Tempo testnet ETH (gas) + USDC
 *       BROKER_PRIVATE_KEY     – separate broker wallet (can equal deployer for testing)
 *       PRODUCER_AEROPLAN_KEY  – AeroPlan wallet private key (testnet)
 *       PRODUCER_SKYWATCH_KEY  – SkyWatch wallet private key (testnet)
 *
 *  2. Fund DEPLOYER wallet with testnet ETH from the Tempo faucet:
 *       https://faucet.tempo.xyz
 *
 *  3. Run:
 *       node scripts/deploy_tempo_testnet.js
 *
 *  4. Addresses are written to .env.tempo_testnet (overwrites the keys section).
 *     Run the demo against the testnet:
 *       python demo.py --env .env.tempo_testnet
 *
 * Tempo Testnet
 * ─────────────
 *  RPC  : https://rpc.testnet.tempo.xyz
 *  Chain: 42429
 *  Explorer: https://explorer.testnet.tempo.xyz
 */

const { ethers } = require("ethers");
const fs   = require("fs");
const path = require("path");

// ── Load .env.tempo_testnet ───────────────────────────────────────────────────
const ENV_FILE = path.join(__dirname, "..", ".env.tempo_testnet");
if (!fs.existsSync(ENV_FILE)) {
  console.error(`\n  ✗  ${ENV_FILE} not found.`);
  console.error(`     Copy .env.tempo_testnet.example → .env.tempo_testnet and fill in your keys.\n`);
  process.exit(1);
}

// Parse the env file manually (avoids dotenv dependency in this script)
const envVars = {};
fs.readFileSync(ENV_FILE, "utf8").split("\n").forEach(line => {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith("#")) return;
  const idx = trimmed.indexOf("=");
  if (idx < 0) return;
  const k = trimmed.slice(0, idx).trim();
  const v = trimmed.slice(idx + 1).trim();
  envVars[k] = v;
});

const TEMPO_TESTNET_RPC = envVars.TEMPO_TESTNET_RPC || "https://rpc.testnet.tempo.xyz";
const DEPLOYER_KEY      = envVars.DEPLOYER_PRIVATE_KEY  || "";
const BROKER_KEY        = envVars.BROKER_PRIVATE_KEY    || DEPLOYER_KEY;
const CONSUMER_KEY      = envVars.CONSUMER_PRIVATE_KEY  || "";
const AEROPLAN_KEY      = envVars.PRODUCER_AEROPLAN_KEY || "";
const SKYWATCH_KEY      = envVars.PRODUCER_SKYWATCH_KEY || "";

if (!DEPLOYER_KEY.startsWith("0x") || DEPLOYER_KEY.length < 64) {
  console.error("\n  ✗  DEPLOYER_PRIVATE_KEY missing or malformed in .env.tempo_testnet\n");
  process.exit(1);
}

const ARTIFACTS = path.join(__dirname, "..", "artifacts", "contracts");

function loadArtifact(name) {
  const p = path.join(ARTIFACTS, `${name}.sol`, `${name}.json`);
  if (!fs.existsSync(p)) {
    console.error(`\n  ✗  Artifact not found: ${p}`);
    console.error(`     Run: node scripts/compile_local.js\n`);
    process.exit(1);
  }
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

const usdc = n => BigInt(n) * 10n ** 6n;

// Nonce tracking — prevents race conditions on fast-finality chains
const nonces = {};
async function send(wallet, provider, txRequest) {
  if (nonces[wallet.address] === undefined) {
    nonces[wallet.address] = await provider.getTransactionCount(wallet.address, "latest");
  }
  const feeData = await provider.getFeeData();
  const tx = await wallet.sendTransaction({
    nonce:               nonces[wallet.address]++,
    gasLimit:            2_000_000n,
    maxFeePerGas:        feeData.maxFeePerGas,
    maxPriorityFeePerGas:feeData.maxPriorityFeePerGas,
    ...txRequest,
  });
  console.log(`    ↻ tx ${tx.hash.slice(0, 18)}… pending`);
  const receipt = await tx.wait();
  console.log(`    ✓ confirmed in block ${receipt.blockNumber}`);
  return receipt;
}

async function main() {
  const provider = new ethers.JsonRpcProvider(TEMPO_TESTNET_RPC);
  const network  = await provider.getNetwork();

  console.log(`\n  Network : ${network.name}  (chain ${network.chainId})`);
  console.log(`  RPC     : ${TEMPO_TESTNET_RPC}`);

  const TEMPO_CHAIN_IDS = [42429n, 42431n];  // testnet + Moderato testnet
  if (!TEMPO_CHAIN_IDS.includes(network.chainId)) {
    console.warn(`  ⚠  Unexpected chain ID ${network.chainId} — expected 42429 or 42431.`);
    console.warn(`     Proceeding anyway — double-check your RPC URL.`);
  }

  // Validate that a key looks like a real hex private key, not a placeholder
  function isValidKey(k) {
    return typeof k === "string" && /^0x[0-9a-fA-F]{64}$/.test(k);
  }

  const deployer  = new ethers.Wallet(DEPLOYER_KEY, provider);
  const broker    = isValidKey(BROKER_KEY)   ? new ethers.Wallet(BROKER_KEY,   provider) : deployer;
  const consumer  = isValidKey(CONSUMER_KEY) ? new ethers.Wallet(CONSUMER_KEY, provider) : null;
  const aeroplan  = isValidKey(AEROPLAN_KEY) ? new ethers.Wallet(AEROPLAN_KEY, provider) : null;
  const skywatch  = isValidKey(SKYWATCH_KEY) ? new ethers.Wallet(SKYWATCH_KEY, provider) : null;

  if (!consumer) {
    console.warn(`  ⚠  CONSUMER_PRIVATE_KEY not set or is still a placeholder — skipping consumer USDC mint.`);
  }

  console.log(`\n  deployer : ${deployer.address}`);
  console.log(`  broker   : ${broker.address}`);
  if (consumer) console.log(`  consumer : ${consumer.address}`);
  if (aeroplan) console.log(`  aeroplan : ${aeroplan.address}`);
  if (skywatch) console.log(`  skywatch : ${skywatch.address}`);

  // ── Check deployer balance ────────────────────────────────────────────────
  const balance = await provider.getBalance(deployer.address);
  console.log(`\n  Deployer balance: ${ethers.formatEther(balance)} ETH`);
  if (balance === 0n) {
    console.error(`\n  ✗  Deployer wallet has 0 ETH. Fund it at: https://faucet.tempo.xyz\n`);
    process.exit(1);
  }

  // ── Deploy MockUSDC ───────────────────────────────────────────────────────
  console.log("\n  Deploying MockUSDC…");
  const MockUSDCArt = loadArtifact("MockUSDC");
  const MockUSDCFac = new ethers.ContractFactory(MockUSDCArt.abi, MockUSDCArt.bytecode, deployer);
  const deployMUSDC = await MockUSDCFac.getDeployTransaction(deployer.address);
  const rcpt1       = await send(deployer, provider, deployMUSDC);
  const usdcAddr    = rcpt1.contractAddress;
  const mockUSDC    = new ethers.Contract(usdcAddr, MockUSDCArt.abi, deployer);
  console.log(`  MockUSDC          → ${usdcAddr}`);

  // ── Deploy AuditLog ───────────────────────────────────────────────────────
  console.log("\n  Deploying AuditLog…");
  const AuditLogArt = loadArtifact("AuditLog");
  const AuditLogFac = new ethers.ContractFactory(AuditLogArt.abi, AuditLogArt.bytecode, deployer);
  const deployAL    = await AuditLogFac.getDeployTransaction();
  const rcpt2       = await send(deployer, provider, deployAL);
  const auditAddr   = rcpt2.contractAddress;
  const auditLog    = new ethers.Contract(auditAddr, AuditLogArt.abi, deployer);
  console.log(`  AuditLog          → ${auditAddr}`);

  // ── Deploy BlackRiverBroker ───────────────────────────────────────────────
  console.log("\n  Deploying BlackRiverBroker…");
  const BrokerArt = loadArtifact("BlackRiverBroker");
  const BrokerFac = new ethers.ContractFactory(BrokerArt.abi, BrokerArt.bytecode, deployer);
  const deployBR  = await BrokerFac.getDeployTransaction(usdcAddr, auditAddr, broker.address);
  const rcpt3     = await send(deployer, provider, deployBR);
  const brokerAddr = rcpt3.contractAddress;
  const brokerC   = new ethers.Contract(brokerAddr, BrokerArt.abi, deployer);
  console.log(`  BlackRiverBroker  → ${brokerAddr}`);

  // ── Authorise broker wallet + contract on AuditLog ───────────────────────
  console.log("\n  Authorising AuditLog writers…");
  const auth1 = await auditLog.setAuthorised.populateTransaction(brokerAddr, true);
  await send(deployer, provider, auth1);
  const auth2 = await auditLog.setAuthorised.populateTransaction(broker.address, true);
  await send(deployer, provider, auth2);
  console.log("  AuditLog: broker wallet + contract authorised ✓");

  // ── Mint USDC to broker wallet ────────────────────────────────────────────
  console.log("\n  Minting USDC to broker wallet…");
  const MINT_AMOUNT = usdc(100_000);
  const mintTx = await mockUSDC.mint.populateTransaction(broker.address, MINT_AMOUNT);
  await send(deployer, provider, mintTx);
  const brokerBal = await mockUSDC.balanceOf(broker.address);
  console.log(`  Minted ${Number(brokerBal) / 1e6} USDC to broker wallet ✓`);

  // ── Mint USDC to consumer wallet (for MPP payments) ──────────────────────
  let consumerAddr = "";
  if (consumer) {
    console.log("\n  Minting USDC to consumer wallet…");
    const CONSUMER_MINT = usdc(10_000);
    const consumerMintTx = await mockUSDC.mint.populateTransaction(consumer.address, CONSUMER_MINT);
    await send(deployer, provider, consumerMintTx);
    const consumerBal = await mockUSDC.balanceOf(consumer.address);
    console.log(`  Minted ${Number(consumerBal) / 1e6} USDC to consumer wallet ✓`);
    consumerAddr = consumer.address;
  }

  // ── Broker approves broker contract to pull USDC ──────────────────────────
  console.log("\n  Approving USDC allowance…");
  const brokerSigner = new ethers.Wallet(BROKER_KEY || DEPLOYER_KEY, provider);
  const approveTx = await mockUSDC.connect(brokerSigner).approve.populateTransaction(brokerAddr, MINT_AMOUNT);
  await send(brokerSigner, provider, approveTx);
  console.log("  USDC approval: broker → broker contract ✓");

  // ── Write .env.tempo_testnet ──────────────────────────────────────────────
  const existingLines = fs.readFileSync(ENV_FILE, "utf8")
    .split("\n")
    .filter(l => {
      const k = l.split("=")[0].trim();
      return ![
        "MOCK_USDC_ADDRESS", "AUDIT_LOG_ADDRESS", "BROKER_CONTRACT_ADDRESS",
        "BROKER_WALLET", "CONSUMER_WALLET",
        "PRODUCER_AEROPLAN_WALLET", "PRODUCER_SKYWATCH_WALLET",
      ].includes(k);
    });

  const deployedLines = [
    "",
    "# ── Written by deploy_tempo_testnet.js ──────────────────────────────────",
    `BROKER_WALLET=${broker.address}`,
    `CONSUMER_WALLET=${consumerAddr}`,
    `MOCK_USDC_ADDRESS=${usdcAddr}`,
    `AUDIT_LOG_ADDRESS=${auditAddr}`,
    `BROKER_CONTRACT_ADDRESS=${brokerAddr}`,
    aeroplan ? `PRODUCER_AEROPLAN_WALLET=${aeroplan.address}` : `PRODUCER_AEROPLAN_WALLET=`,
    skywatch ? `PRODUCER_SKYWATCH_WALLET=${skywatch.address}` : `PRODUCER_SKYWATCH_WALLET=`,
  ];

  fs.writeFileSync(ENV_FILE, [...existingLines, ...deployedLines].join("\n") + "\n");
  console.log(`\n  .env.tempo_testnet updated ✓`);

  console.log(`
  ──────────────────────────────────────────────────────
  Deploy complete. Tempo testnet explorer:
  https://explorer.testnet.tempo.xyz/address/${brokerAddr}

  Next steps:
  1. Direct broker demo (no HTTP):
       python demo.py --env .env.tempo_testnet

  2. MPP broker server + consumer agent:
       python -m agents.mpp.broker_server --env .env.tempo_testnet
       python -m agents.consumer.mpp_consumer --env .env.tempo_testnet
  ──────────────────────────────────────────────────────
`);
}

main().catch(e => { console.error(e); process.exit(1); });
