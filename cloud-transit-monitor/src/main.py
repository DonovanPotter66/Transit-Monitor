from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import SOURCES, Settings
from .extract import run_checks
from .graph import GraphClient
from .workbook import update_workbook


def digest(results, metrics, run_date) -> str:
    lines = [f"Transit Agency Monitor — {run_date.isoformat()}", ""]
    for agency in (
        "MBTA", "BART", "WMATA", "MTA", "MARTA", "CTA", "DART", "RTD Denver",
        "SEPTA", "UTA", "Sound Transit", "LA Metro",
    ):
        agency_results = [result for result in results if result.source.agency == agency]
        failures = [result for result in agency_results if result.status == "Failed"]
        if failures:
            lines.append(f"{agency}: {len(failures)} source check(s) failed; prior baselines were preserved.")
        else:
            count = sum(len(result.opportunities) for result in agency_results if result.status.startswith("Successful"))
            lines.append(f"{agency}: checks successful; {count} source rows observed.")
    lines.extend([
        "",
        f"Agencies with changes: {metrics['agencies_with_changes']}",
        f"High-priority changes: {metrics['high_priority_changes']}",
        f"Medium-priority changes: {metrics['medium_priority_changes']}",
        f"Failed checks: {metrics['failed_checks']}",
        "Workbook update completed." if not metrics["failed_checks"] else "Workbook updated with failed-source baselines preserved.",
    ])
    return "\n".join(lines)


async def execute() -> dict:
    settings = Settings.from_env()
    settings.validate()
    now = datetime.now(tz=ZoneInfo(settings.timezone))
    if not settings.force_run and not settings.within_run_window(now):
        return {"status": "skipped", "reason": "Outside configured local run window", "local_time": now.isoformat()}

    with tempfile.TemporaryDirectory(prefix="transit-monitor-") as temp_dir:
        temp = Path(temp_dir)
        input_path = temp / settings.workbook_name
        output_path = temp / f"updated-{settings.workbook_name}"
        graph = None
        etag = ""
        if settings.dry_run:
            shutil.copy2(settings.local_workbook_path, input_path)
        else:
            graph = GraphClient(settings.graph_drive_id, settings.workbook_item_id)
            etag = graph.download(input_path)

        results = await run_checks(SOURCES)
        verified_count = sum(len(result.opportunities) for result in results)
        if verified_count == 0:
            # Never replace the deliverable with a blank/empty workbook when
            # extraction failed across the sources.  The run must surface the
            # source errors instead of treating zero rows as a valid update.
            raise RuntimeError("No verified opportunities were extracted; refusing empty workbook publication.")
        metrics = update_workbook(input_path, output_path, results, now.date())
        report = digest(results, metrics, now.date())

        if settings.dry_run:
            dry_output = Path(settings.local_workbook_path).with_name("Transit Agency Monitor.cloud-test.xlsx")
            shutil.copy2(output_path, dry_output)
            uploaded = {"dry_run_output": str(dry_output)}
        else:
            uploaded = graph.upload(output_path, etag)
            graph.send_mail(
                settings.mail_sender,
                settings.mail_recipients,
                f"Transit Agency Monitor — {now.date().isoformat()}",
                report,
            )
        return {
            "status": "completed",
            "run_date": now.date().isoformat(),
            "metrics": metrics,
            "source_results": [
                {
                    "agency": result.source.agency,
                    "source": result.source.name,
                    "status": result.status,
                    "items": len(result.opportunities),
                    "error": result.error,
                }
                for result in results
            ],
            "workbook": uploaded,
            "digest": report,
        }


def main() -> None:
    print(json.dumps(asyncio.run(execute()), indent=2, default=str))


if __name__ == "__main__":
    main()
