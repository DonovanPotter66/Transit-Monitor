from src.extract import (
    _bart_records_from_text,
    _mbta_page_html_records,
    _mbta_records_from_html,
    _marta_current_records_from_text,
    _marta_records_from_links,
    _page_text_from_html_url,
    _mta_records_from_text,
    _records_from_bid_links,
    _sound_transit_snapshot_records_from_text,
)
import asyncio
from unittest.mock import patch
import unittest


class AgencyExtractorTests(unittest.TestCase):
    def test_bart_people_soft_stream_uses_result_rows_only(self):
        text = """
        Search Criteria Use Saved Search Manage Saved Searches
        Program Codes Results should include Events
        Solicitation Id Event Name Start Date End Date Status/Due Date Description Status Program Name Contract Type Electronic Bid
        BARTD-49GH-130 Train Control System Modernization Enabling Works 07/01/2026 8:00 AM PDT 08/03/2026 2:00 PM PDT Due 08/03/2026 Accepted Rail Systems Construction Yes
        BARTD-15EK-102 TCCCP New Traction Power Facilities West Bay 07/02/2026 9:00 AM PDT 08/10/2026 2:00 PM PDT Due 08/10/2026 SB Power Construction Yes
        Related Content New Window Close
        """

        rows = _bart_records_from_text(text)

        self.assertEqual([row["Solicitation Id"] for row in rows], ["BARTD-49GH-130", "BARTD-15EK-102"])
        self.assertEqual(rows[0]["Event Name"], "Train Control System Modernization Enabling Works")
        self.assertTrue(all("Search Criteria" not in row["Event Name"] for row in rows))

    def test_marta_current_parser_uses_live_documents_section(self):
        text = """
        MARTA Home
        Current Opportunities
        Current Opportunities Documents
        On-Call Planning Support Services Request for Proposal (RFP) - RFP P50816
        Bid Documents
        Description:
        MARTA is seeking Proposals from Firms to provide planning support.
        Proposal/Quote Submittal From: 8/11/2026 11:35 AM
        Proposal/Quote Submittal To: 9/22/2026 2:00 PM
        Surveying Design Services Architecture/Engineering (A/E) - AE50822A
        Bid Documents
        Description:
        Qualified firms to provide professional architectural and engineering consulting services for Surveying Design Services.
        Proposal/Quote Submittal To: 10/01/2026 2:00 PM
        Structural Inspection Engineering Services Architecture/Engineering (A/E) - AE50821
        Bid Documents
        Description:
        Request for Statement of Qualifications - Qualified firms to provide on-call services for structural engineering inspections services
        Proposal/Quote Submittal To: 11/01/2026 2:00 PM
        Our Mission
        P00000 Archived result
        """

        rows = _marta_current_records_from_text(text)

        self.assertEqual([row["Solicitation Number"] for row in rows], ["RFP P50816", "AE50822A", "AE50821"])
        self.assertEqual(rows[0]["Title"], "On-Call Planning Support Services Request for Proposal (RFP)")
        self.assertEqual(rows[0]["Due Date"], "9/22/2026 2:00 PM")
        self.assertEqual(rows[1]["Due Date"], "10/01/2026 2:00 PM")

    def test_mbta_static_table_parser_uses_future_project_rows(self):
        html = """
        <table class="tableFormat" cellspacing="1" style="width:710px">
          <thead><tr>
            <th>Contract Number</th><th>Project Name</th><th>Project Description</th>
            <th>Anticipated Advertisement Date</th><th>Duration</th>
          </tr></thead>
          <tbody>
            <tr><td nowrap>Z94PS35-XX</td><td>GEC for Engineering and Capital</td><td>GEC Reprocurements for Engineering and Capital</td><td nowrap>September 2026</td><td nowrap>36 months</td></tr>
            <tr><td nowrap>X14PS01</td><td>Design Procurement for Blue Line Signals</td><td></td><td nowrap>September 2026</td><td nowrap>TBD</td></tr>
          </tbody>
        </table>
        """

        rows = _mbta_records_from_html(html)

        self.assertEqual([row["Contract Number"] for row in rows], ["Z94PS35-XX", "X14PS01"])
        self.assertEqual(rows[0]["Project Name"], "GEC for Engineering and Capital")
        self.assertEqual(rows[1]["Anticipated Advertisement Date"], "September 2026")

    def test_mbta_page_html_parser_uses_loaded_browser_content(self):
        class FakePage:
            async def content(self):
                return """
                <table class="tableFormat"><tr><th>Contract Number</th><th>Project Name</th><th>Project Description</th><th>Anticipated Advertisement Date</th><th>Duration</th></tr>
                <tr><td>X14PS01</td><td>Design Procurement for Blue Line Signals</td><td></td><td>September 2026</td><td>TBD</td></tr></table>
                """

        rows = asyncio.run(_mbta_page_html_records(FakePage()))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Contract Number"], "X14PS01")

    def test_marta_current_parser_can_use_opportunity_links(self):
        links = [
            {"Title": "Current Opportunities Documents", "_url": "https://oracle.example/"},
            {"Title": "On-Call Planning Support Services Request for Proposal (RFP) - RFP P50816", "_url": "https://martabid.example/opportunity/1"},
            {"Title": "Bid Documents", "_url": "https://martabid.example/docs/1"},
            {"Title": "Structural Inspection Engineering Services Architecture/Engineering (A/E) - AE50821", "_url": "https://martabid.example/opportunity/2"},
        ]

        rows = _marta_records_from_links(links)

        self.assertEqual([row["Solicitation Number"] for row in rows], ["RFP P50816", "AE50821"])
        self.assertEqual(rows[0]["Title"], "On-Call Planning Support Services Request for Proposal (RFP)")
        self.assertEqual(rows[0]["_url"], "https://martabid.example/opportunity/1")

    def test_mta_parser_uses_active_solicitation_label_blocks(self):
        text = """
        Active Solicitations
        S48020 CBTC for 6th Ave Line, 63rd St Line and DeKalb Interlocking (0000541781)
        * Solicitation number: 0000541781
        * Title/Description: CBTC for 6th Ave Line, 63rd St Line and DeKalb Interlocking
        * Funding: 100% MTA
        * Current opening/due date: 10/16/2026
        * Document availability date: 5/21/2026
        E31634 Fan Plant Component Repairs
        * Contract number: E31634
        * Title/description: Fan Plant Component Repairs
        * Current opening/due date: 9/22/2026
        * Document availability date: 6/05/2026
        """

        rows = _mta_records_from_text(text)

        self.assertEqual([row["Solicitation Number"] for row in rows], ["0000541781", "E31634"])
        self.assertEqual(rows[0]["Title"], "CBTC for 6th Ave Line, 63rd St Line and DeKalb Interlocking")
        self.assertEqual(rows[1]["Current Opening/Due Date"], "9/22/2026")

    def test_static_html_text_fallback_preserves_procurement_text(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"<html><script>ignore()</script><body><h2>Active Solicitations</h2><p>* Solicitation number: 0000541781</p></body></html>"

        with patch("src.extract.urlopen", return_value=FakeResponse()):
            text = _page_text_from_html_url("https://example.test/current")

        self.assertIn("Active Solicitations", text)
        self.assertIn("Solicitation number: 0000541781", text)
        self.assertNotIn("ignore()", text)

    def test_septa_link_parser_keeps_bid_records_not_navigation(self):
        links = [
            {"Title": "Procurement", "_url": "https://www.septa.org/procurement/"},
            {"Title": "Filter by professional services", "_url": "https://www.septa.org/procurement/bids/?filter=professional"},
            {"Title": "RFP #25-003 Station Accessibility Design Services Due 09/30/2026", "_url": "https://www.septa.org/procurement/bids/rfp-25-003/"},
            {"Title": "IFB 24-110 Rail Materials Procurement", "_url": "https://www.septa.org/procurement/bids/ifb-24-110/"},
        ]

        rows = _records_from_bid_links(links, r"\b(?:RFP|RFQ|IFB|RFI|RFQu|Bid)\s*#?\s*[A-Z0-9-]{3,}\b", "SEPTA")

        self.assertEqual([row["Solicitation Number"] for row in rows], ["RFP #25-003", "IFB 24-110"])
        self.assertNotIn("Filter", " ".join(row["Title"] for row in rows))

    def test_uta_link_parser_keeps_document_labels_only(self):
        links = [
            {"Title": "How to do business with UTA", "_url": "https://www.rideuta.com/Doing-Business"},
            {"Title": "RFP 26-101 Light Rail Vehicle Engineering Services", "_url": "https://www.rideuta.com/-/media/Files/Doing-Business/Procurement/RFP-26-101.pdf"},
            {"Title": "RFQ 26-220 Program Management Support", "_url": "https://www.rideuta.com/Documents/RFQ-26-220.docx"},
            {"Title": "RFP 26-999 Informational webinar", "_url": "https://www.rideuta.com/news/info"},
        ]

        rows = _records_from_bid_links(links, r"\b(?:RFP|RFQ|IFB|RFI|SOQ)\s*#?\s*[A-Z0-9-]{3,}(?:-[A-Z0-9]+)*\b", "UTA")

        self.assertEqual([row["Solicitation Number"] for row in rows], ["RFP 26-101", "RFQ 26-220"])
        self.assertEqual(rows[0]["Title"], "Light Rail Vehicle Engineering Services")

    def test_sound_transit_snapshot_parser_uses_pdf_procurement_rows(self):
        text = """
        Procurement Snapshot
        Procurement Title Procurement ID Procurement Process Phase Solicitation
        Pre-Bid Meeting Submittal Due NOIA or NOA
        Light Rail Vehicle Series 3 RP 0110-24 Request for Proposal Advertising 06/26/26 03/12/27 09/28/27
        On-Call Transit Fare Collection and Payment Industry Consulting
        Services
        RP 0157-26 Request for Proposal Advertising 09/10/26 10/21/26 01/25/27
        W100- WSLE SODO Station - GC/CM GC 0124-26 Advertising 08/10/26 09/21/26 07/01/27
        DRLE Large Wood Material Installation CN 0003-26 Invitation for Bid (IFB) In Development TBD
        WSLE Geo & Instrumentation Monitoring AE 0096-26 Request for
        Qualifications
        Evaluating 06/12/26 06/23/26 07/09/26 10/22/26
        """

        rows = _sound_transit_snapshot_records_from_text(text)

        self.assertEqual(
            [row["Procurement ID"] for row in rows],
            ["RP 0110-24", "RP 0157-26", "GC 0124-26", "CN 0003-26", "AE 0096-26"],
        )
        self.assertEqual(rows[0]["Project Name"], "Light Rail Vehicle Series 3")
        self.assertEqual(
            rows[1]["Project Name"],
            "On-Call Transit Fare Collection and Payment Industry Consulting Services",
        )
        self.assertEqual(rows[2]["Due Date"], "09/21/26")
        self.assertEqual(rows[3]["Status"], "In Development")


if __name__ == "__main__":
    unittest.main()
