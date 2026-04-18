"""
agents/interfaces/consumer_adapter.py
──────────────────────────────────────
Abstract interface for consumer agents.

In Phase 1, the ConsumerSimulator implements this.
In Phase 4, the web UI implements this (translating user input into
a ServiceRequirement and displaying broker events back to the user).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from shared.cnp_messages import (
    ServiceRequirement,
    ContractAward,
    AuditEvent,
    PaymentSettlement,
)


class ConsumerAdapter(ABC):
    """
    Abstract interface for consumer agents.

    The broker calls these methods to receive requirements and
    deliver status updates back to the consumer.
    """

    CONSUMER_ID:   str = NotImplemented
    CONSUMER_NAME: str = NotImplemented

    @abstractmethod
    def get_requirement(self) -> ServiceRequirement:
        """
        Return a ServiceRequirement to initiate the procurement cycle.
        Simulated adapter returns a pre-built requirement.
        UI adapter collects input from the user.
        """
        ...

    @abstractmethod
    def on_stage_update(self, event: AuditEvent) -> None:
        """
        Called by the broker at each CNP stage transition.
        Simulated adapter logs to stdout.
        UI adapter pushes to the live broker feed panel.
        """
        ...

    @abstractmethod
    def on_award(self, award: ContractAward) -> None:
        """Called when the broker issues a ContractAward."""
        ...

    @abstractmethod
    def on_settlement(self, settlement: PaymentSettlement) -> None:
        """Called when payment has been confirmed on-chain."""
        ...
