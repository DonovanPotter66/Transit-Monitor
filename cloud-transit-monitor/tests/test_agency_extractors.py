from src.extract import (
    _bart_records_from_text,
    _marta_current_records_from_text,
    _records_from_bid_links,
)
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
        Current Opportunities Documents
        RFP P50723 Rail Station Rehabilitation Design Services
        Description: Architectural and engineering services
        Proposal/Quote Submittal To: 10/15/2026 2:00 PM
        IFB P50683 Track Materials Procurement
        Description: Rail components
        Proposal/Quote Submittal To: 11/01/2026 2:00 PM
        Bid Results
        P00000 Archived result
        """

        rows = _marta_current_records_from_text(text)

        self.assertEqual([row["Solicitation Number"] for row in rows], ["RFP P50723", "IFB P50683"])
        self.assertEqual(rows[0]["Title"], "Rail Station Rehabilitation Design Services")
        self.assertEqual(rows[1]["Due Date"], "11/01/2026 2:00 PM")

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


if __name__ == "__main__":
    unittest.main()
