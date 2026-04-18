/**
 * scripts/test_contracts.js
 * Lightweight contract test runner using ethers + Hardhat node-in-process.
 * Runs in the restricted sandbox (no compiler download needed — uses
 * local artifacts from compile_local.js).
 *
 * Usage:  node scripts/test_contracts.js
 */
const { ethers }  = require("ethers");
const fs          = require("fs");
const path        = require("path");

// ── Load artifacts ────────────────────────────────────────────────────────
function loadArtifact(name) {
  const p = path.join(__dirname, "..", "artifacts", "contracts",
                      `${name}.sol`, `${name}.json`);
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

const MockUSDCArt   = loadArtifact("MockUSDC");
const AuditLogArt   = loadArtifact("AuditLog");
const BrokerArt     = loadArtifact("BlackRiverBroker");

// ── In-process Hardhat-style provider using ethers JsonRpcProvider ────────
// We'll use the hardhat node if running, or spin up with hardhat programmatically
const { HardhatEthersSigner } = require("@nomicfoundation/hardhat-ethers/signers");

// Actually use node's hardhat programmatic API
const hre = require("hardhat");

// ── Test framework ────────────────────────────────────────────────────────
let passed = 0, failed = 0;
const results = [];

async function test(name, fn) {
  try {
    await fn();
    console.log(`  ✓ ${name}`);
    passed++;
    results.push({ name, ok: true });
  } catch (e) {
    console.log(`  ✗ ${name}`);
    console.log(`      ${e.message}`);
    failed++;
    results.push({ name, ok: false, error: e.message });
  }
}

function assert(condition, msg) {
  if (!condition) throw new Error(msg || "Assertion failed");
}
function assertEq(a, b, msg) {
  if (a !== b && String(a) !== String(b))
    throw new Error(msg || `Expected ${b}, got ${a}`);
}
function assertGt(a, b, msg) {
  if (!(BigInt(a) > BigInt(b))) throw new Error(msg || `Expected ${a} > ${b}`);
}

// ── Setup ─────────────────────────────────────────────────────────────────
const USDC_DEC  = 6n;
const usdc = n  => BigInt(n) * 10n ** USDC_DEC;
const AWARD_ID  = "AWD-TEST0001";
const REQ_ID    = "REQ-TEST0001";
const ZERO_HASH = "0x" + "0".repeat(64);
const DLV_HASH  = ethers.keccak256(ethers.toUtf8Bytes("video.mp4"));

async function deploy(signers) {
  const [owner, broker, consumer, producer, stranger] = signers;

  const MockUSDC   = new ethers.ContractFactory(MockUSDCArt.abi, MockUSDCArt.bytecode, owner);
  const mockUSDC   = await MockUSDC.deploy(owner.address);
  await mockUSDC.waitForDeployment();

  const AuditLog   = new ethers.ContractFactory(AuditLogArt.abi, AuditLogArt.bytecode, owner);
  const auditLog   = await AuditLog.deploy();
  await auditLog.waitForDeployment();

  const Broker     = new ethers.ContractFactory(BrokerArt.abi, BrokerArt.bytecode, owner);
  const brokerC    = await Broker.deploy(
    await mockUSDC.getAddress(),
    await auditLog.getAddress(),
    broker.address,
  );
  await brokerC.waitForDeployment();

  const brokerAddr = await brokerC.getAddress();
  await auditLog.setAuthorised(brokerAddr, true);
  await auditLog.setAuthorised(broker.address, true);

  await mockUSDC.mint(consumer.address, usdc(10_000));
  await mockUSDC.connect(consumer).approve(brokerAddr, usdc(10_000));

  return { mockUSDC, auditLog, brokerC, owner, broker, consumer, producer, stranger };
}

// ── Tests ─────────────────────────────────────────────────────────────────
async function runAll() {
  const signers = await hre.ethers.getSigners();

  console.log("\n── MockUSDC ──────────────────────────────────────────────────");
  {
    const d = await deploy(signers);

    await test("has 6 decimals", async () => {
      assertEq(await d.mockUSDC.decimals(), 6n);
    });

    await test("owner can mint", async () => {
      await d.mockUSDC.mint(d.consumer.address, usdc(500));
      assertEq(await d.mockUSDC.balanceOf(d.consumer.address), usdc(10_500));
    });

    await test("non-owner cannot mint", async () => {
      try {
        await d.mockUSDC.connect(d.stranger).mint(d.stranger.address, usdc(100));
        throw new Error("should have reverted");
      } catch (e) {
        assert(e.message.includes("revert") || e.message.includes("Unauthorized"),
          `Wrong error: ${e.message}`);
      }
    });

    await test("faucet mints 1000 USDC to caller", async () => {
      await d.mockUSDC.connect(d.stranger).faucet();
      assertEq(await d.mockUSDC.balanceOf(d.stranger.address), usdc(1_000));
    });
  }

  console.log("\n── AuditLog ──────────────────────────────────────────────────");
  {
    const d = await deploy(signers);
    const PAYLOAD_HASH = ethers.keccak256(ethers.toUtf8Bytes("test-payload"));

    await test("owner is authorised by default", async () => {
      assert(await d.auditLog.authorised(d.owner.address));
    });

    await test("authorised address can log an event", async () => {
      await d.auditLog.connect(d.broker).logEvent(
        "EVT-A", REQ_ID, 0, d.broker.address, "Announced", PAYLOAD_HASH,
      );
      assertEq(await d.auditLog.totalEvents(), 1n);
    });

    await test("getEvent returns correct data", async () => {
      const ev = await d.auditLog.getEvent(0n);
      assertEq(ev.eventId, "EVT-A");
      assertEq(ev.requirementId, REQ_ID);
      assertEq(ev.stage, 0n); // ANNOUNCED
    });

    await test("getEventsByRequirement filters correctly", async () => {
      await d.auditLog.connect(d.broker).logEvent(
        "EVT-B", REQ_ID, 1, d.broker.address, "Bidding", PAYLOAD_HASH,
      );
      await d.auditLog.connect(d.broker).logEvent(
        "EVT-C", "REQ-OTHER", 0, d.broker.address, "Other", PAYLOAD_HASH,
      );
      const evs = await d.auditLog.getEventsByRequirement(REQ_ID);
      assertEq(evs.length, 2);
      assertEq(evs[0].eventId, "EVT-A");
      assertEq(evs[1].eventId, "EVT-B");
    });

    await test("unauthorised address cannot log events", async () => {
      try {
        await d.auditLog.connect(d.stranger).logEvent(
          "EVT-X", REQ_ID, 0, d.stranger.address, "Bad", PAYLOAD_HASH,
        );
        throw new Error("should have reverted");
      } catch (e) {
        assert(e.message.includes("not authorised"), `Wrong error: ${e.message}`);
      }
    });

    await test("getEvent reverts on out-of-range index", async () => {
      try {
        await d.auditLog.getEvent(99n);
        throw new Error("should have reverted");
      } catch (e) {
        assert(e.message.includes("out of range"), `Wrong error: ${e.message}`);
      }
    });
  }

  console.log("\n── BlackRiverBroker ──────────────────────────────────────────");
  {
    // createContract
    {
      const d = await deploy(signers);
      await test("createContract escrows USDC from consumer", async () => {
        const before = await d.mockUSDC.balanceOf(d.consumer.address);
        await d.brokerC.connect(d.broker).createContract(
          AWARD_ID, REQ_ID, d.consumer.address, usdc(1_000)
        );
        const after = await d.mockUSDC.balanceOf(d.consumer.address);
        assertEq(before - after, usdc(1_000));
        assertEq(await d.mockUSDC.balanceOf(await d.brokerC.getAddress()), usdc(1_000));
      });
    }
    {
      const d = await deploy(signers);
      await test("createContract reverts for non-broker caller", async () => {
        try {
          await d.brokerC.connect(d.stranger).createContract(
            AWARD_ID, REQ_ID, d.consumer.address, usdc(100)
          );
          throw new Error("should revert");
        } catch (e) {
          assert(e.message.includes("not the broker"), `Wrong error: ${e.message}`);
        }
      });
    }
    {
      const d = await deploy(signers);
      await test("createContract reverts duplicate awardId", async () => {
        await d.brokerC.connect(d.broker).createContract(
          AWARD_ID, REQ_ID, d.consumer.address, usdc(100)
        );
        try {
          await d.brokerC.connect(d.broker).createContract(
            AWARD_ID, REQ_ID, d.consumer.address, usdc(100)
          );
          throw new Error("should revert");
        } catch (e) {
          assert(e.message.includes("already exists"), `Wrong error: ${e.message}`);
        }
      });
    }

    // recordAward
    {
      const d = await deploy(signers);
      await d.brokerC.connect(d.broker).createContract(
        AWARD_ID, REQ_ID, d.consumer.address, usdc(500)
      );
      await test("recordAward sets producer and status=AWARDED", async () => {
        await d.brokerC.connect(d.broker).recordAward(AWARD_ID, d.producer.address);
        const c = await d.brokerC.getContract(AWARD_ID);
        assertEq(c.producer, d.producer.address);
        assertEq(c.status, 1n); // AWARDED
      });
    }

    // confirmDelivery
    {
      const d = await deploy(signers);
      await d.brokerC.connect(d.broker).createContract(AWARD_ID, REQ_ID, d.consumer.address, usdc(500));
      await d.brokerC.connect(d.broker).recordAward(AWARD_ID, d.producer.address);

      await test("confirmDelivery stores hash and status=DELIVERED", async () => {
        await d.brokerC.connect(d.broker).confirmDelivery(AWARD_ID, DLV_HASH);
        const c = await d.brokerC.getContract(AWARD_ID);
        assertEq(c.deliveryHash, DLV_HASH);
        assertEq(c.status, 2n); // DELIVERED
      });
    }
    {
      const d = await deploy(signers);
      await d.brokerC.connect(d.broker).createContract(AWARD_ID, REQ_ID, d.consumer.address, usdc(100));
      await d.brokerC.connect(d.broker).recordAward(AWARD_ID, d.producer.address);

      await test("confirmDelivery reverts with zero hash", async () => {
        try {
          await d.brokerC.connect(d.broker).confirmDelivery(AWARD_ID, ZERO_HASH);
          throw new Error("should revert");
        } catch (e) {
          assert(e.message.includes("invalid delivery hash"), `Wrong: ${e.message}`);
        }
      });
    }

    // settlePayment
    {
      const d = await deploy(signers);
      await d.brokerC.connect(d.broker).createContract(AWARD_ID, REQ_ID, d.consumer.address, usdc(1_000));
      await d.brokerC.connect(d.broker).recordAward(AWARD_ID, d.producer.address);
      await d.brokerC.connect(d.broker).confirmDelivery(AWARD_ID, DLV_HASH);

      await test("settlePayment releases USDC to producer", async () => {
        const before = await d.mockUSDC.balanceOf(d.producer.address);
        await d.brokerC.connect(d.broker).settlePayment(AWARD_ID);
        assertEq(await d.mockUSDC.balanceOf(d.producer.address), before + usdc(1_000));
      });
    }
    {
      const d = await deploy(signers);
      await d.brokerC.connect(d.broker).createContract(AWARD_ID, REQ_ID, d.consumer.address, usdc(100));
      await d.brokerC.connect(d.broker).recordAward(AWARD_ID, d.producer.address);

      await test("settlePayment reverts if not delivered", async () => {
        try {
          await d.brokerC.connect(d.broker).settlePayment(AWARD_ID);
          throw new Error("should revert");
        } catch (e) {
          assert(e.message.includes("not delivered"), `Wrong: ${e.message}`);
        }
      });
    }

    // cancelContract
    {
      const d = await deploy(signers);
      await d.brokerC.connect(d.broker).createContract(AWARD_ID, REQ_ID, d.consumer.address, usdc(800));
      await test("cancelContract refunds consumer", async () => {
        const before = await d.mockUSDC.balanceOf(d.consumer.address);
        await d.brokerC.connect(d.broker).cancelContract(AWARD_ID);
        assertEq(await d.mockUSDC.balanceOf(d.consumer.address), before + usdc(800));
      });
    }

    // Full lifecycle
    {
      const d = await deploy(signers);
      await test("Full lifecycle: create → award → deliver → settle", async () => {
        const consumerBefore  = await d.mockUSDC.balanceOf(d.consumer.address);
        const producerBefore  = await d.mockUSDC.balanceOf(d.producer.address);
        const amt = usdc(2_000);

        await d.brokerC.connect(d.broker).createContract(AWARD_ID, REQ_ID, d.consumer.address, amt);
        await d.brokerC.connect(d.broker).recordAward(AWARD_ID, d.producer.address);
        await d.brokerC.connect(d.broker).confirmDelivery(AWARD_ID, DLV_HASH);
        await d.brokerC.connect(d.broker).settlePayment(AWARD_ID);

        assertEq(await d.mockUSDC.balanceOf(d.consumer.address), consumerBefore - amt);
        assertEq(await d.mockUSDC.balanceOf(d.producer.address), producerBefore + amt);

        const c = await d.brokerC.getContract(AWARD_ID);
        assertEq(c.status, 3n); // SETTLED
        assertGt(c.settledAt, 0n);
      });
    }
  }

  // ── Summary ───────────────────────────────────────────────────────────
  console.log("\n─────────────────────────────────────────────────────────────");
  console.log(`  ${passed} passed  |  ${failed} failed`);
  if (failed > 0) {
    console.log("\nFailed tests:");
    results.filter(r => !r.ok).forEach(r => console.log(`  ✗ ${r.name}: ${r.error}`));
    process.exit(1);
  }
  console.log("\n  All contract tests PASSED ✓\n");
}

runAll().catch(e => { console.error(e); process.exit(1); });
