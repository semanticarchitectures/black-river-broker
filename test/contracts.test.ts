import { expect } from "chai";
import { ethers } from "hardhat";
import { SignerWithAddress } from "@nomicfoundation/hardhat-ethers/signers";
import { MockUSDC, AuditLog, BlackRiverBroker } from "../typechain-types";

// ── Helpers ───────────────────────────────────────────────────────────────
const USDC_DECIMALS  = 6n;
const usdc = (amount: number) => BigInt(amount) * 10n ** USDC_DECIMALS;

const AWARD_ID   = "AWD-TEST0001";
const REQ_ID     = "REQ-TEST0001";
const EVENT_ID   = "EVT-TEST0001";

// AuditLog Stage enum — must mirror AuditLog.sol Stage enum order
enum Stage { ANNOUNCED, BIDDING, EVALUATING, AWARDED, EXECUTING, DELIVERED, SETTLED, FAILED }

// ── Fixtures ──────────────────────────────────────────────────────────────
async function deployAll() {
  const [owner, broker, consumer, producer, stranger] =
    await ethers.getSigners();

  const MockUSDC         = await ethers.getContractFactory("MockUSDC");
  const mockUSDC         = await MockUSDC.deploy(owner.address) as MockUSDC;

  const AuditLog         = await ethers.getContractFactory("AuditLog");
  const auditLog         = await AuditLog.deploy() as AuditLog;

  const BlackRiverBroker = await ethers.getContractFactory("BlackRiverBroker");
  const brokerContract   = await BlackRiverBroker.deploy(
    await mockUSDC.getAddress(),
    await auditLog.getAddress(),
    broker.address,
  ) as BlackRiverBroker;

  // Authorise broker contract + broker wallet to write to AuditLog
  await auditLog.setAuthorised(await brokerContract.getAddress(), true);
  await auditLog.setAuthorised(broker.address, true);

  // Mint USDC and approve
  await mockUSDC.mint(consumer.address, usdc(10_000));
  await mockUSDC.connect(consumer).approve(
    await brokerContract.getAddress(), usdc(10_000)
  );

  return { mockUSDC, auditLog, brokerContract, owner, broker, consumer, producer, stranger };
}

// ═════════════════════════════════════════════════════════════════════════
// MockUSDC
// ═════════════════════════════════════════════════════════════════════════
describe("MockUSDC", () => {
  it("has 6 decimals", async () => {
    const { mockUSDC } = await deployAll();
    expect(await mockUSDC.decimals()).to.equal(6n);
  });

  it("owner can mint", async () => {
    const { mockUSDC, consumer } = await deployAll();
    await mockUSDC.mint(consumer.address, usdc(500));
    // consumer already had 10_000 from deployAll
    expect(await mockUSDC.balanceOf(consumer.address)).to.equal(usdc(10_500));
  });

  it("non-owner cannot mint", async () => {
    const { mockUSDC, stranger } = await deployAll();
    await expect(
      mockUSDC.connect(stranger).mint(stranger.address, usdc(100))
    ).to.be.revertedWithCustomError(mockUSDC, "OwnableUnauthorizedAccount");
  });

  it("faucet mints 1000 USDC to caller", async () => {
    const { mockUSDC, stranger } = await deployAll();
    await mockUSDC.connect(stranger).faucet();
    expect(await mockUSDC.balanceOf(stranger.address)).to.equal(usdc(1_000));
  });

  it("supports standard ERC20 transfer and allowance", async () => {
    const { mockUSDC, consumer, stranger } = await deployAll();
    await mockUSDC.connect(consumer).transfer(stranger.address, usdc(100));
    expect(await mockUSDC.balanceOf(stranger.address)).to.equal(usdc(100));
  });
});

// ═════════════════════════════════════════════════════════════════════════
// AuditLog
// ═════════════════════════════════════════════════════════════════════════
describe("AuditLog", () => {
  const PAYLOAD_HASH = ethers.keccak256(ethers.toUtf8Bytes("test-payload"));

  it("owner is authorised by default", async () => {
    const { auditLog, owner } = await deployAll();
    expect(await auditLog.authorised(owner.address)).to.be.true;
  });

  it("authorised address can log an event", async () => {
    const { auditLog, broker } = await deployAll();
    await auditLog.connect(broker).logEvent(
      EVENT_ID, REQ_ID, Stage.ANNOUNCED, broker.address,
      "Task announced", PAYLOAD_HASH,
    );
    expect(await auditLog.totalEvents()).to.equal(1n);
  });

  it("emits EventLogged with correct fields", async () => {
    const { auditLog, broker } = await deployAll();
    await expect(
      auditLog.connect(broker).logEvent(
        EVENT_ID, REQ_ID, Stage.ANNOUNCED, broker.address,
        "Task announced", PAYLOAD_HASH,
      )
    ).to.emit(auditLog, "EventLogged")
      .withArgs(0n, REQ_ID, Stage.ANNOUNCED, broker.address,
                EVENT_ID, PAYLOAD_HASH, await ethers.provider
                  .getBlock("latest").then(b => b!.timestamp + 1));
  });

  it("getEvent returns correct data", async () => {
    const { auditLog, broker } = await deployAll();
    await auditLog.connect(broker).logEvent(
      EVENT_ID, REQ_ID, Stage.AWARDED, broker.address,
      "Award issued", PAYLOAD_HASH,
    );
    const ev = await auditLog.getEvent(0n);
    expect(ev.eventId).to.equal(EVENT_ID);
    expect(ev.requirementId).to.equal(REQ_ID);
    expect(ev.stage).to.equal(Stage.AWARDED);
    expect(ev.payloadHash).to.equal(PAYLOAD_HASH);
  });

  it("getEventsByRequirement filters correctly", async () => {
    const { auditLog, broker } = await deployAll();
    // Log two events for REQ_ID and one for a different req
    await auditLog.connect(broker).logEvent(
      "EVT-A", REQ_ID, Stage.ANNOUNCED, broker.address, "A", PAYLOAD_HASH,
    );
    await auditLog.connect(broker).logEvent(
      "EVT-B", REQ_ID, Stage.BIDDING, broker.address, "B", PAYLOAD_HASH,
    );
    await auditLog.connect(broker).logEvent(
      "EVT-C", "REQ-OTHER", Stage.ANNOUNCED, broker.address, "C", PAYLOAD_HASH,
    );
    const events = await auditLog.getEventsByRequirement(REQ_ID);
    expect(events.length).to.equal(2);
    expect(events[0].eventId).to.equal("EVT-A");
    expect(events[1].eventId).to.equal("EVT-B");
  });

  it("unauthorised address cannot log events", async () => {
    const { auditLog, stranger } = await deployAll();
    await expect(
      auditLog.connect(stranger).logEvent(
        EVENT_ID, REQ_ID, Stage.ANNOUNCED, stranger.address,
        "Unauthorised", PAYLOAD_HASH,
      )
    ).to.be.revertedWith("AuditLog: not authorised writer");
  });

  it("reverts on out-of-range getEvent index", async () => {
    const { auditLog } = await deployAll();
    await expect(auditLog.getEvent(0n)).to.be.revertedWith("AuditLog: index out of range");
  });

  it("owner can revoke authorisation", async () => {
    const { auditLog, owner, broker } = await deployAll();
    await auditLog.connect(owner).setAuthorised(broker.address, false);
    expect(await auditLog.authorised(broker.address)).to.be.false;
  });
});

// ═════════════════════════════════════════════════════════════════════════
// BlackRiverBroker
// ═════════════════════════════════════════════════════════════════════════
describe("BlackRiverBroker", () => {

  // ── createContract ─────────────────────────────────────────────────────
  describe("createContract", () => {
    it("escrows USDC from consumer", async () => {
      const { mockUSDC, brokerContract, broker, consumer } = await deployAll();
      const brokerAddr = await brokerContract.getAddress();
      const before = await mockUSDC.balanceOf(consumer.address);

      await brokerContract.connect(broker).createContract(
        AWARD_ID, REQ_ID, consumer.address, usdc(1_000)
      );

      expect(await mockUSDC.balanceOf(consumer.address)).to.equal(before - usdc(1_000));
      expect(await mockUSDC.balanceOf(brokerAddr)).to.equal(usdc(1_000));
    });

    it("emits ContractCreated", async () => {
      const { brokerContract, broker, consumer } = await deployAll();
      await expect(
        brokerContract.connect(broker).createContract(
          AWARD_ID, REQ_ID, consumer.address, usdc(500)
        )
      ).to.emit(brokerContract, "ContractCreated")
        .withArgs(AWARD_ID, REQ_ID, consumer.address, usdc(500));
    });

    it("reverts if award already exists", async () => {
      const { brokerContract, broker, consumer } = await deployAll();
      await brokerContract.connect(broker).createContract(
        AWARD_ID, REQ_ID, consumer.address, usdc(500)
      );
      await expect(
        brokerContract.connect(broker).createContract(
          AWARD_ID, REQ_ID, consumer.address, usdc(500)
        )
      ).to.be.revertedWith("BlackRiverBroker: award already exists");
    });

    it("reverts if amount is zero", async () => {
      const { brokerContract, broker, consumer } = await deployAll();
      await expect(
        brokerContract.connect(broker).createContract(
          AWARD_ID, REQ_ID, consumer.address, 0n
        )
      ).to.be.revertedWith("BlackRiverBroker: amount must be > 0");
    });

    it("reverts for non-broker caller", async () => {
      const { brokerContract, stranger, consumer } = await deployAll();
      await expect(
        brokerContract.connect(stranger).createContract(
          AWARD_ID, REQ_ID, consumer.address, usdc(100)
        )
      ).to.be.revertedWith("BlackRiverBroker: caller is not the broker");
    });
  });

  // ── recordAward ────────────────────────────────────────────────────────
  describe("recordAward", () => {
    async function withCreated() {
      const d = await deployAll();
      await d.brokerContract.connect(d.broker).createContract(
        AWARD_ID, REQ_ID, d.consumer.address, usdc(1_000)
      );
      return d;
    }

    it("records the winning producer", async () => {
      const { brokerContract, broker, producer } = await withCreated();
      await brokerContract.connect(broker).recordAward(AWARD_ID, producer.address);
      const c = await brokerContract.getContract(AWARD_ID);
      expect(c.producer).to.equal(producer.address);
      expect(c.status).to.equal(1n); // AWARDED
    });

    it("emits ContractAwarded", async () => {
      const { brokerContract, broker, producer } = await withCreated();
      await expect(
        brokerContract.connect(broker).recordAward(AWARD_ID, producer.address)
      ).to.emit(brokerContract, "ContractAwarded").withArgs(AWARD_ID, producer.address);
    });

    it("reverts if contract does not exist", async () => {
      const { brokerContract, broker, producer } = await deployAll();
      await expect(
        brokerContract.connect(broker).recordAward("AWD-MISSING", producer.address)
      ).to.be.revertedWith("BlackRiverBroker: contract not found");
    });
  });

  // ── confirmDelivery ────────────────────────────────────────────────────
  describe("confirmDelivery", () => {
    const DELIVERY_HASH = ethers.keccak256(ethers.toUtf8Bytes("video.mp4"));

    async function withAwarded() {
      const d = await deployAll();
      await d.brokerContract.connect(d.broker).createContract(
        AWARD_ID, REQ_ID, d.consumer.address, usdc(1_000)
      );
      await d.brokerContract.connect(d.broker).recordAward(AWARD_ID, d.producer.address);
      return d;
    }

    it("records delivery hash on-chain", async () => {
      const { brokerContract, broker } = await withAwarded();
      await brokerContract.connect(broker).confirmDelivery(AWARD_ID, DELIVERY_HASH);
      const c = await brokerContract.getContract(AWARD_ID);
      expect(c.deliveryHash).to.equal(DELIVERY_HASH);
      expect(c.status).to.equal(2n); // DELIVERED
    });

    it("emits DeliveryConfirmed", async () => {
      const { brokerContract, broker } = await withAwarded();
      await expect(
        brokerContract.connect(broker).confirmDelivery(AWARD_ID, DELIVERY_HASH)
      ).to.emit(brokerContract, "DeliveryConfirmed").withArgs(AWARD_ID, DELIVERY_HASH);
    });

    it("reverts with zero hash", async () => {
      const { brokerContract, broker } = await withAwarded();
      await expect(
        brokerContract.connect(broker).confirmDelivery(AWARD_ID, ethers.ZeroHash)
      ).to.be.revertedWith("BlackRiverBroker: invalid delivery hash");
    });
  });

  // ── settlePayment ──────────────────────────────────────────────────────
  describe("settlePayment", () => {
    const DELIVERY_HASH = ethers.keccak256(ethers.toUtf8Bytes("video.mp4"));

    async function withDelivered() {
      const d = await deployAll();
      await d.brokerContract.connect(d.broker).createContract(
        AWARD_ID, REQ_ID, d.consumer.address, usdc(1_000)
      );
      await d.brokerContract.connect(d.broker).recordAward(AWARD_ID, d.producer.address);
      await d.brokerContract.connect(d.broker).confirmDelivery(AWARD_ID, DELIVERY_HASH);
      return d;
    }

    it("releases USDC to producer", async () => {
      const { mockUSDC, brokerContract, broker, producer } = await withDelivered();
      const before = await mockUSDC.balanceOf(producer.address);
      await brokerContract.connect(broker).settlePayment(AWARD_ID);
      expect(await mockUSDC.balanceOf(producer.address)).to.equal(before + usdc(1_000));
    });

    it("emits PaymentSettled", async () => {
      const { brokerContract, broker, producer } = await withDelivered();
      await expect(
        brokerContract.connect(broker).settlePayment(AWARD_ID)
      ).to.emit(brokerContract, "PaymentSettled")
        .withArgs(AWARD_ID, producer.address, usdc(1_000));
    });

    it("reverts if not yet delivered", async () => {
      const d = await deployAll();
      await d.brokerContract.connect(d.broker).createContract(
        AWARD_ID, REQ_ID, d.consumer.address, usdc(500)
      );
      await d.brokerContract.connect(d.broker).recordAward(AWARD_ID, d.producer.address);
      await expect(
        d.brokerContract.connect(d.broker).settlePayment(AWARD_ID)
      ).to.be.revertedWith("BlackRiverBroker: not delivered");
    });
  });

  // ── cancelContract ─────────────────────────────────────────────────────
  describe("cancelContract", () => {
    it("refunds consumer and emits ContractCancelled", async () => {
      const { mockUSDC, brokerContract, broker, consumer } = await deployAll();
      await brokerContract.connect(broker).createContract(
        AWARD_ID, REQ_ID, consumer.address, usdc(800)
      );
      const before = await mockUSDC.balanceOf(consumer.address);
      await expect(
        brokerContract.connect(broker).cancelContract(AWARD_ID)
      ).to.emit(brokerContract, "ContractCancelled")
        .withArgs(AWARD_ID, consumer.address, usdc(800));
      expect(await mockUSDC.balanceOf(consumer.address)).to.equal(before + usdc(800));
    });

    it("reverts if already settled", async () => {
      const DELIVERY_HASH = ethers.keccak256(ethers.toUtf8Bytes("x"));
      const { brokerContract, broker, consumer, producer } = await deployAll();
      await brokerContract.connect(broker).createContract(AWARD_ID, REQ_ID, consumer.address, usdc(100));
      await brokerContract.connect(broker).recordAward(AWARD_ID, producer.address);
      await brokerContract.connect(broker).confirmDelivery(AWARD_ID, DELIVERY_HASH);
      await brokerContract.connect(broker).settlePayment(AWARD_ID);
      await expect(
        brokerContract.connect(broker).cancelContract(AWARD_ID)
      ).to.be.revertedWith("BlackRiverBroker: already finalised");
    });
  });

  // ── Full happy-path lifecycle ──────────────────────────────────────────
  describe("Full lifecycle", () => {
    it("create → award → deliver → settle: balances correct end-to-end", async () => {
      const { mockUSDC, brokerContract, broker, consumer, producer } = await deployAll();
      const HASH = ethers.keccak256(ethers.toUtf8Bytes("delivery"));
      const amt  = usdc(2_000);

      const consumerBefore  = await mockUSDC.balanceOf(consumer.address);
      const producerBefore  = await mockUSDC.balanceOf(producer.address);

      await brokerContract.connect(broker).createContract(AWARD_ID, REQ_ID, consumer.address, amt);
      await brokerContract.connect(broker).recordAward(AWARD_ID, producer.address);
      await brokerContract.connect(broker).confirmDelivery(AWARD_ID, HASH);
      await brokerContract.connect(broker).settlePayment(AWARD_ID);

      expect(await mockUSDC.balanceOf(consumer.address)).to.equal(consumerBefore - amt);
      expect(await mockUSDC.balanceOf(producer.address)).to.equal(producerBefore + amt);

      const c = await brokerContract.getContract(AWARD_ID);
      expect(c.status).to.equal(3n); // SETTLED
      expect(c.settledAt).to.be.gt(0n);
    });
  });
});
