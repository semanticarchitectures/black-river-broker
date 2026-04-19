"""
agents/broker/chain_client.py
──────────────────────────────
Thin wrapper around web3 / pytempo for on-chain writes.

Phase 1 : connects to local Hardhat node — uses plain web3.py (standard EVM).
Phase 2+: connects to Tempo testnet — uses pytempo.TempoTransaction so that
          every write carries the explicit `fee_token` field required by Tempo's
          stablecoin gas system.

Reads (eth_call) always use plain web3.py — no fee token needed.

Fee token (TEMPO_FEE_TOKEN env var, default AlphaUSD testnet):
  0x20c0000000000000000000000000000000000001
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

# AlphaUSD on Tempo testnet — the default fee token.
# Override with TEMPO_FEE_TOKEN env var.
DEFAULT_FEE_TOKEN = "0x20c0000000000000000000000000000000000001"

# Tempo Moderato testnet chain ID
TEMPO_CHAIN_ID = 42431

# Hardhat local chain ID
HARDHAT_CHAIN_ID = 31337


class ChainClient:
    """
    Manages all interactions with the BlackRiverBroker and AuditLog contracts.

    On Tempo testnet, every write is sent as a Tempo Transaction (pytempo)
    with an explicit fee_token, because Tempo has no native gas token.

    On local Hardhat, plain web3.py EIP-1559 transactions are used.
    """

    def __init__(
        self,
        rpc_url:          str = "http://127.0.0.1:8545",
        broker_wallet:    str = "",
        private_key:      str = "",
        audit_log_addr:   str = "",
        broker_contract_addr: str = "",
        usdc_addr:        str = "",
        fee_token:        str = "",
        dry_run:          bool = False,
    ):
        self.dry_run      = dry_run
        self.rpc_url      = rpc_url
        self.broker_wallet = broker_wallet
        self.private_key   = private_key
        self.fee_token     = (
            fee_token
            or os.getenv("TEMPO_FEE_TOKEN", DEFAULT_FEE_TOKEN)
        )

        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        # Detect which chain we're on
        try:
            self.chain_id = self.w3.eth.chain_id
        except Exception:
            self.chain_id = HARDHAT_CHAIN_ID

        self._is_tempo = self.chain_id in (TEMPO_CHAIN_ID,)
        self._pytempo_available = self._check_pytempo()

        if not dry_run:
            self._audit_log = self._load_contract("AuditLog",         audit_log_addr)
            self._broker    = self._load_contract("BlackRiverBroker", broker_contract_addr)

        logger.info(
            f"[CHAIN] Connected to {rpc_url}  chain={self.chain_id}  "
            f"tempo={'yes (pytempo)' if (self._is_tempo and self._pytempo_available) else 'no (web3)'}"
            f"  dry_run={dry_run}"
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _check_pytempo() -> bool:
        try:
            import pytempo  # noqa: F401
            return True
        except ImportError:
            return False

    def _load_contract(self, name: str, address: str) -> Any:
        abi_path = ARTIFACTS_DIR / f"{name}.sol" / f"{name}.json"
        if not abi_path.exists():
            logger.warning(f"[CHAIN] ABI not found at {abi_path} — run compile_local.js first")
            return None
        with open(abi_path) as f:
            artifact = json.load(f)
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(address),
            abi=artifact["abi"],
        )

    # ── Transaction sender ────────────────────────────────────────────────────

    def _send_tx(self, fn, *args) -> Dict:
        """
        Build, sign, and broadcast a transaction.

        On Tempo testnet with pytempo installed: sends a Tempo Transaction
        with the configured fee_token (stablecoin gas).

        On local Hardhat or when pytempo is unavailable: sends a standard
        EIP-1559 transaction with dynamic gas estimation.

        Returns {"tx_hash": str, "block_number": int}.
        """
        if self.dry_run:
            logger.info(f"[CHAIN DRY-RUN] {fn.__name__ if hasattr(fn, '__name__') else fn}({args})")
            return {"tx_hash": "0x" + "0" * 64, "block_number": 0}

        if self._is_tempo and self._pytempo_available:
            return self._send_tempo_tx(fn, *args)
        else:
            return self._send_evm_tx(fn, *args)

    def _send_tempo_tx(self, fn, *args) -> Dict:
        """Send a Tempo Transaction (stablecoin gas) via pytempo."""
        from pytempo import TempoTransaction

        call      = fn(*args)
        # Build calldata without broadcasting
        tx_params = call.build_transaction({
            "from":  self.broker_wallet,
            "nonce": 0,          # dummy — TempoTransaction manages nonce
            "gas":   500_000,
        })
        calldata         = tx_params["data"]
        contract_address = tx_params["to"]
        nonce = self.w3.eth.get_transaction_count(self.broker_wallet)

        tempo_tx = TempoTransaction.create(
            chain_id=   self.chain_id,
            gas_limit=  500_000,
            fee_token=  Web3.to_checksum_address(self.fee_token),
            nonce=      nonce,
            calls=[{
                "to":    contract_address,
                "data":  calldata,
                "value": 0,
            }],
        )

        signed  = tempo_tx.sign(self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        logger.info(f"[CHAIN/TEMPO] tx={tx_hash.hex()}  block={receipt['blockNumber']}")
        return {"tx_hash": tx_hash.hex(), "block_number": receipt["blockNumber"]}

    def _send_evm_tx(self, fn, *args) -> Dict:
        """Send a standard EIP-1559 transaction (Hardhat / generic EVM)."""
        call = fn(*args)
        try:
            gas_estimate = call.estimate_gas({"from": self.broker_wallet})
            gas_limit    = int(gas_estimate * 1.25)
        except Exception:
            gas_limit    = 500_000

        tx = call.build_transaction({
            "from":  self.broker_wallet,
            "nonce": self.w3.eth.get_transaction_count(self.broker_wallet),
            "gas":   gas_limit,
        })
        signed  = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        logger.info(f"[CHAIN/EVM] tx={tx_hash.hex()}  block={receipt['blockNumber']}")
        return {"tx_hash": tx_hash.hex(), "block_number": receipt["blockNumber"]}

    # ── AuditLog writes ───────────────────────────────────────────────────────

    _STAGE_TO_INT = {
        "ANNOUNCED":  0, "BIDDING":  1, "EVALUATING": 2, "AWARDED": 3,
        "EXECUTING":  4, "DELIVERED": 5, "SETTLED":    6, "FAILED":  7,
    }

    def log_event(self, event: AuditEvent) -> None:
        if self.dry_run:
            logger.info(f"[CHAIN DRY-RUN] log_event  stage={event.stage}  {event.summary[:60]}")
            return

        raw_hash = event.payload_hash.lstrip("0x") if event.payload_hash.startswith("0x") else event.payload_hash
        payload_hash_bytes = bytes.fromhex(raw_hash[:64].ljust(64, "0"))
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

    # ── BlackRiverBroker writes ───────────────────────────────────────────────

    def record_award(self, award: ContractAward, consumer_wallet: str = "",
                     requirement_id: str = "") -> Dict:
        if self.dry_run:
            logger.info(f"[CHAIN DRY-RUN] record_award  award={award.award_id}")
            return {"tx_hash": "0x" + "0" * 64, "contract_address": "0x" + "1" * 40}

        amount_units = int(award.price_usdc * 10 ** 6)
        consumer     = Web3.to_checksum_address(consumer_wallet or self.broker_wallet)
        req_id       = requirement_id or award.requirement_id

        self._send_tx(
            self._broker.functions.createContract,
            award.award_id, req_id, consumer, amount_units,
        )
        result = self._send_tx(
            self._broker.functions.recordAward,
            award.award_id,
            Web3.to_checksum_address(award.producer_wallet),
        )
        return {"tx_hash": result["tx_hash"], "contract_address": self._broker.address}

    def settle_payment(self, award_id: str) -> Dict:
        if self.dry_run:
            logger.info(f"[CHAIN DRY-RUN] settle_payment  award={award_id}")
            return {"tx_hash": "0x" + "0" * 64, "block_number": 0}
        return self._send_tx(self._broker.functions.settlePayment, award_id)

    def cancel_contract(self, award_id: str) -> Dict:
        if self.dry_run:
            logger.info(f"[CHAIN DRY-RUN] cancel_contract  award={award_id}")
            return {"tx_hash": "0x" + "0" * 64, "block_number": 0}
        return self._send_tx(self._broker.functions.cancelContract, award_id)

    def confirm_delivery(self, award_id: str, delivery_hash: str) -> None:
        if self.dry_run:
            logger.info(f"[CHAIN DRY-RUN] confirm_delivery  award={award_id}")
            return
        raw        = delivery_hash.lstrip("0x") if delivery_hash.startswith("0x") else delivery_hash
        hash_bytes = bytes.fromhex(raw[:64].ljust(64, "0"))
        self._send_tx(self._broker.functions.confirmDelivery, award_id, hash_bytes)
