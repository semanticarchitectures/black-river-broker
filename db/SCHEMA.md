# Black River Broker — Data Schema Design

## Overview

The schema models three interacting systems: the **supply side** (producers
the broker has discovered and qualifies), the **demand side** (consumers with
projects), and the **broker's own activity** (research, outreach, negotiation,
monitoring, and issue resolution).

The broker's value compounds over time: every research run, every negotiated
deal, and every resolved issue adds signal that makes subsequent work faster
and better calibrated.

---

## Entity Map

```
SUPPLY SIDE                    BROKER ACTIVITY              DEMAND SIDE
───────────────────────────    ──────────────────────────   ──────────────────────
Producer                       ResearchRun                  Consumer
 ├── ProducerContact             └── ResearchFinding          ├── ConsumerContact
 ├── ProducerCapability                                        └── Project
 ├── ProducerCoverageArea       OutreachRecord                    ├── ProjectPhase
 ├── ProducerCertification                                        │    └── ProjectRequirement
 └── ProducerPricingRecord      BrokerEvent (audit log)          └── Deal
                                                                      ├── TaskOrder
PERFORMANCE & ISSUES                                                  └── PerformanceRecord
─────────────────────
PerformanceRecord  ←──── feeds ──── Producer.reliability_score
Issue
 └── IssueEvent
```

---

## Entity Reference

### Producer

The central supply-side entity. State machine:

```
discovered → contacted → responded → qualified → active → dormant → inactive
```

Key fields:
- `reliability_score` (0.0–1.0): computed from `PerformanceRecord`s; updated
  after every completed or disputed engagement
- `description_embedding`: vector(1536) for semantic search — embed a consumer
  requirement and find producers by cosine similarity
- `last_researched`: the broker re-researches producers that haven't been
  checked recently, or when a new capability claim needs verification

**Reliability score formula** (suggested starting point):

```
score = (
    0.4 × on_time_rate          # fraction of deliveries on time
  + 0.3 × quality_met_rate      # fraction meeting quality SLA
  + 0.2 × avg_communication     # mean communication_score / 5
  + 0.1 × response_rate         # fraction of outreach that got a reply
)
```

Decay factor: weight recent records more heavily (e.g., exponential decay
with half-life of 12 months) so a producer can recover from past issues.

---

### ProducerCapability

One row per service a producer offers. Each row has:
- `service_type`: canonical enum for structured queries
- `capability_detail`: free text describing exactly how they deliver it
- `capability_embedding`: vector for semantic matching against
  `ProjectRequirement.requirement_embedding`

**Semantic matching workflow:**

```
1. Consumer describes requirement in natural language
2. Broker embeds the description → ProjectRequirement.requirement_embedding
3. Query: SELECT producer_id, capability_detail,
          1 - (capability_embedding <=> $req_embedding) AS similarity
          FROM producer_capabilities
          WHERE service_type = $type
          ORDER BY similarity DESC LIMIT 20
4. Filter by ProducerCoverageArea matching project site
5. Filter by Producer.reliability_score >= threshold
6. Return ranked shortlist to broker agent for RFQ
```

---

### Project + ProjectPhase

A project is the consumer's top-level engagement. Phases model the temporal
structure of complex work.

**Construction project example:**

| Seq | Phase Name                  | Service Type         | Frequency  | Est. Duration |
|-----|-----------------------------|----------------------|------------|---------------|
| 1   | Site Survey & Baseline      | photogrammetry       | once       | week 1        |
| 2   | Excavation Progress         | construction_progress| weekly     | weeks 2–8     |
| 3   | Structural Framing          | construction_progress| bi-weekly  | weeks 8–24    |
| 4   | Envelope & Facade           | thermal_imaging      | once       | week 24       |
| 5   | Punch List Documentation    | aerial_photography   | once       | week 36       |

Each phase becomes either:
- A **spot deal** (for one-off phases)
- A **task order** under a **master agreement** (for recurring phases with the
  same producer)

The broker decides which structure to propose based on phase count, duration,
and whether the consumer wants pricing certainty upfront.

---

### Deal + TaskOrder

`Deal` records a negotiated agreement. `deal_type` determines structure:

| Type               | When to use                                          | Price field        |
|--------------------|------------------------------------------------------|--------------------|
| `spot`             | Single phase, one-time                               | `price_total_usd`  |
| `master_agreement` | Multi-phase with same producer                       | `price_per_unit_usd` |
| `retainer`         | Ongoing service level (e.g. monthly surveillance)    | `price_total_usd` per period |

Under a `master_agreement`, each `TaskOrder` carries its own price (which may
differ from the master rate for complexity).

**Private broker fields** on Deal:
- `private_broker_notes`: negotiation context (never surfaced)
- `consumer_budget_at_negotiation`: what the broker knew the consumer's ceiling
  was when the deal was struck
- `producer_floor_at_negotiation`: minimum the producer would accept

These fields are the mechanism for the information asymmetry demonstrated in
`proto_interfaces.py`. They also enable post-deal analytics: was the broker
leaving money on the table, or finding genuinely good outcomes for both sides?

---

### PerformanceRecord + Reliability Scoring

Written by the broker after each deliverable. Feeds back into
`Producer.reliability_score`.

The broker should write a `PerformanceRecord` for:
- Each `TaskOrder` completion (or failure)
- Each `Issue` resolution
- Each check-in response from consumer that mentions quality

`consumer_satisfaction` (1–5) is the consumer's explicit rating; if not
provided, the broker infers it from the check-in conversation content.

---

### Issue + IssueEvent

Issues follow a state machine:

```
detected
   │
   ▼
notified ──────────────────────────────────────────────┐
   │                                                   │
   ▼                                                   │
mediating                                              │
   │                                                   │
   ├── resolved ──────────────────────────────── closed│
   │                                                   │
   └── escalated                                       │
         │                                             │
         ▼                                             │
       replacement_found ────────────────────── closed ┘
```

`IssueEvent` is append-only — every status transition, communication sent,
and broker decision is logged. This record is critical for two reasons:
1. Dispute resolution: complete evidence trail
2. Producer learning: patterns in how a producer handles issues are as
   informative as whether issues occur in the first place

---

### Research + Outreach

`ResearchRun` captures one execution of the broker's discovery agent:
- triggered by scheduler (weekly), consumer request, or manual command
- scoped to a `service_type` and `region` if the request came from a consumer
- all discovered producers flow through `ResearchFinding` before being
  created or updated in `producers`

`OutreachRecord` logs every communication to a producer. `outreach_type`:
- `initial_discovery`: first contact after finding via research
- `rfq`: sending a request for quote for a specific project
- `follow_up`: chasing a non-response
- `check_in`: periodic relationship maintenance
- `issue_notification`: alerting a producer to a consumer complaint

`response_summary` is the broker's digest of a reply — key facts extracted,
not the raw email. This keeps the broker's knowledge structured and searchable.

---

### BrokerEvent (Audit Log)

Append-only record of every significant action. Never updated, never deleted.

`event_type` is dot-namespaced:

```
research.run_started           research.producer_discovered
outreach.sent                  outreach.response_received
negotiation.rfq_issued         negotiation.deal_proposed
negotiation.deal_accepted      negotiation.deal_rejected
project.phase_started          project.checkin_sent
project.checkin_received
issue.detected                 issue.notified
issue.mediation_started        issue.resolved
issue.escalated                issue.replacement_contracted
payment.settlement_initiated   payment.settlement_confirmed
```

---

## Database Notes

**Target: PostgreSQL 15+ with pgvector extension**

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

**Key indexes to create beyond primary keys:**

```sql
-- Semantic search
CREATE INDEX ON producer_capabilities USING ivfflat (capability_embedding vector_cosine_ops);
CREATE INDEX ON project_requirements  USING ivfflat (requirement_embedding vector_cosine_ops);
CREATE INDEX ON producers             USING ivfflat (description_embedding  vector_cosine_ops);

-- Monitoring loop
CREATE INDEX ON projects (next_checkin_at) WHERE status = 'active';

-- Research freshness
CREATE INDEX ON producers (last_researched) WHERE is_active = true;

-- Issue triage
CREATE INDEX ON issues (status, severity) WHERE status NOT IN ('resolved','closed');

-- Outreach follow-up
CREATE INDEX ON outreach_records (sent_at, status) WHERE status = 'sent';
```

**SQLite (development):** Use `db/models.py` directly — `_VEC` falls back to
`Text` when pgvector is unavailable. Semantic search won't work but all other
functionality does. Use Docker for a local Postgres instance in integration
testing.

---

## Migration Strategy

Use Alembic (`pip install alembic`). Init with:

```bash
alembic init db/migrations
# Set sqlalchemy.url in alembic.ini
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

Seed reference data after first migration:

```bash
python db/seed/load_seed.py
```

---

## Phase 3 Build Order

Given this schema, the recommended build order is:

1. **DB setup**: Postgres + pgvector via Docker, Alembic migrations, seed loader
2. **Registry layer**: `db/registry.py` — CRUD and search queries over producers/capabilities
3. **Research agent**: web search → `ResearchRun` + `ResearchFinding` → upsert `Producer`
4. **Outreach agent**: email identity, `OutreachRecord` writer, response parser
5. **Extended broker**: connect `BrokerAgent` (from `agents/interfaces/base.py`) to registry
   for producer discovery and RFQ dispatch
6. **Project monitor**: cron/scheduler reads `projects.next_checkin_at`, dispatches check-ins
7. **Issue resolver**: activates on issue detection, runs resolution state machine
8. **Analytics layer**: reliability score computation, market rate benchmarks
