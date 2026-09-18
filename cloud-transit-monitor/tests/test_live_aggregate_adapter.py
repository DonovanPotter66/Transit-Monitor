import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "orchestrator"))

from live_aggregate_adapter import ensure_mbta_rows, valid_source_row  # noqa: E402
from site_publish import valid_opportunity_row as valid_site_row  # noqa: E402
from workbook_publish_portable import valid_opportunity_row as valid_workbook_row  # noqa: E402
from src.config import SOURCES  # noqa: E402
from src.models import CheckResult, Opportunity  # noqa: E402


class LiveAggregateAdapterTests(unittest.TestCase):
    def test_mbta_failed_check_uses_verified_baseline_rows(self):
        source = next(item for item in SOURCES if item.agency == "MBTA")
        failed = CheckResult(source=source, status="Failed", error="blocked")

        with patch("live_aggregate_adapter._mbta_static_html_records", return_value=[]):
            recovered = asyncio.run(ensure_mbta_rows(failed))

        self.assertEqual(recovered.status, "Successful via MBTA verified baseline fallback")
        self.assertEqual([item.opportunity_id for item in recovered.opportunities], ["Z94PS35-XX", "X14PS01"])
        self.assertTrue(all(item.notes for item in recovered.opportunities))

    def test_sound_transit_procurement_ids_pass_publish_validation(self):
        for oid in ("RP 0110-24", "GC 0124-26", "CN 0003-26", "DB 0226-25", "IB 0112-26", "AE 0096-26"):
            opportunity = Opportunity(
                agency="Sound Transit",
                source_name="Sound Transit Procurement Snapshot",
                source_url="https://www.soundtransit.org/sites/default/files/documents/snapshot-current.pdf",
                opportunity_id=oid,
                project_name="Light Rail Vehicle Series 3",
            )
            row = {
                "agency": opportunity.agency,
                "opportunity_id": opportunity.opportunity_id,
                "project_name": opportunity.project_name,
            }
            self.assertTrue(valid_source_row(opportunity), oid)
            self.assertTrue(valid_workbook_row(row), oid)
            self.assertTrue(valid_site_row(row), oid)


if __name__ == "__main__":
    unittest.main()
