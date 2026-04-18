// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "./AuditLog.sol";

/**
 * @title BlackRiverBroker
 * @notice Enforces the CNP award and payment lifecycle on-chain.
 *
 * Lifecycle
 * ─────────
 *   1. Broker calls createContract()  — consumer deposits funds in escrow.
 *   2. Broker calls recordAward()     — winning producer is recorded.
 *   3. Broker calls confirmDelivery() — delivery hash is verified.
 *   4. Broker calls settlePayment()   — escrowed funds released to producer.
 *
 *   At any point, the broker can call cancelContract() to refund the consumer
 *   (e.g., no qualifying bids, producer withdrawal, delivery failure).
 *
 * Payment model
 * ─────────────
 *   Consumer approves this contract to spend USDC before calling
 *   createContract().  Funds are held in escrow until settlePayment().
 *   The broker does not hold funds — the contract is the escrow.
 *
 * Phase note
 * ──────────
 *   In Phase 1, USDC is the MockUSDC token on the local Hardhat chain.
 *   In Phase 2+, USDC is the real TIP-20 token on Tempo testnet/mainnet.
 *   No contract changes are required between phases — only the token
 *   address passed to the constructor changes.
 */
contract BlackRiverBroker is Ownable, ReentrancyGuard {

    // ── Enumerations ──────────────────────────────────────────────────────

    enum ContractStatus {
        CREATED,      // funds escrowed, award pending
        AWARDED,      // winning producer recorded
        DELIVERED,    // delivery hash confirmed on-chain
        SETTLED,      // payment released to producer
        CANCELLED     // contract cancelled, consumer refunded
    }

    // ── Storage structs ───────────────────────────────────────────────────

    struct ProcurementContract {
        string          requirementId;
        string          awardId;
        address         consumer;
        address         producer;
        uint256         amountUsdc;     // in token's base units (6 decimals for USDC)
        bytes32         deliveryHash;   // keccak256 of delivery evidence
        ContractStatus  status;
        uint256         createdAt;
        uint256         settledAt;
    }

    // ── State ─────────────────────────────────────────────────────────────

    IERC20   public immutable usdc;
    AuditLog public immutable auditLog;

    /// awardId → ProcurementContract
    mapping(string => ProcurementContract) public contracts;

    /// Broker address — the Python agent's wallet
    address public broker;

    // ── Events ────────────────────────────────────────────────────────────

    event ContractCreated(string indexed awardId, string requirementId,
                          address consumer, uint256 amountUsdc);
    event ContractAwarded(string indexed awardId, address producer);
    event DeliveryConfirmed(string indexed awardId, bytes32 deliveryHash);
    event PaymentSettled(string indexed awardId, address producer, uint256 amountUsdc);
    event ContractCancelled(string indexed awardId, address consumer, uint256 refundAmount);

    // ── Modifiers ─────────────────────────────────────────────────────────

    modifier onlyBroker() {
        require(msg.sender == broker, "BlackRiverBroker: caller is not the broker");
        _;
    }

    modifier contractExists(string calldata awardId) {
        require(contracts[awardId].createdAt != 0, "BlackRiverBroker: contract not found");
        _;
    }

    // ── Constructor ───────────────────────────────────────────────────────

    /**
     * @param _usdc       Address of the USDC token (MockUSDC in Phase 1)
     * @param _auditLog   Address of the deployed AuditLog contract
     * @param _broker     Address of the broker agent's wallet
     */
    constructor(address _usdc, address _auditLog, address _broker)
        Ownable(msg.sender)
    {
        usdc     = IERC20(_usdc);
        auditLog = AuditLog(_auditLog);
        broker   = _broker;
    }

    // ── Admin ─────────────────────────────────────────────────────────────

    function setBroker(address _broker) external onlyOwner {
        broker = _broker;
    }

    // ── Lifecycle ─────────────────────────────────────────────────────────

    /**
     * @notice Step 1 — create escrow for a procurement contract.
     * @dev Consumer must approve this contract for amountUsdc before calling.
     *
     * @param awardId       Unique award identifier from the Python layer
     * @param requirementId Source requirement identifier
     * @param consumer      Consumer's wallet address
     * @param amountUsdc    Amount to escrow (in USDC base units, 6 decimals)
     */
    function createContract(
        string  calldata awardId,
        string  calldata requirementId,
        address          consumer,
        uint256          amountUsdc
    ) external onlyBroker nonReentrant {
        require(contracts[awardId].createdAt == 0, "BlackRiverBroker: award already exists");
        require(amountUsdc > 0, "BlackRiverBroker: amount must be > 0");

        // Pull funds from consumer into escrow
        require(
            usdc.transferFrom(consumer, address(this), amountUsdc),
            "BlackRiverBroker: USDC transfer failed"
        );

        contracts[awardId] = ProcurementContract({
            requirementId: requirementId,
            awardId:       awardId,
            consumer:      consumer,
            producer:      address(0),
            amountUsdc:    amountUsdc,
            deliveryHash:  bytes32(0),
            status:        ContractStatus.CREATED,
            createdAt:     block.timestamp,
            settledAt:     0
        });

        emit ContractCreated(awardId, requirementId, consumer, amountUsdc);
    }

    /**
     * @notice Step 2 — record the winning producer.
     */
    function recordAward(
        string  calldata awardId,
        address          producer
    ) external onlyBroker contractExists(awardId) {
        ProcurementContract storage c = contracts[awardId];
        require(c.status == ContractStatus.CREATED, "BlackRiverBroker: invalid status");

        c.producer = producer;
        c.status   = ContractStatus.AWARDED;

        emit ContractAwarded(awardId, producer);
    }

    /**
     * @notice Step 3 — record the delivery hash on-chain.
     * @param deliveryHash keccak256 of the delivery evidence (e.g. video URL)
     */
    function confirmDelivery(
        string  calldata awardId,
        bytes32          deliveryHash
    ) external onlyBroker contractExists(awardId) {
        ProcurementContract storage c = contracts[awardId];
        require(c.status == ContractStatus.AWARDED, "BlackRiverBroker: invalid status");
        require(deliveryHash != bytes32(0), "BlackRiverBroker: invalid delivery hash");

        c.deliveryHash = deliveryHash;
        c.status       = ContractStatus.DELIVERED;

        emit DeliveryConfirmed(awardId, deliveryHash);
    }

    /**
     * @notice Step 4 — release escrowed funds to the producer.
     */
    function settlePayment(
        string calldata awardId
    ) external onlyBroker nonReentrant contractExists(awardId) {
        ProcurementContract storage c = contracts[awardId];
        require(c.status == ContractStatus.DELIVERED, "BlackRiverBroker: not delivered");

        uint256 amount   = c.amountUsdc;
        address producer = c.producer;

        c.status    = ContractStatus.SETTLED;
        c.settledAt = block.timestamp;

        require(
            usdc.transfer(producer, amount),
            "BlackRiverBroker: payment transfer failed"
        );

        emit PaymentSettled(awardId, producer, amount);
    }

    /**
     * @notice Cancel the contract at any pre-settlement stage and refund consumer.
     */
    function cancelContract(
        string calldata awardId
    ) external onlyBroker nonReentrant contractExists(awardId) {
        ProcurementContract storage c = contracts[awardId];
        require(
            c.status != ContractStatus.SETTLED &&
            c.status != ContractStatus.CANCELLED,
            "BlackRiverBroker: already finalised"
        );

        uint256 refund   = c.amountUsdc;
        address consumer = c.consumer;

        c.status = ContractStatus.CANCELLED;

        require(
            usdc.transfer(consumer, refund),
            "BlackRiverBroker: refund transfer failed"
        );

        emit ContractCancelled(awardId, consumer, refund);
    }

    // ── View ──────────────────────────────────────────────────────────────

    function getContract(string calldata awardId)
        external view returns (ProcurementContract memory)
    {
        return contracts[awardId];
    }
}
