"""
agents/broker/broker_graph.py
──────────────────────────────
Black River Broker Agent — LangGraph state machine.

Graph structure (CNP lifecycle)
────────────────────────────────
  [START]
    │
    ▼
  announce_task          → log ANNOUNCED event on-chain
    │
    ▼
  collect_bids           → gather ProducerBid from all eligible producers
    │
    ├─ no bids ──────────► handle_no_bids → [FAILED / END]
    │
    ▼
  evaluate_bids          → LLM-assisted scoring + BidEvaluation
    │
    ▼
  award_contract         → write ContractAward on-chain, notify producer
    │
    ├─ producer rejects ─► re_evaluate → loop back to evaluate_bids
    │
    ▼
  monitor_execution      → poll producer adapter until COMPLETE
    │
    ▼
  verify_and_settle      → confirm delivery hash, release payment
    │
    ▼
  [END]
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel
from typing_extensions import TypedDict

from shared.cnp_messages import (
    AuditEvent, BidEvaluation, BidScore, CNPStage, ContractAward,
    DeliveryConfirmation, ExecutionUpdate, PaymentSettlement, ProducerBid,
    ServiceRequirement, TaskAnnouncement,
)
from agents.interfaces.producer_adapter import ProducerAdapter
from agents.interfaces.consumer_adapter import ConsumerAdapter
from agents.broker.chain_client import ChainClient

logger = logging.getLogger(__name__)


# ── Graph State ───────────────────────────────────────────────────────────

class BrokerState(TypedDict):
    """
    The complete state of one procurement cycle, carried through the graph.
    Every node reads from and writes to this dict.
    """
    # Input
    requirement:        ServiceRequirement

    # Built during the cycle
    announcement:       Optional[TaskAnnouncement]
    bids:               List[ProducerBid]
    evaluation:         Optional[BidEvaluation]
    award:              Optional[ContractAward]
    delivery:           Optional[DeliveryConfirmation]
    settlement:         Optional[PaymentSettlement]

    # Audit trail (appended at each stage)
    audit_events:       List[AuditEvent]

    # Current CNP stage
    stage:              CNPStage

    # Error / failure reason (if any)
    failure_reason:     Optional[str]

    # Re-evaluation attempt counter (guards against infinite loops)
    re_eval_count:      int


# ── Broker Agent ──────────────────────────────────────────────────────────

class BrokerAgent:
    """
    The Black River autonomous broker.

    Instantiate with:
      - producers   : list of ProducerAdapter implementations
      - consumer    : ConsumerAdapter implementation
      - chain       : ChainClient for on-chain writes
      - llm_model   : Anthropic model string (default: claude-sonnet-4-6)

    Phase 2 note: MPPSimulator has been removed.  Payment settlement is
    derived directly from the on-chain settlePayment() transaction receipt —
    the BlackRiverBroker contract IS the payment rail.
    """

    MAX_RE_EVALS = 2        # max times to re-evaluate after producer rejection
    BID_TIMEOUT_SECONDS = 30  # how long to wait for bids in simulation

    def __init__(
        self,
        producers:   List[ProducerAdapter],
        consumer:    ConsumerAdapter,
        chain:       ChainClient,
        mpp:         Any = None,   # deprecated — ignored; kept for call-site compatibility
        llm_model:   str = "claude-sonnet-4-6",
        broker_wallet: str = "0x0000000000000000000000000000000000000001",
        dry_run:     bool = False,
    ):
        self.producers     = {p.PRODUCER_ID: p for p in producers}
        self.consumer      = consumer
        self.chain         = chain
        self.broker_wallet = broker_wallet
        self.dry_run       = dry_run
        # Only initialise the LLM if we'll actually use it (requires ANTHROPIC_API_KEY)
        has_key = bool(os.getenv("ANTHROPIC_API_KEY"))
        self.llm = ChatAnthropic(model=llm_model, temperature=0) if (has_key and not dry_run) else None
        self._graph = self._build_graph()

    # ── Graph construction ────────────────────────────────────────────────

    def _build_graph(self) -> Any:
        builder = StateGraph(BrokerState)

        builder.add_node("announce_task",     self._announce_task)
        builder.add_node("collect_bids",      self._collect_bids)
        builder.add_node("evaluate_bids",     self._evaluate_bids)
        builder.add_node("award_contract",    self._award_contract)
        builder.add_node("monitor_execution", self._monitor_execution)
        builder.add_node("verify_and_settle", self._verify_and_settle)
        builder.add_node("handle_failure",    self._handle_failure)

        builder.add_edge(START, "announce_task")
        builder.add_edge("announce_task", "collect_bids")

        builder.add_conditional_edges(
            "collect_bids",
            self._route_after_bids,
            {"evaluate": "evaluate_bids", "fail": "handle_failure"},
        )

        builder.add_edge("evaluate_bids", "award_contract")

        builder.add_conditional_edges(
            "award_contract",
            self._route_after_award,
            {
                "execute":    "monitor_execution",
                "re_evaluate":"evaluate_bids",
                "fail":       "handle_failure",
            },
        )

        builder.add_edge("monitor_execution", "verify_and_settle")
        builder.add_edge("verify_and_settle", END)
        builder.add_edge("handle_failure", END)

        return builder.compile()

    # ── Routing functions ─────────────────────────────────────────────────

    def _route_after_bids(self, state: BrokerState) -> str:
        if not state["bids"]:
            return "fail"
        return "evaluate"

    def _route_after_award(self, state: BrokerState) -> str:
        if state["failure_reason"]:
            return "fail"
        if state["award"] is None:
            # Producer rejected — try re-evaluation if attempts remain
            if state["re_eval_count"] < self.MAX_RE_EVALS:
                return "re_evaluate"
            return "fail"
        return "execute"

    # ── Node implementations ──────────────────────────────────────────────

    def _announce_task(self, state: BrokerState) -> Dict:
        req = state["requirement"]
        logger.info(f"[BROKER] Announcing task for requirement {req.requirement_id}")

        announcement = TaskAnnouncement(
            requirement_id=      req.requirement_id,
            service_type=        req.service_type,
            description=         req.description,
            location=            req.location,
            start_time=          req.start_time,
            end_time=            req.end_time,
            coverage_frequency=  req.coverage_frequency,
            budget_ceiling_usdc= req.budget_ceiling_usdc,
            criteria=            req.criteria,
            bid_deadline=        datetime.utcnow() + timedelta(seconds=self.BID_TIMEOUT_SECONDS),
            broker_wallet=       self.broker_wallet,
        )

        event = self._make_audit_event(
            req.requirement_id, CNPStage.ANNOUNCED,
            "broker",
            f"Task announced to {len(self.producers)} registered producers",
            announcement,
        )
        self.chain.log_event(event)
        self.consumer.on_stage_update(event)

        return {
            "announcement":  announcement,
            "stage":         CNPStage.ANNOUNCED,
            "audit_events":  state["audit_events"] + [event],
        }

    def _collect_bids(self, state: BrokerState) -> Dict:
        announcement = state["announcement"]
        logger.info(f"[BROKER] Collecting bids for {announcement.announcement_id}")

        bids: List[ProducerBid] = []
        for producer in self.producers.values():
            if producer.can_fulfil(announcement):
                bid = producer.submit_bid(announcement)
                bids.append(bid)
                logger.info(f"  ← Bid received from {producer.PRODUCER_NAME}: ${bid.price_usdc:.2f}")
            else:
                logger.info(f"  ✗ {producer.PRODUCER_NAME} cannot fulfil")

        event = self._make_audit_event(
            announcement.requirement_id, CNPStage.BIDDING,
            "broker",
            f"{len(bids)} bid(s) received from {len(self.producers)} solicited producers",
            {"bid_count": len(bids), "announcement_id": announcement.announcement_id},
        )
        self.chain.log_event(event)
        self.consumer.on_stage_update(event)

        return {
            "bids":         bids,
            "stage":        CNPStage.BIDDING,
            "audit_events": state["audit_events"] + [event],
        }

    def _evaluate_bids(self, state: BrokerState) -> Dict:
        bids     = state["bids"]
        req      = state["requirement"]
        criteria = req.criteria
        logger.info(f"[BROKER] Evaluating {len(bids)} bids")

        # ── Deterministic scoring ─────────────────────────────────────────
        prices = [b.price_usdc for b in bids]
        min_p, max_p = min(prices), max(prices)
        price_range = max_p - min_p or 1.0  # avoid div-by-zero

        raw_scores = []
        for bid in bids:
            price_score        = 1.0 - (bid.price_usdc - min_p) / price_range
            availability_score = 1.0 if bid.available else 0.0
            perf_score         = bid.past_performance_score
            weighted = (
                criteria.price_weight            * price_score +
                criteria.availability_weight     * availability_score +
                criteria.past_performance_weight * perf_score
            )
            raw_scores.append((bid, price_score, availability_score, perf_score, weighted))

        raw_scores.sort(key=lambda x: x[4], reverse=True)
        winner_bid = raw_scores[0][0]

        # ── LLM-generated rationale (skipped in dry-run) ─────────────────
        scores_summary = "\n".join([
            f"- {b.producer_name}: price=${b.price_usdc:.2f} "
            f"(score={ps:.2f}), available={b.available}, "
            f"past_performance={b.past_performance_score:.2f}, "
            f"weighted_total={w:.3f}"
            for b, ps, _, _, w in raw_scores
        ])

        if self.llm is None:
            winner_rationale = (
                f"{winner_bid.producer_name} selected with weighted score "
                f"{raw_scores[0][4]:.3f} (price {criteria.price_weight*100:.0f}% / "
                f"availability {criteria.availability_weight*100:.0f}% / "
                f"performance {criteria.past_performance_weight*100:.0f}%). "
                f"[{'dry-run' if self.dry_run else 'no ANTHROPIC_API_KEY'}: LLM rationale skipped]"
            )
        else:
            prompt = [
                SystemMessage(content=(
                    "You are the Black River autonomous broker. "
                    "Write a concise (2-3 sentence) award rationale explaining why the "
                    "top-scoring producer won, referencing the evaluation criteria weights."
                )),
                HumanMessage(content=(
                    f"Requirement: {req.description}\n"
                    f"Criteria weights — price: {criteria.price_weight}, "
                    f"availability: {criteria.availability_weight}, "
                    f"past performance: {criteria.past_performance_weight}\n\n"
                    f"Bid scores:\n{scores_summary}\n\n"
                    f"Winner: {winner_bid.producer_name}"
                )),
            ]
            rationale_response = self.llm.invoke(prompt)
            winner_rationale   = rationale_response.content

        # ── Build BidEvaluation ───────────────────────────────────────────
        bid_scores = [
            BidScore(
                bid_id=                 b.bid_id,
                producer_id=            b.producer_id,
                price_score=            ps,
                availability_score=     av,
                past_performance_score= perf,
                weighted_total=         w,
                rationale=              winner_rationale if b.bid_id == winner_bid.bid_id else "",
            )
            for b, ps, av, perf, w in raw_scores
        ]

        evaluation = BidEvaluation(
            requirement_id= req.requirement_id,
            scores=         bid_scores,
            winner_bid_id=  winner_bid.bid_id,
        )

        event = self._make_audit_event(
            req.requirement_id, CNPStage.EVALUATING,
            "broker",
            f"Evaluation complete. Winner: {winner_bid.producer_name} "
            f"(score={raw_scores[0][4]:.3f}). {winner_rationale}",
            evaluation,
        )
        self.chain.log_event(event)
        self.consumer.on_stage_update(event)

        return {
            "evaluation":   evaluation,
            "stage":        CNPStage.EVALUATING,
            "audit_events": state["audit_events"] + [event],
            "re_eval_count":state["re_eval_count"],
        }

    def _award_contract(self, state: BrokerState) -> Dict:
        evaluation = state["evaluation"]
        bids_by_id = {b.bid_id: b for b in state["bids"]}
        req        = state["requirement"]
        winner_bid = bids_by_id[evaluation.winner_bid_id]
        producer   = self.producers[winner_bid.producer_id]

        award = ContractAward(
            requirement_id= req.requirement_id,
            announcement_id=state["announcement"].announcement_id,
            winning_bid_id= winner_bid.bid_id,
            producer_id=    winner_bid.producer_id,
            producer_wallet=winner_bid.producer_wallet,
            price_usdc=     winner_bid.price_usdc,
        )

        # Write to chain (escrow + award record)
        tx = self.chain.record_award(award)
        award.contract_address = tx.get("contract_address")
        award.tx_hash          = tx.get("tx_hash")

        # Notify producer
        accepted = producer.accept_award(award)

        event = self._make_audit_event(
            req.requirement_id, CNPStage.AWARDED,
            "broker",
            f"Contract awarded to {producer.PRODUCER_NAME} "
            f"for ${winner_bid.price_usdc:.2f} USDC. "
            f"Producer {'accepted' if accepted else 'REJECTED'}.",
            award,
        )
        self.chain.log_event(event)
        self.consumer.on_stage_update(event)
        self.consumer.on_award(award)

        updates: Dict = {
            "stage":        CNPStage.AWARDED,
            "audit_events": state["audit_events"] + [event],
            "re_eval_count":state["re_eval_count"],
        }

        if accepted:
            updates["award"] = award
        else:
            logger.warning(f"[BROKER] Producer {producer.PRODUCER_NAME} rejected award — re-evaluating")
            # Remove the rejecting producer's bid and increment counter
            updates["bids"]         = [b for b in state["bids"] if b.producer_id != winner_bid.producer_id]
            updates["re_eval_count"]= state["re_eval_count"] + 1
            updates["award"]        = None

        return updates

    def _monitor_execution(self, state: BrokerState) -> Dict:
        award    = state["award"]
        producer = self.producers[award.producer_id]
        req      = state["requirement"]
        logger.info(f"[BROKER] Monitoring execution for award {award.award_id}")

        import time
        if self.dry_run:
            # In dry-run skip real-time polling — assume execution completes immediately
            update = ExecutionUpdate(
                award_id=    award.award_id,
                producer_id= award.producer_id,
                status=      "COMPLETE",
                progress_pct=100.0,
                notes=       "[dry-run] execution simulated as instant",
            )
            logger.info(f"  ↻ {producer.PRODUCER_NAME}: {update.status} (100%) [dry-run]")
        else:
            while True:
                update = producer.get_status(award.award_id)
                logger.info(f"  ↻ {producer.PRODUCER_NAME}: {update.status} ({update.progress_pct:.0f}%)")
                if update.status == "COMPLETE":
                    break
                time.sleep(2)

        event = self._make_audit_event(
            req.requirement_id, CNPStage.EXECUTING,
            award.producer_id,
            f"Execution complete. {producer.PRODUCER_NAME} reported delivery.",
            update,
        )
        self.chain.log_event(event)
        self.consumer.on_stage_update(event)

        return {
            "stage":        CNPStage.EXECUTING,
            "audit_events": state["audit_events"] + [event],
        }

    def _verify_and_settle(self, state: BrokerState) -> Dict:
        award    = state["award"]
        producer = self.producers[award.producer_id]
        req      = state["requirement"]

        # 1. Get delivery evidence from producer
        delivery = producer.confirm_delivery(award.award_id)

        # 2. Write delivery hash on-chain → contract advances to DELIVERED
        self.chain.confirm_delivery(award.award_id, delivery.delivery_hash)

        # 3. Release escrowed USDC to producer → contract advances to SETTLED
        #    The on-chain tx IS the payment; no external payment rail is needed.
        chain_result = self.chain.settle_payment(award.award_id)

        # 4. Build a PaymentSettlement record from the chain receipt
        settlement = PaymentSettlement(
            award_id=       award.award_id,
            producer_wallet=award.producer_wallet,
            amount_usdc=    award.price_usdc,
            tx_hash=        chain_result["tx_hash"],
            memo=           f"{award.award_id}:{req.requirement_id}"[:32],
            settled_at=     datetime.utcnow(),
        )

        delivered_event = self._make_audit_event(
            req.requirement_id, CNPStage.DELIVERED,
            award.producer_id,
            f"Delivery confirmed. Hash: {delivery.delivery_hash[:20]}...",
            delivery,
        )
        settled_event = self._make_audit_event(
            req.requirement_id, CNPStage.SETTLED,
            "broker",
            f"Payment of ${award.price_usdc:.2f} USDC settled on-chain. "
            f"Tx: {settlement.tx_hash[:20]}...",
            settlement,
        )

        self.chain.log_event(delivered_event)
        self.chain.log_event(settled_event)
        self.consumer.on_stage_update(delivered_event)
        self.consumer.on_stage_update(settled_event)
        self.consumer.on_settlement(settlement)

        logger.info(f"[BROKER] ✓ Procurement complete for {req.requirement_id}")

        return {
            "delivery":     delivery,
            "settlement":   settlement,
            "stage":        CNPStage.SETTLED,
            "audit_events": state["audit_events"] + [delivered_event, settled_event],
        }

    def _handle_failure(self, state: BrokerState) -> Dict:
        req    = state["requirement"]
        reason = state.get("failure_reason") or "No qualifying bids received"
        logger.error(f"[BROKER] ✗ Procurement FAILED for {req.requirement_id}: {reason}")

        event = self._make_audit_event(
            req.requirement_id, CNPStage.FAILED,
            "broker",
            f"Procurement failed: {reason}",
            {"failure_reason": reason},
        )
        self.chain.log_event(event)
        self.consumer.on_stage_update(event)

        return {
            "stage":          CNPStage.FAILED,
            "failure_reason": reason,
            "audit_events":   state["audit_events"] + [event],
        }

    # ── Entry point ───────────────────────────────────────────────────────

    def run(self, requirement: ServiceRequirement) -> BrokerState:
        """Run a complete procurement cycle for the given requirement."""
        initial: BrokerState = {
            "requirement":    requirement,
            "announcement":   None,
            "bids":           [],
            "evaluation":     None,
            "award":          None,
            "delivery":       None,
            "settlement":     None,
            "audit_events":   [],
            "stage":          CNPStage.ANNOUNCED,
            "failure_reason": None,
            "re_eval_count":  0,
        }
        return self._graph.invoke(initial)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _make_audit_event(
        requirement_id: str,
        stage:          CNPStage,
        actor:          str,
        summary:        str,
        payload:        Any,
    ) -> AuditEvent:
        payload_str  = json.dumps(payload, default=str)
        payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()
        return AuditEvent(
            requirement_id= requirement_id,
            stage=          stage,
            actor=          actor,
            summary=        summary,
            payload_hash=   payload_hash,
        )
