import unittest
import sys
from pathlib import Path

from src.extract import _bart_records_from_grid, normalize
from src.models import Source

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from orchestrator.site_publish import clean_opportunity


class StatusFieldsTests(unittest.TestCase):
    headers = ["Solicitation Id", "Event Name", "Start Date", "End Date",
               "Status/Due Date", "Status", "Program Name", "Contract Type", "Electronic Bid"]

    def row(self, status="", program="NA"):
        return ["BARTD-9151", "MUX conductor cable", "09/17/2026", "10/20/2026",
                "10/20/2026", status, program, "Invitation for Bid (IFB)", "No", ""]

    def test_programs_never_become_statuses(self):
        source = Source("BART", "View Active Solicitations", "https://suppliers.bart.gov")
        for program in ["NA", "DBE", "MICR", "MWBE,SB", "SB", "SBE"]:
            with self.subTest(program=program):
                records = _bart_records_from_grid(self.headers, [self.row(program=program)])
                self.assertEqual(records[0]["Program Name"], program)
                item = normalize(source, records)[0]
                self.assertEqual(item.status, "Unknown / Not provided")
                self.assertEqual(item.raw["Program Name"], program)

    def test_real_status_preserved(self):
        records = _bart_records_from_grid(self.headers, [self.row("Accepted", "DBE")])
        self.assertEqual(normalize(Source("BART", "Bids", "url"), records)[0].status, "Accepted")

    def test_changed_columns_fail_closed(self):
        with self.assertRaises(ValueError):
            _bart_records_from_grid(self.headers[:5] + self.headers[6:], [self.row()])

    def test_generic_missing_status_and_due_date_are_not_active(self):
        row = {"Solicitation Id": "123", "Title": "Example", "Status/Due Date": "10/20/2026"}
        item = normalize(Source("Example", "Current", "url"), [row])[0]
        self.assertEqual(item.status, "Unknown / Not provided")

    def test_site_rejects_legacy_program_statuses_and_unifies_aliases(self):
        for value in ["Na", "Dbe", "Micr", "Mwbe,Sb", "Sb", "", None]:
            self.assertEqual(clean_opportunity({"status": value})["status"], "Unknown / Not provided")
        for value in ["Evaluation", "Evaluating"]:
            self.assertEqual(clean_opportunity({"status": value})["status"], "Evaluating")
        self.assertEqual(clean_opportunity({"status": "OPEN"})["status"], "Open")


if __name__ == "__main__":
    unittest.main()
