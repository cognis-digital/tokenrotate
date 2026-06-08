"""Core rotation-planning engine for TOKENROTATE.

Pure standard-library logic. The engine takes an inventory of secret metadata
and computes, per secret:

  * age in days (from last_rotated)
  * the rotation interval that applies (per-secret override, else per-provider
    default, else a global fallback)
  * days until / past due
  * a status bucket: ok | due_soon | overdue | unknown
  * a priority score used to order the rotation plan

No secret *values* are ever required or processed -- only metadata.
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# Sensible default rotation cadence (in days) per provider class. These are
# conservative, real-world-ish defaults; any secret may override via
# "rotation_days" and any provider may be overridden in the inventory file.
DEFAULT_PROVIDER_INTERVALS: Dict[str, int] = {
    "aws": 90,
    "gcp": 90,
    "azure": 90,
    "github": 180,
    "gitlab": 180,
    "stripe": 365,
    "datadog": 180,
    "slack": 365,
    "pagerduty": 365,
    "database": 90,
    "ssh": 365,
    "tls": 365,
    "generic": 90,
}

GLOBAL_FALLBACK_DAYS = 90
DUE_SOON_WINDOW_DAYS = 14

# Severity weights bump priority for high-value credentials.
_SEVERITY_WEIGHT = {"critical": 3.0, "high": 2.0, "medium": 1.0, "low": 0.5}


def _today() -> _dt.date:
    return _dt.date.today()


def _parse_date(value: Optional[str]) -> Optional[_dt.date]:
    if not value:
        return None
    value = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return _dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    # ISO timestamp fallback (YYYY-MM-DDTHH:MM:SS...)
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


@dataclass
class Secret:
    """Metadata for a single secret/credential. No value, ever."""

    name: str
    provider: str = "generic"
    last_rotated: Optional[str] = None
    rotation_days: Optional[int] = None
    severity: str = "medium"
    owner: Optional[str] = None
    environment: Optional[str] = None
    notes: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Secret":
        if "name" not in d or not str(d.get("name", "")).strip():
            raise ValueError("secret entry missing required 'name' field")
        rd = d.get("rotation_days")
        if rd is not None:
            try:
                rd = int(rd)
            except (TypeError, ValueError):
                raise ValueError(
                    "secret %r has non-integer rotation_days %r"
                    % (d.get("name"), d.get("rotation_days"))
                )
            if rd <= 0:
                raise ValueError(
                    "secret %r has non-positive rotation_days" % d.get("name")
                )
        sev = str(d.get("severity", "medium")).lower()
        if sev not in _SEVERITY_WEIGHT:
            sev = "medium"
        return cls(
            name=str(d["name"]).strip(),
            provider=str(d.get("provider", "generic")).strip().lower() or "generic",
            last_rotated=d.get("last_rotated"),
            rotation_days=rd,
            severity=sev,
            owner=d.get("owner"),
            environment=d.get("environment"),
            notes=d.get("notes"),
        )


@dataclass
class Inventory:
    secrets: List[Secret]
    provider_intervals: Dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_PROVIDER_INTERVALS)
    )

    def interval_for(self, secret: Secret) -> int:
        if secret.rotation_days is not None:
            return secret.rotation_days
        if secret.provider in self.provider_intervals:
            return self.provider_intervals[secret.provider]
        return GLOBAL_FALLBACK_DAYS


@dataclass
class PlanItem:
    name: str
    provider: str
    severity: str
    status: str  # ok | due_soon | overdue | unknown
    interval_days: int
    last_rotated: Optional[str]
    age_days: Optional[int]
    days_until_due: Optional[int]  # negative => overdue
    priority: float
    owner: Optional[str] = None
    environment: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RotationPlan:
    generated_on: str
    items: List[PlanItem]

    @property
    def overdue(self) -> List[PlanItem]:
        return [i for i in self.items if i.status == "overdue"]

    @property
    def due_soon(self) -> List[PlanItem]:
        return [i for i in self.items if i.status == "due_soon"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_on": self.generated_on,
            "items": [i.to_dict() for i in self.items],
        }


def load_inventory(path: str) -> Inventory:
    """Load an inventory from a JSON file.

    Accepted shapes:
      * {"secrets": [ ... ], "provider_intervals": { ... }}
      * [ {secret}, {secret}, ... ]   (a bare list of secrets)
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    provider_intervals = dict(DEFAULT_PROVIDER_INTERVALS)
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        entries = raw.get("secrets", [])
        overrides = raw.get("provider_intervals", {})
        if not isinstance(overrides, dict):
            raise ValueError("'provider_intervals' must be an object")
        for k, v in overrides.items():
            try:
                iv = int(v)
            except (TypeError, ValueError):
                raise ValueError("provider_intervals[%r] is not an integer" % k)
            if iv <= 0:
                raise ValueError("provider_intervals[%r] must be positive" % k)
            provider_intervals[str(k).strip().lower()] = iv
    else:
        raise ValueError("inventory must be a JSON object or array")

    if not isinstance(entries, list):
        raise ValueError("'secrets' must be an array")

    secrets = [Secret.from_dict(e) for e in entries]
    return Inventory(secrets=secrets, provider_intervals=provider_intervals)


def _build_item(inv: Inventory, secret: Secret, today: _dt.date) -> PlanItem:
    interval = inv.interval_for(secret)
    last = _parse_date(secret.last_rotated)
    weight = _SEVERITY_WEIGHT.get(secret.severity, 1.0)

    if last is None:
        # Never rotated / unparseable date -> treat as unknown but high priority.
        return PlanItem(
            name=secret.name,
            provider=secret.provider,
            severity=secret.severity,
            status="unknown",
            interval_days=interval,
            last_rotated=secret.last_rotated,
            age_days=None,
            days_until_due=None,
            priority=round(100.0 * weight, 3),
            owner=secret.owner,
            environment=secret.environment,
        )

    age = (today - last).days
    days_until = interval - age  # negative => overdue by that many days

    if days_until < 0:
        status = "overdue"
    elif days_until <= DUE_SOON_WINDOW_DAYS:
        status = "due_soon"
    else:
        status = "ok"

    # Priority: how far through (or past) the rotation window, scaled by
    # severity. Overdue secrets exceed 100 * weight; fresh ones near 0.
    pct_through = (age / interval) * 100.0 if interval else 0.0
    priority = round(pct_through * weight, 3)

    return PlanItem(
        name=secret.name,
        provider=secret.provider,
        severity=secret.severity,
        status=status,
        interval_days=interval,
        last_rotated=secret.last_rotated,
        age_days=age,
        days_until_due=days_until,
        priority=priority,
        owner=secret.owner,
        environment=secret.environment,
    )


def build_plan(inv: Inventory, today: Optional[_dt.date] = None) -> RotationPlan:
    """Compute the rotation plan, ordered by descending priority."""
    today = today or _today()
    items = [_build_item(inv, s, today) for s in inv.secrets]
    # Highest priority first; unknown (never-rotated) float to the top via 100*w.
    items.sort(key=lambda i: i.priority, reverse=True)
    return RotationPlan(generated_on=today.isoformat(), items=items)


def summarize(plan: RotationPlan) -> Dict[str, Any]:
    """Roll-up counts for reporting / exit-code decisions."""
    counts = {"ok": 0, "due_soon": 0, "overdue": 0, "unknown": 0}
    by_provider: Dict[str, int] = {}
    for item in plan.items:
        counts[item.status] = counts.get(item.status, 0) + 1
        if item.status in ("overdue", "unknown"):
            by_provider[item.provider] = by_provider.get(item.provider, 0) + 1
    return {
        "total": len(plan.items),
        "counts": counts,
        "actionable": counts["overdue"] + counts["unknown"],
        "overdue_by_provider": by_provider,
        "generated_on": plan.generated_on,
    }
