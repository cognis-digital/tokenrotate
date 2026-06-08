"""TOKENROTATE -- Plan and track secret rotation across providers.

Defensive / authorized-use tooling: this package only reads an inventory of
secret *metadata* (names, providers, ages, severities) and produces a rotation
plan, a status report, and overdue findings. It never reads, transmits, or
attacks secret material.
"""
from .core import (
    Secret,
    RotationPlan,
    PlanItem,
    Inventory,
    load_inventory,
    build_plan,
    summarize,
    DEFAULT_PROVIDER_INTERVALS,
)

TOOL_NAME = "tokenrotate"
TOOL_VERSION = "1.0.0"

__all__ = [
    "Secret",
    "RotationPlan",
    "PlanItem",
    "Inventory",
    "load_inventory",
    "build_plan",
    "summarize",
    "DEFAULT_PROVIDER_INTERVALS",
    "TOOL_NAME",
    "TOOL_VERSION",
]
