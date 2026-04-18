"""
test/test_contracts.py
──────────────────────
Contract tests using eth-tester (in-process EVM).
No Hardhat node, no network access required.

Run:  pytest test/test_contracts.py -v
"""

import json
import os
import pytest

from eth_tester import EthereumTester, PyEVMBackend
from web3 import Web3
from web3.exceptions import ContractLogicError

# ── Load compiled artifacts ───────────────────────────────────────────────

ARTIFACTS = os.path.join(os.path.dirname(__file__), "..", "artifacts", "contracts")

def load_artifact(name: str) -> dict:
    path = os.path.join(ARTIFACTS, f"{name}.sol", f"{name}.json")
    with open(path) as f:
        return json.load(f)

MockUSDC_art   = load_artifact("MockUSDC")
AuditLog_art   = load_artifact("AuditLog")
Broker_art     = load_artifact("BlackRiverBroker")

# ── Helpers ───────────────────────────────────────────────────────────────

USDC_DEC = 6
def usdc(n: float) -> int:
    return int(n * 10 ** USDC_DEC)

AWARD_ID    = "AWD-TEST0001"
REQ_ID      = "REQ-TEST0001"
ZERO_HASH   = b"\x00" * 32
DLV_HASH    = Web3.keccak(text="video.mp4")
PAYLOAD_HASH= Web3.keccak(text="test-payload")

# AuditLog Stage enum — must match AuditLog.sol
class Stage:
    ANNOUNCED  = 0
    BIDDING    = 1
    EVALUATING = 2
    AWARDED    = 3
    EXECUTING  = 4
    DELIVERED  = 5
    SETTLED    = 6
    FAILED     = 7

# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def w3():
    tester = EthereumTester(PyEVMBackend())
    w3 = Web3(Web3.EthereumTesterProvider(tester))
    w3.eth.default_account = w3.eth.accounts[0]
    return w3

@pytest.fixture
def accounts(w3):
    # owner, broker, consumer, producer, stranger
    return w3.eth.accounts[:5]

@pytest.fixture
def mock_usdc(w3, accounts):
    owner = accounts[0]
    contract = w3.eth.contract(
        abi=MockUSDC_art["abi"],
        bytecode=MockUSDC_art["bytecode"],
    )
    tx_hash = contract.constructor(owner).transact({"from": owner})
    receipt = w3.eth.get_transaction_receipt(tx_hash)
    return w3.eth.contract(address=receipt["contractAddress"], abi=MockUSDC_art["abi"])

@pytest.fixture
def audit_log(w3, accounts):
    owner, broker_wallet = accounts[0], accounts[1]
    contract = w3.eth.contract(
        abi=AuditLog_art["abi"],
        bytecode=AuditLog_art["bytecode"],
    )
    tx_hash = contract.constructor().transact({"from": owner})
    receipt = w3.eth.get_transaction_receipt(tx_hash)
    deployed = w3.eth.contract(address=receipt["contractAddress"], abi=AuditLog_art["abi"])
    # Authorize the broker wallet so AuditLog tests can call logEvent
    deployed.functions.setAuthorised(broker_wallet, True).transact({"from": owner})
    return deployed

@pytest.fixture
def broker_contract(w3, accounts, mock_usdc, audit_log):
    owner, broker_wallet = accounts[0], accounts[1]

    # Deploy
    contract = w3.eth.contract(
        abi=Broker_art["abi"],
        bytecode=Broker_art["bytecode"],
    )
    tx_hash = contract.constructor(
        mock_usdc.address, audit_log.address, broker_wallet
    ).transact({"from": owner})
    receipt = w3.eth.get_transaction_receipt(tx_hash)
    deployed = w3.eth.contract(address=receipt["contractAddress"], abi=Broker_art["abi"])

    # Authorise broker contract + wallet to write AuditLog
    audit_log.functions.setAuthorised(deployed.address, True).transact({"from": owner})
    audit_log.functions.setAuthorised(broker_wallet, True).transact({"from": owner})

    # Mint + approve USDC for consumer
    consumer = accounts[2]
    mock_usdc.functions.mint(consumer, usdc(10_000)).transact({"from": owner})
    mock_usdc.functions.approve(deployed.address, usdc(10_000)).transact({"from": consumer})

    return deployed


# ═════════════════════════════════════════════════════════════════════════
# MockUSDC
# ═════════════════════════════════════════════════════════════════════════

class TestMockUSDC:

    def test_decimals(self, mock_usdc):
        assert mock_usdc.functions.decimals().call() == 6

    def test_owner_can_mint(self, w3, accounts, mock_usdc):
        owner, _, consumer = accounts[:3]
        mock_usdc.functions.mint(consumer, usdc(500)).transact({"from": owner})
        assert mock_usdc.functions.balanceOf(consumer).call() == usdc(500)

    def test_non_owner_cannot_mint(self, w3, accounts, mock_usdc):
        stranger = accounts[4]
        with pytest.raises(Exception, match="revert|Unauthorized|unauthorized"):
            mock_usdc.functions.mint(stranger, usdc(100)).transact({"from": stranger})

    def test_faucet_mints_1000_usdc(self, w3, accounts, mock_usdc):
        stranger = accounts[4]
        mock_usdc.functions.faucet().transact({"from": stranger})
        assert mock_usdc.functions.balanceOf(stranger).call() == usdc(1_000)

    def test_erc20_transfer(self, w3, accounts, mock_usdc):
        owner, _, consumer, _, stranger = accounts
        mock_usdc.functions.mint(consumer, usdc(200)).transact({"from": owner})
        mock_usdc.functions.transfer(stranger, usdc(50)).transact({"from": consumer})
        assert mock_usdc.functions.balanceOf(stranger).call() == usdc(50)


# ═════════════════════════════════════════════════════════════════════════
# AuditLog
# ═════════════════════════════════════════════════════════════════════════

class TestAuditLog:

    def test_owner_authorised_by_default(self, accounts, audit_log):
        owner = accounts[0]
        assert audit_log.functions.authorised(owner).call() is True

    def test_authorised_can_log_event(self, accounts, audit_log):
        broker_wallet = accounts[1]
        audit_log.functions.logEvent(
            "EVT-A", REQ_ID, Stage.ANNOUNCED,
            broker_wallet, "Announced", PAYLOAD_HASH,
        ).transact({"from": broker_wallet})
        assert audit_log.functions.totalEvents().call() == 1

    def test_get_event_returns_correct_data(self, accounts, audit_log):
        broker_wallet = accounts[1]
        audit_log.functions.logEvent(
            "EVT-A", REQ_ID, Stage.AWARDED,
            broker_wallet, "Award issued", PAYLOAD_HASH,
        ).transact({"from": broker_wallet})
        ev = audit_log.functions.getEvent(0).call()
        assert ev[0] == "EVT-A"          # eventId
        assert ev[1] == REQ_ID            # requirementId
        assert ev[2] == Stage.AWARDED     # stage

    def test_get_events_by_requirement_filters_correctly(self, accounts, audit_log):
        broker_wallet = accounts[1]
        for stage, eid in [(Stage.ANNOUNCED, "EVT-A"), (Stage.BIDDING, "EVT-B")]:
            audit_log.functions.logEvent(
                eid, REQ_ID, stage, broker_wallet, "msg", PAYLOAD_HASH,
            ).transact({"from": broker_wallet})
        # Different requirement
        audit_log.functions.logEvent(
            "EVT-C", "REQ-OTHER", Stage.ANNOUNCED,
            broker_wallet, "other", PAYLOAD_HASH,
        ).transact({"from": broker_wallet})

        evs = audit_log.functions.getEventsByRequirement(REQ_ID).call()
        assert len(evs) == 2
        assert evs[0][0] == "EVT-A"
        assert evs[1][0] == "EVT-B"

    def test_unauthorised_cannot_log(self, accounts, audit_log):
        stranger = accounts[4]
        with pytest.raises(Exception, match="not authorised"):
            audit_log.functions.logEvent(
                "EVT-X", REQ_ID, Stage.ANNOUNCED,
                stranger, "bad", PAYLOAD_HASH,
            ).transact({"from": stranger})

    def test_get_event_reverts_out_of_range(self, audit_log):
        with pytest.raises(Exception, match="out of range"):
            audit_log.functions.getEvent(99).call()

    def test_owner_can_revoke_authorisation(self, accounts, audit_log):
        owner, broker_wallet = accounts[0], accounts[1]
        audit_log.functions.setAuthorised(broker_wallet, False).transact({"from": owner})
        assert audit_log.functions.authorised(broker_wallet).call() is False


# ═════════════════════════════════════════════════════════════════════════
# BlackRiverBroker
# ═════════════════════════════════════════════════════════════════════════

class TestBlackRiverBroker:

    # ── createContract ─────────────────────────────────────────────────────

    def test_create_escrows_usdc(self, accounts, mock_usdc, broker_contract):
        _, broker_wallet, consumer, *_ = accounts
        before = mock_usdc.functions.balanceOf(consumer).call()
        broker_contract.functions.createContract(
            AWARD_ID, REQ_ID, consumer, usdc(1_000)
        ).transact({"from": broker_wallet})
        assert mock_usdc.functions.balanceOf(consumer).call() == before - usdc(1_000)
        assert mock_usdc.functions.balanceOf(broker_contract.address).call() == usdc(1_000)

    def test_create_emits_contract_created(self, w3, accounts, broker_contract):
        _, broker_wallet, consumer, *_ = accounts
        tx = broker_contract.functions.createContract(
            AWARD_ID, REQ_ID, consumer, usdc(500)
        ).transact({"from": broker_wallet})
        receipt = w3.eth.get_transaction_receipt(tx)
        logs = broker_contract.events.ContractCreated().process_receipt(receipt)
        assert len(logs) == 1
        # awardId is indexed (stored as keccak hash), check non-indexed fields instead
        assert logs[0]["args"]["requirementId"] == REQ_ID
        assert logs[0]["args"]["consumer"] == consumer
        assert logs[0]["args"]["amountUsdc"] == usdc(500)

    def test_create_reverts_duplicate_award_id(self, accounts, broker_contract):
        _, broker_wallet, consumer, *_ = accounts
        broker_contract.functions.createContract(
            AWARD_ID, REQ_ID, consumer, usdc(100)
        ).transact({"from": broker_wallet})
        with pytest.raises(Exception, match="already exists"):
            broker_contract.functions.createContract(
                AWARD_ID, REQ_ID, consumer, usdc(100)
            ).transact({"from": broker_wallet})

    def test_create_reverts_zero_amount(self, accounts, broker_contract):
        _, broker_wallet, consumer, *_ = accounts
        with pytest.raises(Exception, match="amount must be"):
            broker_contract.functions.createContract(
                AWARD_ID, REQ_ID, consumer, 0
            ).transact({"from": broker_wallet})

    def test_create_reverts_non_broker(self, accounts, broker_contract):
        _, _, consumer, _, stranger = accounts
        with pytest.raises(Exception, match="not the broker"):
            broker_contract.functions.createContract(
                AWARD_ID, REQ_ID, consumer, usdc(100)
            ).transact({"from": stranger})

    # ── recordAward ────────────────────────────────────────────────────────

    def test_record_award_sets_producer_and_status(self, accounts, broker_contract):
        _, broker_wallet, consumer, producer, _ = accounts
        broker_contract.functions.createContract(
            AWARD_ID, REQ_ID, consumer, usdc(500)
        ).transact({"from": broker_wallet})
        broker_contract.functions.recordAward(
            AWARD_ID, producer
        ).transact({"from": broker_wallet})
        c = broker_contract.functions.getContract(AWARD_ID).call()
        assert c[3] == producer         # producer address  (struct[3])
        assert c[6] == 1                # status = AWARDED  (struct[6])

    def test_record_award_reverts_missing_contract(self, accounts, broker_contract):
        _, broker_wallet, _, producer, _ = accounts
        with pytest.raises(Exception, match="not found"):
            broker_contract.functions.recordAward(
                "AWD-MISSING", producer
            ).transact({"from": broker_wallet})

    # ── confirmDelivery ────────────────────────────────────────────────────

    def test_confirm_delivery_stores_hash(self, accounts, broker_contract):
        _, broker_wallet, consumer, producer, _ = accounts
        broker_contract.functions.createContract(AWARD_ID, REQ_ID, consumer, usdc(500)).transact({"from": broker_wallet})
        broker_contract.functions.recordAward(AWARD_ID, producer).transact({"from": broker_wallet})
        broker_contract.functions.confirmDelivery(AWARD_ID, DLV_HASH).transact({"from": broker_wallet})
        c = broker_contract.functions.getContract(AWARD_ID).call()
        assert c[5] == DLV_HASH         # deliveryHash       (struct[5])
        assert c[6] == 2                # status = DELIVERED (struct[6])

    def test_confirm_delivery_reverts_zero_hash(self, accounts, broker_contract):
        _, broker_wallet, consumer, producer, _ = accounts
        broker_contract.functions.createContract(AWARD_ID, REQ_ID, consumer, usdc(100)).transact({"from": broker_wallet})
        broker_contract.functions.recordAward(AWARD_ID, producer).transact({"from": broker_wallet})
        with pytest.raises(Exception, match="invalid delivery hash"):
            broker_contract.functions.confirmDelivery(AWARD_ID, ZERO_HASH).transact({"from": broker_wallet})

    # ── settlePayment ──────────────────────────────────────────────────────

    def test_settle_releases_usdc_to_producer(self, accounts, mock_usdc, broker_contract):
        _, broker_wallet, consumer, producer, _ = accounts
        broker_contract.functions.createContract(AWARD_ID, REQ_ID, consumer, usdc(1_000)).transact({"from": broker_wallet})
        broker_contract.functions.recordAward(AWARD_ID, producer).transact({"from": broker_wallet})
        broker_contract.functions.confirmDelivery(AWARD_ID, DLV_HASH).transact({"from": broker_wallet})
        before = mock_usdc.functions.balanceOf(producer).call()
        broker_contract.functions.settlePayment(AWARD_ID).transact({"from": broker_wallet})
        assert mock_usdc.functions.balanceOf(producer).call() == before + usdc(1_000)

    def test_settle_reverts_if_not_delivered(self, accounts, broker_contract):
        _, broker_wallet, consumer, producer, _ = accounts
        broker_contract.functions.createContract(AWARD_ID, REQ_ID, consumer, usdc(100)).transact({"from": broker_wallet})
        broker_contract.functions.recordAward(AWARD_ID, producer).transact({"from": broker_wallet})
        with pytest.raises(Exception, match="not delivered"):
            broker_contract.functions.settlePayment(AWARD_ID).transact({"from": broker_wallet})

    # ── cancelContract ─────────────────────────────────────────────────────

    def test_cancel_refunds_consumer(self, accounts, mock_usdc, broker_contract):
        _, broker_wallet, consumer, *_ = accounts
        broker_contract.functions.createContract(AWARD_ID, REQ_ID, consumer, usdc(800)).transact({"from": broker_wallet})
        before = mock_usdc.functions.balanceOf(consumer).call()
        broker_contract.functions.cancelContract(AWARD_ID).transact({"from": broker_wallet})
        assert mock_usdc.functions.balanceOf(consumer).call() == before + usdc(800)

    def test_cancel_reverts_after_settle(self, accounts, broker_contract):
        _, broker_wallet, consumer, producer, _ = accounts
        broker_contract.functions.createContract(AWARD_ID, REQ_ID, consumer, usdc(100)).transact({"from": broker_wallet})
        broker_contract.functions.recordAward(AWARD_ID, producer).transact({"from": broker_wallet})
        broker_contract.functions.confirmDelivery(AWARD_ID, DLV_HASH).transact({"from": broker_wallet})
        broker_contract.functions.settlePayment(AWARD_ID).transact({"from": broker_wallet})
        with pytest.raises(Exception, match="already finalised"):
            broker_contract.functions.cancelContract(AWARD_ID).transact({"from": broker_wallet})

    # ── Full lifecycle ─────────────────────────────────────────────────────

    def test_full_lifecycle_end_to_end(self, accounts, mock_usdc, broker_contract):
        _, broker_wallet, consumer, producer, _ = accounts
        amt = usdc(2_000)

        consumer_before = mock_usdc.functions.balanceOf(consumer).call()
        producer_before = mock_usdc.functions.balanceOf(producer).call()

        broker_contract.functions.createContract(AWARD_ID, REQ_ID, consumer, amt).transact({"from": broker_wallet})
        broker_contract.functions.recordAward(AWARD_ID, producer).transact({"from": broker_wallet})
        broker_contract.functions.confirmDelivery(AWARD_ID, DLV_HASH).transact({"from": broker_wallet})
        broker_contract.functions.settlePayment(AWARD_ID).transact({"from": broker_wallet})

        assert mock_usdc.functions.balanceOf(consumer).call() == consumer_before - amt
        assert mock_usdc.functions.balanceOf(producer).call() == producer_before + amt

        c = broker_contract.functions.getContract(AWARD_ID).call()
        assert c[6] == 3            # status = SETTLED  (struct[6])
        assert c[8] > 0             # settledAt set     (struct[8])
