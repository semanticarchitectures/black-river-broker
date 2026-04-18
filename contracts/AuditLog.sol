// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/**
 * @title AuditLog
 * @notice Immutable on-chain record of every CNP stage transition.
 *
 * The broker writes one event per stage: ANNOUNCED, BIDDING, EVALUATING,
 * AWARDED, EXECUTING, DELIVERED, SETTLED (or FAILED).
 *
 * Events are append-only and cannot be modified or deleted, providing
 * the tamper-evident audit trail that is central to Black River's
 * value proposition.
 *
 * Anyone can read the full event history for any requirement — the
 * audit trail is public by design.
 */
contract AuditLog {

    // ── Stage enum mirrors CNPStage in cnp_messages.py ────────────────────
    enum Stage {
        ANNOUNCED,
        BIDDING,
        EVALUATING,
        AWARDED,
        EXECUTING,
        DELIVERED,
        SETTLED,
        FAILED
    }

    // ── Event record ──────────────────────────────────────────────────────
    struct AuditEvent {
        string  eventId;        // e.g. "EVT-3F2A1B4C"
        string  requirementId;  // e.g. "REQ-8A4D2E1F"
        Stage   stage;
        address actor;          // broker or producer address
        string  summary;        // human-readable description
        bytes32 payloadHash;    // keccak256 of the triggering message
        uint256 blockTimestamp;
    }

    // ── Storage ───────────────────────────────────────────────────────────

    /// All events, in append order
    AuditEvent[] private _events;

    /// requirement_id → indices into _events[]
    mapping(string => uint256[]) private _byRequirement;

    /// Authorised writers (broker contract address)
    mapping(address => bool) public authorised;

    address public owner;

    // ── Events (EVM events for indexing) ─────────────────────────────────

    event EventLogged(
        uint256 indexed index,
        string  indexed requirementId,
        Stage           stage,
        address         actor,
        string          eventId,
        bytes32         payloadHash,
        uint256         blockTimestamp
    );

    event AuthorisedWriter(address indexed writer, bool status);

    // ── Modifiers ─────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "AuditLog: not owner");
        _;
    }

    modifier onlyAuthorised() {
        require(authorised[msg.sender], "AuditLog: not authorised writer");
        _;
    }

    // ── Constructor ───────────────────────────────────────────────────────

    constructor() {
        owner = msg.sender;
        authorised[msg.sender] = true;
    }

    // ── Admin ─────────────────────────────────────────────────────────────

    function setAuthorised(address writer, bool status) external onlyOwner {
        authorised[writer] = status;
        emit AuthorisedWriter(writer, status);
    }

    // ── Write ─────────────────────────────────────────────────────────────

    /**
     * @notice Append a new audit event.  Only authorised addresses may write.
     * @param eventId       Unique event identifier from the Python layer
     * @param requirementId Procurement requirement this event belongs to
     * @param stage         CNP stage being recorded
     * @param actor         Address of the agent recording the event
     * @param summary       Human-readable description
     * @param payloadHash   keccak256 of the full message payload (off-chain)
     */
    function logEvent(
        string  calldata eventId,
        string  calldata requirementId,
        Stage            stage,
        address          actor,
        string  calldata summary,
        bytes32          payloadHash
    ) external onlyAuthorised returns (uint256 index) {
        index = _events.length;

        _events.push(AuditEvent({
            eventId:        eventId,
            requirementId:  requirementId,
            stage:          stage,
            actor:          actor,
            summary:        summary,
            payloadHash:    payloadHash,
            blockTimestamp: block.timestamp
        }));

        _byRequirement[requirementId].push(index);

        emit EventLogged(
            index,
            requirementId,
            stage,
            actor,
            eventId,
            payloadHash,
            block.timestamp
        );
    }

    // ── Read ──────────────────────────────────────────────────────────────

    function getEvent(uint256 index) external view returns (AuditEvent memory) {
        require(index < _events.length, "AuditLog: index out of range");
        return _events[index];
    }

    function totalEvents() external view returns (uint256) {
        return _events.length;
    }

    function getEventsByRequirement(string calldata requirementId)
        external view returns (AuditEvent[] memory)
    {
        uint256[] storage indices = _byRequirement[requirementId];
        AuditEvent[] memory result = new AuditEvent[](indices.length);
        for (uint256 i = 0; i < indices.length; i++) {
            result[i] = _events[indices[i]];
        }
        return result;
    }
}
