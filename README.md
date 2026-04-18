# Project Black River — Phase 1 Scaffold

Agent-based autonomous procurement broker on the Tempo blockchain.

## Quick Start

### Prerequisites
- Node.js 20+
- Python 3.11+
- An Anthropic API key

### 1. Install dependencies

```bash
# Smart contract layer
npm install

# Agent layer
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env
```

### 3. Start local chain and deploy contracts

```bash
# Terminal 1 — local Hardhat node
npx hardhat node

# Terminal 2 — deploy contracts
npx hardhat run scripts/deploy.ts --network localhost
```

### 4. Run the demo

```bash
# Full demo (requires Hardhat node running)
python demo.py

# Dry-run (no chain writes — for CI / quick smoke test)
python demo.py --dry-run
```

## Project Structure

```
black-river/
├── contracts/
│   ├── MockUSDC.sol          # ERC20 mock token (Phase 1 only)
│   ├── AuditLog.sol          # Immutable on-chain audit trail
│   └── BlackRiverBroker.sol  # Procurement escrow + award contract
├── scripts/
│   └── deploy.ts             # Hardhat deployment script
├── agents/
│   ├── interfaces/
│   │   ├── producer_adapter.py   # ← THE critical boundary
│   │   └── consumer_adapter.py
│   ├── broker/
│   │   ├── broker_graph.py   # LangGraph CNP state machine
│   │   └── chain_client.py   # web3 wrapper
│   ├── consumer/
│   │   └── consumer_simulator.py
│   ├── producers/
│   │   ├── drone_ops_producer.py
│   │   └── drone_service_producer.py
│   └── mpp/
│       └── mpp_simulator.py  # Phase 1 MPP stand-in
├── shared/
│   └── cnp_messages.py       # Pydantic CNP message schemas
├── demo.py                   # Entry point
├── requirements.txt
├── package.json
└── hardhat.config.ts
```

## Phase Roadmap

| Phase | What changes |
|-------|-------------|
| **1 — Emulation** | Everything runs locally. You are here. |
| **2 — Tempo testnet** | Swap `ChainClient` RPC URL. `MPPSimulator` → real MPP client. |
| **3 — Partner APIs** | Implement real `ProducerAdapter` subclasses backed by partner APIs. |
| **4 — Live demo** | Add web UI `ConsumerAdapter`. Polish scripted scenarios. |

## Adding a New Producer (Phase 3)

1. Subclass `ProducerAdapter` in `agents/producers/your_producer.py`
2. Implement all five methods (`can_fulfil`, `submit_bid`, `accept_award`, `get_status`, `confirm_delivery`)
3. Register the instance in `demo.py`'s `producers` list
4. Write integration tests in `test/test_your_producer.py`

The broker never needs to change.
