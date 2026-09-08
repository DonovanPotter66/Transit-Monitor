"""Single-source MBTA acquisition adapter.

This deliberately stops at acquisition. It never opens or writes a workbook;
the orchestrator owns hashing, change detection, normalization, and publishing.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONITOR_SRC = ROOT / "cloud-transit-monitor" / "src"
sys.path.insert(0, str(MONITOR_SRC.parent))

from src.config import SOURCES  # type: ignore
from src.extract import run_checks  # type: ignore


def iso(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def workbook_status(value: str) -> str:
    text = str(value or "").lower()
    if "active" in text or "future" in text or "open" in text or "listed" in text:
        return "Open"
    if "award" in text or "closed" in text:
        return "Closed"
    return "Unknown"


async def collect():
    mbta = next((source for source in SOURCES if source.agency == "MBTA"), None)
    if mbta is None:
        raise RuntimeError("MBTA source is not configured")
    results = await run_checks((mbta,))
    result = results[0]
    if result.status == "Failed":
        raise RuntimeError(result.error or "MBTA source check failed")
    opportunities = []
    for item in result.opportunities:
        opportunities.append({
            "agency": item.agency,
            "source_name": item.source_name,
            "source_url": item.source_url,
            "opportunity_id": item.opportunity_id,
            "project_name": item.project_name,
            "description": item.description,
            "posted_date": iso(item.posted_date),
            "due_date": iso(item.due_date),
            "status": workbook_status(item.status),
            "priority": item.priority,
            "pgh_wong_relevance": item.relevance,
            "change_status": item.change_status,
            "last_seen_date": datetime.now(timezone.utc).date().isoformat(),
            "source_url": item.source_url,
            "opportunity_url": item.opportunity_url or item.source_url,
            "notes": item.notes,
        })
    checked = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "schema_version": "monitor-workbook-acquisition-v1",
        "run": {"run_id": f"mbta-live-{checked.strftime('%Y%m%d')}", "run_date": checked.date().isoformat(), "status": "Complete"},
        "sources": [{
            "agency": result.source.agency,
            "source_name": result.source.name,
            "source_url": result.source.url,
            "checked_at": checked.isoformat(),
            "check_result": "success",
            "last_successful_check": checked.isoformat(),
            "items_found": len(opportunities),
            "consecutive_failures": 0,
            "failure_reason": "",
            "health_indicator": "Healthy",
        }],
        "opportunities": opportunities,
        "changes": [],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        payload = asyncio.run(collect())
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"mbta_acquisition_failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
