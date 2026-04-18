"""
agents/interfaces/producer_adapter.py
──────────────────────────────────────
The ProducerAdapter abstract base class is THE critical boundary in the
Black River architecture.

Every producer — simulated (Phase 1) or real (Phase 3) — must implement
this interface exactly.  The broker agent never imports a concrete adapter
directly; it works only with this ABC.  This is what makes
simulation-first work: swapping a simulated adapter for a real one is a
configuration change, not a code change.

Implementation checklist for new adapters
──────────────────────────────────────────
  [ ] Subclass ProducerAdapter
  [ ] Set PRODUCER_ID and PRODUCER_NAME as class attributes
  [ ] Implement all five abstract methods
  [ ] Register the adapter in agents/producers/__init__.py
  [ ] Write at least one integration test in test/test_adapter.py
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from shared.cnp_messages import (
    TaskAnnouncement,
    ProducerBid,
    ContractAward,
    ExecutionUpdate,
    DeliveryConfirmation,
)


class ProducerAdapter(ABC):
    """
    Abstract interface that every producer must implement.

    The broker calls these methods in order during the CNP lifecycle:

      1. can_fulfil()       — quick capability check before soliciting a bid
      2. submit_bid()       — produce a structured bid for the announcement
      3. accept_award()     — acknowledge the contract award
      4. get_status()       — broker polls during EXECUTING stage
      5. confirm_delivery() — producer signals service completion
    """

    # Concrete adapters MUST set these at the class level
    PRODUCER_ID:   str = NotImplemented
    PRODUCER_NAME: str = NotImplemented

    # ── Lifecycle methods ─────────────────────────────────────────────────

    @abstractmethod
    def can_fulfil(self, announcement: TaskAnnouncement) -> bool:
        """
        Quick pre-check: can this producer fulfil the announcement at all?

        Called before submit_bid().  If False, the producer is excluded
        from the solicitation and submit_bid() is never called.

        Should be fast and deterministic — no LLM calls, no network I/O.
        """
        ...

    @abstractmethod
    def submit_bid(self, announcement: TaskAnnouncement) -> ProducerBid:
        """
        Return a fully populated ProducerBid for the announcement.

        The bid must include:
          - price_usdc         : total cost to fulfil the contract
          - available          : True if the producer can meet the schedule
          - past_performance_score : 0.0–1.0 normalised score
          - producer_wallet    : on-chain address for payment
          - proposal_narrative : free-text explanation of the proposal

        Simulated adapters return deterministic data.
        Real adapters call their backend API.
        """
        ...

    @abstractmethod
    def accept_award(self, award: ContractAward) -> bool:
        """
        Notify the producer that they have won the contract.

        Returns True if the producer acknowledges and commits.
        Returns False if the producer withdraws (triggers re-evaluation).

        Real adapters should persist the award_id for status polling.
        """
        ...

    @abstractmethod
    def get_status(self, award_id: str) -> ExecutionUpdate:
        """
        Return the current execution status for a given award.

        Called by the broker during the EXECUTING stage on a polling
        interval.  The broker transitions to DELIVERED when
        update.status == "COMPLETE".
        """
        ...

    @abstractmethod
    def confirm_delivery(self, award_id: str) -> DeliveryConfirmation:
        """
        Return delivery confirmation including evidence and delivery hash.

        The delivery_hash (keccak256 of evidence) is written on-chain
        by the broker for tamper-evident record-keeping.

        Only called after get_status() returns status == "COMPLETE".
        """
        ...

    # ── Convenience ───────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<ProducerAdapter id={self.PRODUCER_ID} name={self.PRODUCER_NAME}>"
