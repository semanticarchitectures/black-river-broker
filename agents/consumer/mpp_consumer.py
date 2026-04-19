"""
agents/consumer/mpp_consumer.py
─────────────────────────────────
Black River MPP Consumer Client — Phase 2.

An autonomous consumer agent that discovers the Black River broker via
HTTP, handles the MPP payment challenge (HTTP 402), and receives the
procurement result.

The pympp Client handles the 402 / WWW-Authenticate: Payment /
Authorization: Payment cycle automatically — this module just builds
the request and pretty-prints the result.

Usage:
  # Against a running broker server on localhost:8000
  python -m agents.consumer.mpp_consumer

  # Dry-run (no on-chain payment — useful when server is also in dry-run)
  python -m agents.consumer.mpp_consumer --dry-run

  # Custom broker URL
  python -m agents.consumer.mpp_consumer --broker http://broker.example.com:8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

logger  = logging.getLogger(__name__)
console = Console()


# ── Requirement builder ───────────────────────────────────────────────────────

def _default_requirement(budget: float = 1000.0) -> Dict[str, Any]:
    """Build a sample drone surveillance procurement request."""
    start = (datetime.utcnow() + timedelta(days=1)).date()
    end   = (datetime.utcnow() + timedelta(days=4)).date()
    return {
        "description":           "Aerial surveillance of active construction site",
        "location_description":  "Interstate 91 Bridge, Windsor, VT",
        "location_lat":          43.4792,
        "location_lon":         -72.3843,
        "service_type":          "DRONE_SURVEILLANCE",
        "start_date":            str(start),
        "end_date":              str(end),
        "coverage_frequency":    "DAILY",
        "budget_ceiling_usdc":   budget,
        "price_weight":          0.5,
        "availability_weight":   0.3,
        "past_performance_weight": 0.2,
    }


# ── MPP client flow ───────────────────────────────────────────────────────────

async def run_mpp(
    broker_url: str,
    consumer_key: str,
    requirement: Dict[str, Any],
    rpc_url: str = "https://rpc.testnet.tempo.xyz",
) -> Dict[str, Any]:
    """
    Execute the full MPP payment flow against the broker server.

    Returns the JSON response from POST /procure after successful payment.
    Raises RuntimeError on failure.
    """
    from mpp.client import Client
    from mpp.methods.tempo import tempo, TempoAccount, ChargeIntent

    account = TempoAccount.from_key(consumer_key, rpc_url=rpc_url)

    async with Client(
        methods=[tempo(account=account, intents={"charge": ChargeIntent()})]
    ) as client:
        logger.info(f"[CONSUMER] POST {broker_url}/procure  "
                    f"budget=${requirement['budget_ceiling_usdc']:.2f} USDC")

        response = await client.post(
            f"{broker_url}/procure",
            json=requirement,
        )

        if response.status_code != 200:
            body = response.text
            raise RuntimeError(
                f"Broker returned {response.status_code}: {body[:400]}"
            )

        return response.json()


# ── Ungated client flow (dry-run / dev) ───────────────────────────────────────

async def run_ungated(
    broker_url: str,
    requirement: Dict[str, Any],
) -> Dict[str, Any]:
    """Call the broker without MPP payment (for dry-run / ungated server)."""
    import httpx

    async with httpx.AsyncClient(timeout=120.0) as client:
        logger.info(f"[CONSUMER] POST {broker_url}/procure (no payment gate)")
        response = await client.post(f"{broker_url}/procure", json=requirement)

        if response.status_code != 200:
            raise RuntimeError(
                f"Broker returned {response.status_code}: {response.text[:400]}"
            )

        return response.json()


# ── Pretty output ─────────────────────────────────────────────────────────────

def _print_result(result: Dict[str, Any]) -> None:
    console.print(Panel(
        f"[bold green]✓ Procurement complete[/bold green]\n\n"
        f"Requirement:  {result['requirement_id']}\n"
        f"Winner:       {result['winner_id']}\n"
        f"Amount:       ${result['amount_usdc']:,.2f} USDC\n"
        f"Tx hash:      {result['tx_hash'][:30]}…\n"
        f"Memo:         {result['memo']}\n"
        f"Settled:      {result['settled_at']}\n"
        f"Audit events: {result['audit_events']}",
        title="[green]Black River — Settlement Confirmed[/green]",
        border_style="green",
    ))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Black River MPP Consumer Client")
    parser.add_argument("--broker",  default=None,
                        help="Broker server URL (default: MPP_SERVER_URL env var or http://localhost:8000)")
    parser.add_argument("--budget",  type=float, default=1000.0,
                        help="Budget ceiling in USDC (default: 1000)")
    parser.add_argument("--env",     metavar="FILE", default=None,
                        help="Env file to load")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip MPP payment — use ungated HTTP call (requires server in --dry-run mode)")
    args = parser.parse_args()

    # Load environment
    if args.env:
        load_dotenv(args.env, override=True)
    load_dotenv(".env.tempo_testnet", override=False)
    load_dotenv(".env.local",         override=False)
    load_dotenv(".env",               override=False)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    broker_url   = args.broker or os.getenv("MPP_SERVER_URL", "http://localhost:8000")
    consumer_key = os.getenv("CONSUMER_PRIVATE_KEY") or os.getenv("DEPLOYER_PRIVATE_KEY", "")
    rpc_url      = os.getenv("TEMPO_TESTNET_RPC", "https://rpc.testnet.tempo.xyz")
    requirement  = _default_requirement(budget=args.budget)

    console.rule("[bold blue]Black River — MPP Consumer Agent[/bold blue]")
    console.print(
        f"\n  Broker : [cyan]{broker_url}[/cyan]\n"
        f"  Budget : [yellow]${args.budget:,.2f} USDC[/yellow]\n"
        f"  Mode   : {'[dim]dry-run (no payment)[/dim]' if args.dry_run else '[green]live MPP[/green]'}\n"
    )

    try:
        if args.dry_run:
            result = asyncio.run(run_ungated(broker_url, requirement))
        else:
            if not consumer_key:
                console.print(
                    "[red]✗  CONSUMER_PRIVATE_KEY (or DEPLOYER_PRIVATE_KEY) not set.[/red]\n"
                    "   Set it in .env.tempo_testnet or use --dry-run for ungated testing.\n"
                )
                sys.exit(1)
            result = asyncio.run(run_mpp(broker_url, consumer_key, requirement, rpc_url))

        _print_result(result)

    except RuntimeError as e:
        console.print(Panel(
            f"[bold red]✗ Procurement failed[/bold red]\n\n{e}",
            title="[red]Error[/red]",
            border_style="red",
        ))
        sys.exit(1)
    except Exception as e:
        logger.exception("[CONSUMER] Unexpected error")
        sys.exit(1)


if __name__ == "__main__":
    main()
