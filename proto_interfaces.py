"""
proto_interfaces.py
────────────────────
Prototype: three-agent negotiation with information asymmetry.

Scenario
────────
  Consumer  — Cascade Agriculture needs 30 days of drone surveillance.
              Private: budget ceiling $9,500, walk-away at $10,000.

  Producer  — AeroPlan Drone Services, multispectral imaging specialists.
              Private: cost floor $243/day ($7,290 for 30 days).

  Broker    — BlackRiver Broker mediates. It holds SEPARATE private threads
              for consumer and producer. Neither party's private data ever
              appears in the other's thread. The broker synthesises a deal
              in its own private reasoning space.

What to watch for
─────────────────
  Red panels  = private (only the recipient ever sees this)
  Blue panels = messages sent between agents
  The final summary shows what each side's private floor was and where
  the deal landed — the broker bridged the gap without leaking either.

Usage
─────
  python proto_interfaces.py
  python proto_interfaces.py --dry-run   # shows agent setup, skips API calls
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env", override=False)

from agents.interfaces.base import BrokerAgent, PrivateAgent  # noqa: E402

# ── Console helpers ───────────────────────────────────────────────────────────
console = Console()


def rule(title: str) -> None:
    console.print()
    console.rule(f"[bold yellow]{title}[/bold yellow]")
    console.print()


def private_panel(name: str, briefing: str) -> None:
    console.print(Panel(
        f"[dim]{briefing}[/dim]",
        title=f"[dim red]⚿  {name} — PRIVATE BRIEFING[/dim red]",
        border_style="dim red",
    ))


def msg(sender: str, recipient: str, content: str, private: bool = False) -> None:
    style = "dim blue" if private else "blue"
    lock = "⚿  " if private else ""
    console.print(Panel(
        content,
        title=f"[{style}]{lock}{sender} → {recipient}[/{style}]",
        border_style=style,
    ))


# ── Dry-run stubs ─────────────────────────────────────────────────────────────

class _DryRunAgent:
    """Minimal stub used when --dry-run is set (no API calls)."""

    def __init__(self, name: str, private_briefing: str) -> None:
        self.name = name
        self.private_briefing = private_briefing

    def respond(self, _: str) -> str:
        return f"[DRY-RUN] {self.name} acknowledges."


class _DryRunBroker:
    name = "BlackRiver Broker (dry-run)"

    def talk_to_consumer(self, _: str) -> str:
        return "[DRY-RUN] Broker acknowledges consumer."

    def talk_to_producer(self, _name: str, _msg: str) -> str:
        return "[DRY-RUN] Broker acknowledges producer."

    def synthesize_deal(self, _: str) -> dict:
        return {
            "deal_viable": True,
            "deal_price_total": 8_400,
            "deal_price_per_day": 280,
            "terms": "30 days, daily multispectral flights, weekly reporting",
            "to_consumer": "[DRY-RUN] AeroPlan proposes $8,400 for 30 days.",
            "to_producer": "[DRY-RUN] Client will accept $8,400 for 30 days.",
            "broker_rationale": (
                "Consumer ceiling $9,500 > deal $8,400 ✓  "
                "Producer floor $7,290 < deal $8,400 ✓"
            ),
        }


# ── Main demo ─────────────────────────────────────────────────────────────────

def main(dry_run: bool = False) -> None:
    console.rule("[bold cyan]Black River — Agent Interface Prototype[/bold cyan]")
    console.print(
        "[dim]Three-agent negotiation with information asymmetry.\n"
        "⚿ red = private channel  │  blue = inter-agent message[/dim]"
    )

    # ── Initialise agents ─────────────────────────────────────────────────────
    rule("Agents initialised")

    consumer_briefing = (
        "Approved budget ceiling: $9,500 for 30 days.\n"
        "Walk-away price: $10,000 — above this you defer to next season.\n"
        "Preference for AeroPlan (better sensors); SkyWatch acceptable if cheaper.\n"
        "Do NOT volunteer the $9,500 ceiling — let the broker negotiate."
    )
    producer_briefing = (
        "Cost basis: $180/day (equipment depreciation, pilot, fuel, insurance).\n"
        "Minimum margin 35% → floor price $243/day = $7,290 for 30 days.\n"
        "Current utilisation: 60% — you have capacity and negotiating room.\n"
        "Target: $280–300/day ($8,400–$9,000 total) is a good outcome.\n"
        "Do NOT reveal cost basis or the $243/day floor."
    )

    if dry_run:
        consumer = _DryRunAgent("Cascade Agriculture", consumer_briefing)
        producer = _DryRunAgent("AeroPlan Drone Services", producer_briefing)
        broker   = _DryRunBroker()
    else:
        consumer = PrivateAgent(
            name="Cascade Agriculture",
            role_prompt=(
                "You are a farm operations manager seeking drone surveillance services "
                "for a 50 km² agricultural property. You need 30 days of daily "
                "multispectral imaging. Be professional; protect your budget — "
                "don't volunteer your ceiling unprompted."
            ),
            private_briefing=consumer_briefing,
        )
        producer = PrivateAgent(
            name="AeroPlan Drone Services",
            role_prompt=(
                "You are a commercial drone operator specialising in agricultural "
                "multispectral imaging. You offer daily flights with professional "
                "reporting. Highlight quality and reliability. Protect your cost "
                "structure — list price is $350/day."
            ),
            private_briefing=producer_briefing,
        )
        broker = BrokerAgent("BlackRiver Broker")

    private_panel(consumer.name, consumer_briefing)
    private_panel(producer.name, producer_briefing)
    console.print(f"[green]  ✓  {broker.name} initialised — three separate context threads[/green]")

    # ── Step 1: Consumer briefs broker (private channel) ─────────────────────
    rule("Step 1 — Consumer briefs the broker")

    consumer_brief = consumer.respond(
        "Summarise my drone surveillance requirements for the broker. "
        "Include all details — the broker treats budget as confidential."
    )
    msg(consumer.name, broker.name, consumer_brief, private=True)

    broker_ack = broker.talk_to_consumer(
        f"Hello — I'm your BlackRiver broker. Here is what the client shared:\n\n"
        f"{consumer_brief}\n\n"
        "Confirm you've noted the requirements and outline how you'll approach sourcing."
    )
    msg(broker.name, consumer.name, broker_ack)

    # ── Step 2: Broker issues RFQ to producer (no budget disclosed) ───────────
    rule("Step 2 — Broker contacts producer")

    rfq = broker.talk_to_producer(
        producer.name,
        "I have a client requiring 30 days of daily drone surveillance over a 50 km² "
        "agricultural property. Multispectral imaging and professional reporting required. "
        "Please describe your capabilities and provide your best pricing for this scope."
    )
    msg(broker.name, producer.name, rfq)

    producer_response = producer.respond(
        f"Request for quote:\n\n{rfq}\n\n"
        "Provide a capability overview and your pricing proposal. "
        "You may share context about availability and any flexibility privately."
    )
    msg(producer.name, broker.name, producer_response, private=True)

    broker_follow_up = broker.talk_to_producer(
        producer.name,
        f"Thank you:\n\n{producer_response}\n\n"
        "What is the most competitive price you could offer for a firm 30-day commitment, "
        "given your current availability?"
    )
    msg(broker.name, producer.name, broker_follow_up)

    producer_counter = producer.respond(broker_follow_up)
    msg(producer.name, broker.name, producer_counter, private=True)
    broker.talk_to_producer(producer.name, producer_counter)   # absorb into thread

    # ── Step 3: Broker private synthesis ─────────────────────────────────────
    rule("Step 3 — Broker private synthesis (reads both private threads)")

    deal = broker.synthesize_deal(producer.name)

    console.print(Panel(
        f"[bold]Viable:[/bold] {deal.get('deal_viable')}\n"
        f"[bold]Price:[/bold]  ${deal.get('deal_price_total', 0):,} total  "
        f"(${deal.get('deal_price_per_day', 0)}/day × 30 days)\n"
        f"[bold]Terms:[/bold]  {deal.get('terms', '')}\n\n"
        f"[bold]Rationale (private):[/bold]\n[dim]{deal.get('broker_rationale', '')}[/dim]",
        title="[dim red]⚿  Broker — PRIVATE DEAL ANALYSIS[/dim red]",
        border_style="dim red",
    ))

    if not deal.get("deal_viable", False):
        console.print("[red]  ✗  No viable deal — consumer ceiling is below producer floor.[/red]")
        return

    # ── Step 4: Broker presents to each party ────────────────────────────────
    rule("Step 4 — Broker presents deal (information asymmetry enforced)")

    # To consumer: price only — no mention of producer's cost floor
    broker_to_consumer = broker.talk_to_consumer(
        f"I have completed preliminary negotiations with AeroPlan.\n\n"
        f"{deal.get('to_consumer', '')}\n\n"
        f"Proposed total: ${deal.get('deal_price_total', 0):,} for 30 days "
        f"(${deal.get('deal_price_per_day', 0)}/day). Do you accept?"
    )
    msg(broker.name, consumer.name, broker_to_consumer)

    consumer_decision = consumer.respond(broker_to_consumer)
    msg(consumer.name, broker.name, consumer_decision)

    # To producer: confirmed client intent — no mention of consumer's ceiling
    broker_to_producer = broker.talk_to_producer(
        producer.name,
        f"My client has reviewed the proposal.\n\n"
        f"{deal.get('to_producer', '')}\n\n"
        f"We would like to proceed at ${deal.get('deal_price_total', 0):,} for 30 days. "
        "Please confirm."
    )
    msg(broker.name, producer.name, broker_to_producer)

    producer_decision = producer.respond(broker_to_producer)
    msg(producer.name, broker.name, producer_decision)

    # ── Summary ───────────────────────────────────────────────────────────────
    console.print()
    console.rule("[bold green]Outcome[/bold green]")
    console.print(
        f"\n"
        f"  Consumer budget ceiling  $9,500   [dim](producer never saw this)[/dim]\n"
        f"  Producer cost floor      $7,290   [dim](consumer never saw this)[/dim]\n"
        f"  Deal price               ${deal.get('deal_price_total', 0):,}   "
        f"[dim](clears both floors, leaves value for both sides)[/dim]\n\n"
        f"  [dim]The broker used private information from both threads to find the deal.\n"
        f"  Neither party's private constraints were disclosed to the other.[/dim]\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Black River agent interface prototype")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip API calls — shows structure without running LLM inference",
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run)
