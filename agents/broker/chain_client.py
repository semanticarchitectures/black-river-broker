"""
agents/broker/chain_client.py
──────────────────────────────
Thin wrapper around web3 / ethers for on-chain writes.

Phase 1: connects to local Hardhat node (http://127.0.0.1:8545)
Phase 2: swap RPC URL to Tempo testnet — no other changes needed.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from shared.cnp_messages import AuditEvent, ContractAward

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = Path(__file__).parents[2] / "artifacts" / "contracts"


class ChainClient:
    """
    Manages all interactions with the BlackRiverBroker and AuditLog contracts.
    """

    def __init__(
        self,
        rpc_url:          str = "http://127.0.0.1:8545",
        broker_wallet:    str = "",
        private_key:      str = "",
        audit_log_addr:   str = "",
        broker_contract_addr: str = "",
        usdc_addr:        str = "",
        dry_run:          bool = False,
    ):
        self.dry_run = dry_run
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        self.broker_wallet = broker_wallet
        self.private_key   = private_key

        if not dry_run:
            self._audit_log    = self._load_contract("AuditLog",         audit_log_addr)
            self._broker       = self._load_contract("BlackRiverBroker", broker_contract_addr)

        logger.info(f"[CHAIN] Connected to {rpc_url}  dry_run={dry_run}")

    # ── Contract loading ──────────────────────────────────────────────────

    def _load_contract(self, name: str, address: str) -> Any:
        abi_path = ARTIFACTS_DIR / f"{name}.sol" / f"{name}.json"
        if not abi_path.exists():
            logger.warning(f"[CHAIN] ABI not found at {abi_path} — run 'npx hardhat compile' first")
            return None
        with open(abi_path) as f:
            artifact = json.load(f)
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(address),
            abi=artifact["abi"],
        )

    # ── Transactions ──────────────────────────────────────────────────────

    def _send_tx(self, fn, *args) -> Dict:
        """Build, sign, and send a transaction. Returns receipt dict."""
        if self.dry_run:
            logger.info(f"[CHAIN DRY-RUN] {fn}({args})")
            return {"tx_hash": "0x" + "0" * 64, "block_number": 0}

        tx = fn(*args).build_transaction({
            "from":  self.broker_wallet,
            "nonce": self.w3.eth.get_transaction_count(self.broker_wallet),
            "gas":   500_000,
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        logger.info(f"[CHAIN] tx={tx_hash.hex()}  block={receipt['blockNumber']}")
        return {"tx_hash": tx_hash.hex(), "block_number": receipt["blockNumber"]}

    # ── AuditLog writes ───────────────────────────────────────────────────

    # Stage enum must match AuditLog.sol Stage enum order
    _STAGE_TO_INT = {
        "ANNOUNCED":  0,
        "BIDDING":    1,
        "EVALUATING": 2,
        "AWARDED":    3,
        "EXECUTING":  4,
        "DELIVERED":  5,
        "SETTLED":    6,
        "FAILED":     7,
    }

    def log_event(self, event: AuditEvent) -> None:
        """Write an AuditEvent to the on-chain AuditLog."""
        if self.dry_run:
            logger.info(f"[CHAIN DRY-RUN] log_event  stage={event.stage}  {event.summary[:60]}")
            return

        payload_hash_bytes = bytes.fromhex(event.payload_hash[:64].ljust(64, "0"))

        # actor may be "broker", a consumer/producer ID string, or an address.
        # On-chain we always record the broker wallet as the msg.sender proxy.
        actor_addr = Web3.to_checksum_address(self.broker_wallet)

        result = self._send_tx(
            self._audit_log.functions.logEvent,
            event.event_id,
            event.requirement_id,
            self._STAGE_TO_INT[event.stage.value],
            actor_addr,
            event.summary[:256],
            payload_hash_bytes,
        )
        event.tx_hash     = result.get("tx_hash")
        event.block_number= result.get("block_number")

    # ── BlackRiverBroker writes ───────────────────────────────────────────

    def record_award(self, award: ContractAward, consumer_wallet: str = "",
                     requirement_id: str = "") -> Dict:
        """
        Step 1: createContract (escrow) + Step 2: recordAward on-chain.

        The consumer must have pre-approved the broker contract to spend
        amountUsdc of USDC before this is called (done in deploy.ts).
        """
        if self.dry_run:
            logger.info(f"[CHAIN DRY-RUN] record_award  award={award.award_id}")
            return {"tx_hash": "0x" + "0" * 64, "contract_address": "0x" + "1" * 40}

        amount_units = int(award.price_usdc * 10 ** 6)  # USDC has 6 decimals

        # Step 1 — createContract (pulls USDC from consumer into escrow)
        consumer = Web3.to_checksum_address(
            consumer_wallet or self.broker_wallet  # fallback for Phase 1
        )
        req_id = requirement_id or award.requirement_id
        self._send_tx(
            self._broker.functions.createContract,
            award.award_id,
            req_id,
            consumer,
            amount_units,
        )

        # Step 2 — recordAward (marks winning producer)
        result = self._send_tx(
            self._broker.functions.recordAward,
            award.award_id,
            Web3.to_checksum_address(award.producer_wallet),
        )
        return {
            "tx_hash":          result["tx_hash"],
            "contract_address": self._broker.address,
        }

    def confirm_delivery(self, award_id: str, delivery_hash: str) -> None:
        """Record delivery hash on-chain."""
        if self.dry_run:
            logger.info(f"[CHAIN DRY-RUN] confirm_delivery  award={award_id}")
            return

        hash_bytes = bytes.fromhex(delivery_hash[:64].ljust(64, "0"))
        self._send_tx(
            self._broker.functions.confirmDelivery,
            award_id,
            hash_bytes,
        )
