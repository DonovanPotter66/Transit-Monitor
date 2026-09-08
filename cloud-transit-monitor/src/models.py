from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class Source:
    agency: str
    name: str
    url: str
    mode: str = "grid"
    markers: tuple[str, ...] = ()
    verification_urls: tuple[str, ...] = ()
    priority: str = "primary"
    # Secondary pages may corroborate discovery but must not be promoted as
    # primary procurement records.
    secondary_policy: str = ""


@dataclass
class Opportunity:
    agency: str
    source_name: str
    source_url: str
    opportunity_id: str
    project_name: str
    description: str = ""
    posted_date: date | None = None
    due_date: date | None = None
    status: str = "Active"
    change_status: str = "No material change"
    priority: str = "Low"
    relevance: str = ""
    notes: str = ""
    opportunity_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return self.agency, self.opportunity_id


@dataclass
class CheckResult:
    source: Source
    status: str
    opportunities: list[Opportunity] = field(default_factory=list)
    error: str = ""
    retried: bool = False
