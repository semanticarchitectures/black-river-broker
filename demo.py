"""
demo.py
────────
Black River Demo Runner — Phase 2.

Runs a complete end-to-end procurement cycle:
  consumer → broker → producers → chain (Base Sepolia or local Hardhat)

Usage:
  # Local Hardhat (Phase 1 workflow, still supported)
  npx hardhat node                         # terminal 1
  node scripts/deploy_local.js             # terminal 2
  python demo.py

  # Base Sepolia testnet (Phase 2)
  node scripts/deploy_tempo_testnet.js     # one-time deploy
  python demo.py --env .env.tempo_testnet

  # Dry-run (no chain writes — for CI / quick smoke tests)
  python demo.py --dry-run
"""

import argparse
import logging
import os
import sys
from dotenv import load_dotenv

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)


def main(dry_run: bool = False, env_file: str | None = None) -> None:
    # ── Load environment ──────────────────────────────────────────────────
    # Priority: explicit --env file > .env.local (local deploy output) > .env
    if env_file:
        load_dotenv(env_file, override=True)
    load_dotenv(".env.local",          override=not bool(env_file))
    load_dotenv(".env",                override=False)

    # ── Import after env is loaded ────────────────────────────────────────
    from shared.cnp_messages import CNPStage
    from agents.broker.broker_graph import BrokerAgent
    from agents.broker.chain_client import ChainClient
    from agents.consumer.consumer_simulator import ConsumerSimulator
    from agents.producers.drone_ops_producer import DroneOpsSoftwareProducer
    from agents.producers.drone_service_producer import DroneServiceProducer

    rpc_url = (
        os.getenv("TEMPO_TESTNET_RPC")
        or os.getenv("HARDHAT_RPC", "http://127.0.0.1:8545")
    )

    network_label = (
        "Tempo testnet"  if "tempo.xyz" in rpc_url
        else "local Hardhat" if "127.0.0.1" in rpc_url or "localhost" in rpc_url
        else rpc_url
    )

    console.rule(f"[bold blue]Project Black River — Phase 2 Demo  [{network_label}][/bold blue]")
    console.print()

    # ── Wire up components ────────────────────────────────────────────────
    consumer  = ConsumerSimulator()
    producers = [DroneOpsSoftwareProducer(), DroneServiceProducer()]

    chain = ChainClient(
        rpc_url=              rpc_url,
        broker_wallet=        os.getenv("BROKER_WALLET", ""),
        private_key=          os.getenv("DEPLOYER_PRIVATE_KEY", ""),
        audit_log_addr=       os.getenv("AUDIT_LOG_ADDRESS", ""),
        broker_contract_addr= os.getenv("BROKER_CONTRACT_ADDRESS", ""),
        usdc_addr=            os.getenv("MOCK_USDC_ADDRESS", ""),
        dry_run=              dry_run,
    )

    broker = BrokerAgent(
        producers=     producers,
        consumer=      consumer,
        chain=         chain,
        broker_wallet= os.getenv("BROKER_WALLET", "0x0000000000000000000000000000000000000001"),
        dry_run=       dry_run,
    )

    # ── Get requirement from consumer ─────────────────────────────────────
    requirement = consumer.get_requirement()

    console.print(Panel(
        f"[bold]{requirement.description}[/bold]\n\n"
        f"Location:   {requirement.location.description}\n"
        f"Duration:   {requirement.start_time.date()} → {requirement.end_time.date()}\n"
        f"Frequency:  {requirement.coverage_frequency.value}\n"
        f"Budget:     ${requirement.budget_ceiling_usdc:,.2f} USDC\n"
        f"Criteria:   price {requirement.criteria.price_weight*100:.0f}% / "
        f"availability {requirement.criteria.availability_weight*100:.0f}% / "
        f"performance {requirement.criteria.past_performance_weight*100:.0f}%",
        title=f"[cyan]Consumer Requirement  [{requirement.requirement_id}][/cyan]",
        border_style="cyan",
    ))
    console.print()

    # ── Run the broker ────────────────────────────────────────────────────
    console.print("[bold yellow]Starting procurement cycle...[/bold yellow]\n")
    result = broker.run(requirement)

    # ── Print audit trail ─────────────────────────────────────────────────
    console.print()
    console.rule("[bold green]Audit Trail[/bold green]")

    audit_table = Table(box=box.ROUNDED, show_lines=True)
    audit_table.add_column("Stage",    style="bold cyan",  width=12)
    audit_table.add_column("Actor",    style="dim",        width=10)
    audit_table.add_column("Summary",                      width=52)
    audit_table.add_column("Tx Hash",  style="dim green",  width=12)

    for event in result["audit_events"]:
        tx = (event.tx_hash or "")[:10] + "…" if event.tx_hash else "(dry-run)"
        audit_table.add_row(
            event.stage.value,
            event.actor[:10],
            event.summary[:80],
            tx,
        )

    console.print(audit_table)

    # ── Final status ──────────────────────────────────────────────────────
    console.print()
    final_stage = result["stage"]
    if final_stage == CNPStage.SETTLED:
        award      = result["award"]
        settlement = result["settlement"]
        console.print(Panel(
            f"[bold green]✓ Procurement complete[/bold green]\n\n"
            f"Winner:   {award.producer_id}\n"
            f"Amount:   ${award.price_usdc:,.2f} USDC\n"
            f"Tx hash:  {settlement.tx_hash[:30]}…\n"
            f"Memo:     {settlement.memo}",
            title="[green]Settlement Confirmed[/green]",
            border_style="green",
        ))
    else:
        reason = result.get("failure_reason", "Unknown")
        console.print(Panel(
            f"[bold red]✗ Procurement failed[/bold red]\n\nReason: {reason}",
            title="[red]Failure[/red]",
            border_style="red",
        ))
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Black River Demo")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip on-chain writes (useful for CI and quick tests)",
    )
    parser.add_argument(
        "--env", metavar="FILE", default=None,
        help="Env file to load (e.g. .env.tempo_testnet). "
             "Overrides .env.local / .env.",
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run, env_file=args.env)
