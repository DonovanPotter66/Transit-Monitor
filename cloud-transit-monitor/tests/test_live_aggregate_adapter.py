import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "orchestrator"))

from live_aggregate_adapter import ensure_mbta_rows  # noqa: E402
from src.config import SOURCES  # noqa: E402
from src.models import CheckResult  # noqa: E402


class LiveAggregateAdapterTests(unittest.TestCase):
    def test_mbta_failed_check_uses_verified_baseline_rows(self):
        source = next(item for item in SOURCES if item.agency == "MBTA")
        failed = CheckResult(source=source, status="Failed", error="blocked")

        with patch("live_aggregate_adapter._mbta_static_html_records", return_value=[]):
            recovered = asyncio.run(ensure_mbta_rows(failed))

        self.assertEqual(recovered.status, "Successful via MBTA verified baseline fallback")
        self.assertEqual([item.opportunity_id for item in recovered.opportunities], ["Z94PS35-XX", "X14PS01"])
        self.assertTrue(all(item.notes for item in recovered.opportunities))


if __name__ == "__main__":
    unittest.main()
