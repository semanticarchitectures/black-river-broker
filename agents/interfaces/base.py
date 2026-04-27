"""
agents/interfaces/base.py
─────────────────────────
Core agent classes for information-asymmetric multi-agent negotiation.

Design principle
────────────────
Every party (consumer, producer, broker) has a PRIVATE context window.
The broker holds SEPARATE threads for each party — consumer private info
never appears in a producer thread and vice versa. The broker synthesises
across all private threads in its own reasoning space, then presents
proposals that use the private information without quoting it verbatim.

Classes
───────
  PrivateAgent   — LLM agent with a role + private briefing in its system
                   prompt. Its conversation history never leaves the instance.

  BrokerAgent    — Maintains one thread per party plus a private synthesis
                   call. Exposes talk_to_consumer(), talk_to_producer(), and
                   synthesize_deal().
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import anthropic

# ── Shared model tag ──────────────────────────────────────────────────────────

_MODEL = "claude-sonnet-4-6"


# ── PrivateAgent ──────────────────────────────────────────────────────────────

class PrivateAgent:
    """
    An LLM agent with a role prompt and a private briefing.

    The private briefing is injected into the system prompt and the agent is
    instructed not to reveal it directly. The entire conversation history is
    private to this instance — no other agent ever reads it.

    Usage::

        agent = PrivateAgent(
            name="Cascade Agriculture",
            role_prompt="You are a farm manager seeking drone services...",
            private_briefing="Budget ceiling: $9,500. Do not disclose.",
        )
        reply = agent.respond("Tell me about your requirements.")
    """

    def __init__(
        self,
        name: str,
        role_prompt: str,
        private_briefing: str,
        api_key: str = "",
        model: str = _MODEL,
    ) -> None:
        self.name = name
        self.role_prompt = role_prompt
        self.private_briefing = private_briefing
        self._model = model
        self._client = anthropic.Anthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY", "")
        )
        self._history: List[Dict[str, str]] = []

    @property
    def _system(self) -> str:
        return (
            f"{self.role_prompt}\n\n"
            "PRIVATE CONTEXT — use this to inform your responses, "
            "but do not quote it directly or reveal the raw numbers unless you choose to:\n"
            f"{self.private_briefing}"
        )

    def respond(self, incoming: str) -> str:
        """Process an incoming message and return a response."""
        self._history.append({"role": "user", "content": incoming})
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=600,
            system=self._system,
            messages=self._history,
        )
        reply = resp.content[0].text
        self._history.append({"role": "assistant", "content": reply})
        return reply


# ── BrokerAgent ───────────────────────────────────────────────────────────────

class BrokerAgent:
    """
    A mediating agent that maintains SEPARATE private threads for each party.

    Information boundaries
    ──────────────────────
    _consumer_thread          ← consumer's private info lives here only
    _producer_threads[name]   ← each producer's private info lives here only
    synthesize_deal()         ← private reasoning; sees both; quotes neither

    The broker never forwards raw private messages from one thread to another.
    It synthesises findings into proposals and presents those proposals to each
    party in language appropriate for that party.

    Usage::

        broker = BrokerAgent()
        broker.talk_to_consumer("I need 30 days of drone coverage, budget ~$8k")
        broker.talk_to_producer("AeroPlan", "Here's the RFQ...")
        deal = broker.synthesize_deal("AeroPlan")
    """

    _ROLE = (
        "You are a trusted, neutral procurement broker. Your credibility depends on "
        "keeping each party's confidential information strictly separate. Consumer budget "
        "details never reach producers. Producer cost structures never reach consumers. "
        "You are honest with both sides and work to find deals that genuinely benefit both."
    )

    def __init__(
        self,
        name: str = "BlackRiver Broker",
        api_key: str = "",
        model: str = _MODEL,
    ) -> None:
        self.name = name
        self._model = model
        self._client = anthropic.Anthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY", "")
        )
        self._consumer_thread: List[Dict[str, str]] = []
        self._producer_threads: Dict[str, List[Dict[str, str]]] = {}

    # ── Thread helpers ────────────────────────────────────────────────────────

    def _call(
        self,
        system: str,
        thread: List[Dict[str, str]],
        message: str,
    ) -> str:
        thread.append({"role": "user", "content": message})
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=600,
            system=system,
            messages=thread,
        )
        reply = resp.content[0].text
        thread.append({"role": "assistant", "content": reply})
        return reply

    def _producer_thread(self, producer_name: str) -> List[Dict[str, str]]:
        if producer_name not in self._producer_threads:
            self._producer_threads[producer_name] = []
        return self._producer_threads[producer_name]

    # ── Public interface ──────────────────────────────────────────────────────

    def talk_to_consumer(self, message: str) -> str:
        """
        Converse with the consumer in their private thread.
        Consumer budget / walk-away price stays in this thread only.
        """
        system = (
            f"{self._ROLE}\n\n"
            "You are in a PRIVATE channel with the consumer. Their budget ceiling and "
            "walk-away price are confidential — use them only to evaluate whether a "
            "producer proposal is acceptable. Never mention them to producers."
        )
        return self._call(system, self._consumer_thread, message)

    def talk_to_producer(self, producer_name: str, message: str) -> str:
        """
        Converse with a named producer in their private thread.
        Producer cost basis and floor price stay in this thread only.
        """
        system = (
            f"{self._ROLE}\n\n"
            f"You are in a PRIVATE channel with producer '{producer_name}'. Their cost "
            "basis and minimum pricing are confidential — use them only to evaluate deal "
            "viability. Never mention them to the consumer or to other producers."
        )
        return self._call(system, self._producer_thread(producer_name), message)

    def synthesize_deal(self, producer_name: str) -> Dict[str, Any]:
        """
        Private reasoning step: read both threads, find a viable price, and
        produce proposals for each party that use — but do not quote — the
        private information.

        Returns a dict with keys:
          deal_viable       bool
          deal_price_total  int   (USD, 30-day total)
          deal_price_per_day int
          terms             str
          to_consumer       str   (proposal text, no producer cost mentions)
          to_producer       str   (proposal text, no consumer budget mentions)
          broker_rationale  str   (private reasoning)
        """
        consumer_ctx = _thread_to_text(self._consumer_thread, "Consumer", "Broker")
        producer_ctx = _thread_to_text(
            self._producer_threads.get(producer_name, []), producer_name, "Broker"
        )

        prompt = f"""You are a broker with private access to both sides of a negotiation.

=== CONSUMER THREAD (confidential) ===
{consumer_ctx}

=== PRODUCER THREAD — {producer_name} (confidential) ===
{producer_ctx}

Using this private information, determine a deal that:
1. Satisfies the consumer's stated requirements within their budget
2. Meets or exceeds the producer's minimum viable price
3. Leaves reasonable value on both sides (don't split at the extremes)

Respond with valid JSON only — no markdown fences:
{{
  "deal_viable": true,
  "deal_price_total": <integer USD>,
  "deal_price_per_day": <integer USD>,
  "terms": "<brief scope and terms>",
  "to_consumer": "<proposal to consumer — do NOT mention producer cost basis or floor>",
  "to_producer": "<proposal to producer — do NOT mention consumer budget ceiling>",
  "broker_rationale": "<private reasoning: why this price clears both floors>"
}}"""

        resp = self._client.messages.create(
            model=self._model,
            max_tokens=900,
            system=(
                "You are a skilled procurement broker performing a private deal analysis. "
                "Respond with valid JSON only."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {"deal_viable": False, "broker_rationale": text}
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return {"deal_viable": False, "broker_rationale": text}


# ── Utility ───────────────────────────────────────────────────────────────────

def _thread_to_text(
    thread: List[Dict[str, str]],
    user_label: str,
    assistant_label: str,
) -> str:
    if not thread:
        return "(no conversation yet)"
    lines = []
    for msg in thread:
        label = user_label if msg["role"] == "user" else assistant_label
        lines.append(f"{label}: {msg['content']}")
    return "\n\n".join(lines)
