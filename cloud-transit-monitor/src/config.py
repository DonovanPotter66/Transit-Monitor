from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .models import Source


@dataclass(frozen=True)
class Settings:
    graph_drive_id: str
    workbook_item_id: str
    workbook_name: str
    timezone: str
    target_local_time: str
    run_window_minutes: int
    mail_sender: str
    mail_recipients: tuple[str, ...]
    dry_run: bool
    force_run: bool
    local_workbook_path: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            graph_drive_id=os.getenv("GRAPH_DRIVE_ID", ""),
            workbook_item_id=os.getenv("WORKBOOK_ITEM_ID", ""),
            workbook_name=os.getenv("WORKBOOK_NAME", "Transit Agency Monitor.xlsx"),
            timezone=os.getenv("TIMEZONE", "America/Los_Angeles"),
            target_local_time=os.getenv("TARGET_LOCAL_TIME", "06:30"),
            run_window_minutes=int(os.getenv("RUN_WINDOW_MINUTES", "25")),
            mail_sender=os.getenv("MAIL_SENDER", ""),
            mail_recipients=tuple(x.strip() for x in os.getenv("MAIL_RECIPIENTS", "").split(",") if x.strip()),
            dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
            force_run=os.getenv("FORCE_RUN", "false").lower() == "true",
            local_workbook_path=os.getenv("LOCAL_WORKBOOK_PATH", ""),
        )

    def validate(self) -> None:
        if not self.dry_run and (not self.graph_drive_id or not self.workbook_item_id):
            raise ValueError("GRAPH_DRIVE_ID and WORKBOOK_ITEM_ID are required outside DRY_RUN mode.")
        if self.dry_run and not self.local_workbook_path:
            raise ValueError("LOCAL_WORKBOOK_PATH is required in DRY_RUN mode.")

    def within_run_window(self, now: datetime | None = None) -> bool:
        local = (now or datetime.now(tz=ZoneInfo(self.timezone))).astimezone(ZoneInfo(self.timezone))
        hour, minute = (int(x) for x in self.target_local_time.split(":", 1))
        target = datetime.combine(local.date(), time(hour, minute), tzinfo=local.tzinfo)
        return 0 <= (local - target).total_seconds() <= self.run_window_minutes * 60


SOURCES = (
    Source("MBTA", "Future Professional Services Contract Bid Solicitations",
           "https://bc.mbta.com/business_center/bidding_solicitations/future_prof_services_solicitations/",
           # MBTA changed the page headings; populated-row validation remains
           # the authoritative success check.
           markers=()),
    Source("BART", "View Active Solicitations",
           "https://suppliers.bart.gov/psc/BRFPV91/SUPPLIER/ERP/c/AUC_MANAGE_BIDS.AUC_RESP_INQ_AUC.GBL?active=P",
           markers=("Search Results", "Solicitation Id", "Event Name")),
    Source("WMATA", "Procurement Opportunities",
           "https://supplier.wmata.com/psc/supplier_1/SUPPLIER/ERP/c/AUC_MANAGE_BIDS.AUC_RESP_INQ_AUC.GBL?SOURCE=ClosedOver150&skipcnav=1",
           markers=("Welcome, WMATA Supplier Guest", "Solicitation ID", "Solicitation Name")),
    Source("MTA", "MTA C&D Current Opportunities",
           "https://www.mta.info/agency/construction-and-development/contracting/current-opportunities",
           markers=("Contract", "Description")),
    Source("MARTA", "MARTA Current Opportunities",
           "https://martabid.marta.net/CurrentOpportunities.aspx",
           markers=("Current Opportunities", "Proposal/Quote Submittal To")),
    Source("CTA", "CTA Bonfire Open Opportunities",
           "https://transitchicago.bonfirehub.com/portal/?tab=openOpportunities",
           markers=("Open Public Opportunities", "Ref. #", "Project", "Close Date")),
    Source("DART", "DART Bonfire Open Opportunities",
           "https://dart.bonfirehub.com/portal/?tab=openOpportunities",
           markers=("Open Public Opportunities", "Ref. #", "Project", "Close Date")),
    Source("DART", "DART Procurement / Anticipated Procurements",
           "https://www.dart.org/about/doing-business/procurement",
           markers=("Anticipated Procurements",), priority="secondary",
           secondary_policy="informational_only"),
    Source("RTD Denver", "RTD OpenGov Open Projects (embedded)",
           "https://procurement.opengov.com/portal/embed/rtd-denver/project-list?departmentId=all&status=open",
           markers=("Procurement Portal", "Project Title", "Project ID", "Status", "Addenda", "Release Date", "Due Date"),
           verification_urls=(
               "https://procurement.opengov.com/portal/embed/rtd-denver/project-list?departmentId=all&status=pendingAward",
               "https://procurement.opengov.com/portal/embed/rtd-denver/project-list?departmentId=all&status=closed",
           )),
    Source("RTD Denver", "RTD OpenGov Coming Soon Projects (embedded)",
           "https://procurement.opengov.com/portal/embed/rtd-denver/project-list?departmentId=all&status=comingSoon",
           markers=("Procurement Portal", "Project Title", "Project ID", "Status", "Addenda", "Release Date", "Due Date")),
    Source("RTD Denver", "RTD OpenGov Pending Award Projects (embedded)",
           "https://procurement.opengov.com/portal/embed/rtd-denver/project-list?departmentId=all&status=pendingAward",
           markers=("Procurement Portal", "Project Title", "Project ID", "Status")),
    Source("RTD Denver", "RTD OpenGov Closed Projects (embedded verification)",
           "https://procurement.opengov.com/portal/embed/rtd-denver/project-list?departmentId=all&status=closed",
           markers=("Procurement Portal", "Project Title", "Project ID", "Status"), priority="verification"),
    Source("RTD Denver", "RTD Procurement Resources Fallback",
           "https://www.rtd-denver.com/doing-business-with-rtd/procurement-resources",
           mode="links", priority="fallback"),
    Source("SEPTA", "SEPTA Procurement Landing Page",
           "https://wwww.septa.org/procurement/", mode="links", priority="secondary"),
    Source("SEPTA", "SEPTA Current Bids Listing",
           "https://wwww.septa.org/procurement/bids/", mode="links", markers=("Procurement",)),
    Source("UTA", "UTA Solicitation Procurements",
           "https://www.rideuta.com/Doing-Business/UTA-Solicitation-Procurements", mode="links"),
    Source("Sound Transit", "Sound Transit System Expansion and Project Documents",
           "https://www.soundtransit.org/system-expansion", mode="links"),
    Source("LA Metro", "LA Metro Vendor Portal Open Solicitations",
           "https://business.metro.net/webcenter/portal/VendorPortal/pages_home/solicitations/openSolicitations",
           markers=("Open Solicitations", "Number", "Title", "Type", "Due Date", "Issue Date", "Status")),
)
