"""
agents/producers
────────────────
Producer adapter registry.

Phase 1/2 (simulated):
  DroneOpsSoftwareProducer  — AeroPlan Systems (hard-coded pricing)
  DroneServiceProducer      — SkyWatch Operations (hard-coded pricing)

Phase 3 (real partner APIs):
  DroneDeployProducer       — DroneDeploy REST API
  SkydioProducer            — Skydio Cloud API

Import the concrete class you need; the broker works with the abstract
ProducerAdapter interface and never imports from here directly.
"""

from agents.producers.drone_ops_producer import DroneOpsSoftwareProducer
from agents.producers.drone_service_producer import DroneServiceProducer
from agents.producers.dronedeploy_producer import DroneDeployProducer
from agents.producers.skydio_producer import SkydioProducer

__all__ = [
    "DroneOpsSoftwareProducer",
    "DroneServiceProducer",
    "DroneDeployProducer",
    "SkydioProducer",
]
