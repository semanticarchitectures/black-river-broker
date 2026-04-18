"""
agents/mpp/mpp_simulator.py
─────────────────────────────
Phase 1 in-process MPP payment simulator.

Uses the real MPP message structure but settles against the mock USDC
token on the local Hardhat chain.  In Phase 2, this class is replaced
by a real MPP client that calls Tempo's Payment authentication scheme.

The interface (settle()) stays identical — the broker_graph.py never
needs to change.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime

from shared.cnp_messages import PaymentSettlement

logger = logging.getLogger(__name__)


class MPPSimulator:
    """
    Simulates the Tempo Machine Payments Protocol (MPP) payment flow.

    In Phase 1, this records a simulated payment transaction.
    In Phase 2, replace this class with a real MPP client that:
      - Uses the 'Payment' HTTP authentication scheme
      - Submits a TIP-20 transfer on Tempo testnet
      - Returns the real transaction hash and block number
    """

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self._settlements: dict[str, PaymentSettlement] = {}

    def settle(
        self,
        award_id:        str,
        producer_wallet: str,
        amount_usdc:     float,
        memo:            str,
    ) -> PaymentSettlement:
        """
        Execute payment settlement.

        Args:
            award_id:        Unique award identifier (carried in TIP-20 memo)
            producer_wallet: Producer's on-chain address
            amount_usdc:     Settlement amount in USDC
            memo:            32-byte memo payload (award_id:requirement_id)

        Returns:
            PaymentSettlement with simulated (Phase 1) or real (Phase 2) tx_hash
        """
        # Simulate a transaction hash
        tx_input = f"{award_id}:{producer_wallet}:{amount_usdc}:{memo}:{uuid.uuid4()}"
        tx_hash  = "0x" + hashlib.sha256(tx_input.encode()).hexdigest()

        settlement = PaymentSettlement(
            award_id=       award_id,
            producer_wallet=producer_wallet,
            amount_usdc=    amount_usdc,
            tx_hash=        tx_hash,
            memo=           memo[:32],   # TIP-20 memo is 32 bytes
            settled_at=     datetime.utcnow(),
        )

        self._settlements[award_id] = settlement

        logger.info(
            f"[MPP {'SIM' if self.dry_run else 'LIVE'}] "
            f"Settled ${amount_usdc:.2f} USDC → {producer_wallet[:10]}...  "
            f"tx={tx_hash[:18]}...  memo='{memo[:32]}'"
        )

        return settlement

    def get_settlement(self, award_id: str) -> PaymentSettlement | None:
        return self._settlements.get(award_id)
