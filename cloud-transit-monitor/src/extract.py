from __future__ import annotations

import asyncio
import re
from datetime import date
from typing import Iterable

from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from playwright.async_api import Browser, Page, async_playwright

from .models import CheckResult, Opportunity, Source


ID_HEADERS = ("opportunity id", "project id", "solicitation id", "solicitation number", "number",
              "ref. #", "reference #", "contract number", "contract no", "event id")
TITLE_HEADERS = ("project title", "project name", "solicitation name", "event name", "title", "project", "description")
POSTED_HEADERS = ("posted date", "issue date", "release date", "start date", "advertisement date")
DUE_HEADERS = ("due date", "close date", "end date", "finish date", "response deadline", "anticipated date", "current opening/due date")
STATUS_HEADERS = ("status", "event status")

# These sources are being reintegrated under an evidence-first contract.  A
# candidate must carry a real source identifier, a title, a recognizable
# status, and at least one real date before it can become an Opportunity.
REINTEGRATION_AGENCIES = {"MBTA", "WMATA", "MTA", "LA Metro"}

HIGH_TERMS = (
    "traction power", "substation", "train control", "signal", "interlocking", "cbrtc", "cbtc",
    "station", "rail modernization", "general engineering", "gec", "pm/cm", "program management",
    "construction management", "owner's rep", "owners rep",
)
MEDIUM_TERMS = (
    "rail", "track", "yard", "maintenance facility", "accessibility", "ada", "design",
    "engineering", "construction", "bridge", "tunnel", "light rail", "metro",
)


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_date(value: str) -> date | None:
    value = clean(value)
    if not value:
        return None
    try:
        return date_parser.parse(value, fuzzy=True).date()
    except (ValueError, OverflowError):
        return None


def choose(record: dict[str, str], aliases: Iterable[str]) -> str:
    normalized = {clean(k).lower(): clean(v) for k, v in record.items()}
    for alias in aliases:
        if alias in normalized and normalized[alias]:
            return normalized[alias]
    for key, value in normalized.items():
        if any(alias in key for alias in aliases) and value:
            return value
    return ""

def looks_like_date_or_timestamp(value: str) -> bool:
    value = clean(value)
    return bool(re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}:\d{2}\s*(?:AM|PM)?\b", value, re.I))


def classify(text: str) -> tuple[str, str]:
    lower = text.lower()
    if any(term in lower for term in HIGH_TERMS):
        return "High", "Direct rail systems, stations, power, signals, engineering, or program-delivery relevance."
    if any(term in lower for term in MEDIUM_TERMS):
        return "Medium", "Potential rail capital, facility, accessibility, design, or construction relevance."
    return "Low", "Low direct relevance; commodity, fleet, administrative, or generic scope."


def normalize(source: Source, records: list[dict[str, str]]) -> list[Opportunity]:
    output: list[Opportunity] = []
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(records, 1):
        opportunity_id = choose(record, source.id_headers or ID_HEADERS)
        if looks_like_date_or_timestamp(opportunity_id):
            opportunity_id = ""
        title = choose(record, TITLE_HEADERS)
        if not title:
            continue
        if source.agency == "BART":
            # PeopleSoft renders its search form and result grid in the same
            # HTML surface. Reject controls/template text before it can become
            # a fake solicitation row.
            title_id = re.search(r"\bBARTD[-\s]?[A-Z0-9]+(?:[-][A-Z0-9]+)*\b", title, re.I)
            bart_id = opportunity_id if re.match(r"^BARTD[-\s]", opportunity_id, re.I) else ""
            if title_id:
                # In concatenated PeopleSoft rows, the first BARTD token is
                # the solicitation identifier; later tokens belong to other
                # rows or filter controls.
                opportunity_id = clean(title_id.group(0))
                bart_id = opportunity_id
            ui_terms = ("search criteria", "use saved search", "program codes", "results should include", "manage saved searches")
            if not bart_id or (not title_id and any(term in title.lower() for term in ui_terms)) or str(record.get("_url", "")).lower().startswith("javascript:"):
                continue
            if len(title) > 180:
                # Malformed cells sometimes concatenate the title with posted
                # and due dates and neighboring filter values.
                title = clean(re.split(r"\b\d{1,2}/\d{1,2}/\d{4}\b", title, maxsplit=1)[0])[:180]
        if not opportunity_id and source.link_id_pattern:
            link = clean(str(record.get("_url", "")))
            match = re.search(source.link_id_pattern, link, re.I)
            opportunity_id = clean(match.group(0)) if match else ""
        # Never infer an ID from a title or row position. If the source
        # contract did not identify an explicit field/link key, this row is
        # intentionally withheld instead of receiving SRC-* or AUTO-* text.
        if not opportunity_id:
            continue
        if source.id_pattern and not re.fullmatch(source.id_pattern, opportunity_id, re.I):
            continue
        description = choose(record, ("short description", "scope", "description"))
        posted = parse_date(choose(record, POSTED_HEADERS))
        due = parse_date(choose(record, DUE_HEADERS))
        status = choose(record, STATUS_HEADERS) or ("Future opportunity" if "anticipated" in source.name.lower() else "Active")
        if source.agency in REINTEGRATION_AGENCIES:
            # Never synthesize an ID or silently accept a UI/header row for a
            # deferred source.  Publication requires an ID, title, status,
            # and at least one source-provided date.
            raw_id = clean(opportunity_id)
            if (not raw_id or raw_id.startswith("SRC-") or
                    not title or not status or not (posted or due)):
                continue
        priority, relevance = classify(" ".join((title, description)))
        opportunity = Opportunity(
            agency=source.agency,
            source_name=source.name,
            source_url=source.url,
            opportunity_id=opportunity_id,
            project_name=title,
            description=description,
            posted_date=posted,
            due_date=due,
            status=status,
            priority=priority,
            relevance=relevance,
            opportunity_url=record.get("_url", source.url),
            raw=record,
        )
        if opportunity.key not in seen:
            output.append(opportunity)
            seen.add(opportunity.key)
    return output


async def _table_records(page: Page, source: Source | None = None) -> list[dict[str, str]]:
    contract = {
        "recordSelector": source.record_selector if source else "",
        "requiredHeaders": [x.lower() for x in (source.required_headers if source else ())],
    }
    records = await page.evaluate(
        """(contract) => {
          const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
          const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
          const candidates = contract.recordSelector
            ? [...document.querySelectorAll(contract.recordSelector)]
            : [...document.querySelectorAll('table')];
          const headerFor = table => {
            const head = [...table.querySelectorAll('thead th')].map(x => clean(x.innerText).toLowerCase());
            if (head.length) return {headers: head, row: null};
            const rows = [...table.querySelectorAll('tr')];
            for (const row of rows.slice(0, 12)) {
              const hs = [...row.querySelectorAll(':scope > th,:scope > td')].map(x => clean(x.innerText).toLowerCase());
              if (contract.requiredHeaders.length && contract.requiredHeaders.every(h => hs.includes(h))) return {headers: hs, row};
            }
            return {headers: [], row: null};
          };
          const tables = candidates.filter(visible).filter(table => {
            if (!contract.requiredHeaders.length) return true;
            return headerFor(table).headers.length > 0;
          });
          // A portal can render the header row outside <thead> or alter its
          // wrapper without changing the observed columns.  If the
          // evidence-backed candidate produced no table, fall back to all
          // visible tables and let normalize() enforce the ID/title contract.
          const selectedTables = tables.length ? tables : [...document.querySelectorAll('table')].filter(visible);
          const out = [];
          for (const table of selectedTables) {
            const headerNodes = [...table.querySelectorAll('thead th')];
            let headers = headerNodes.map(x => clean(x.innerText));
            let rows = [...table.querySelectorAll('tbody tr')];
            if (!headers.length) {
              const allRows = [...table.querySelectorAll('tr')];
              const match = allRows.findIndex(row => {
                const hs = [...row.querySelectorAll(':scope > th,:scope > td')].map(x => clean(x.innerText).toLowerCase());
                return contract.requiredHeaders.length
                  ? contract.requiredHeaders.every(h => hs.includes(h))
                  : hs.length > 0;
              });
              const headerRow = match >= 0 ? allRows[match] : allRows[0];
              if (headerRow) headers = [...headerRow.querySelectorAll(':scope > th,:scope > td')].map(x => clean(x.innerText));
              rows = allRows.slice(match >= 0 ? match + 1 : 1);
            }
            for (const row of rows) {
              const cells = [...row.querySelectorAll(':scope > th, :scope > td')];
              if (!cells.length) continue;
              const item = {};
              cells.forEach((cell, i) => item[headers[i] || `Column ${i + 1}`] = clean(cell.innerText));
              const link = row.querySelector('a[href]');
              if (link) item._url = link.href;
              if (Object.values(item).some(Boolean)) out.push(item);
            }
          }
          if (out.length) return out;
          let gridRoot = contract.recordSelector ? document.querySelector(contract.recordSelector) : document;
          let gridRows = gridRoot ? [...gridRoot.querySelectorAll('[role="row"]')].filter(visible) : [];
          if (!gridRows.length && contract.recordSelector) {
            gridRoot = document;
            gridRows = [...document.querySelectorAll('[role="row"]')].filter(visible);
          }
          let headers = [];
          const globalHeaders = [...gridRoot.querySelectorAll('[role="columnheader"]')].map(x => clean(x.innerText)).filter(Boolean);
          if (globalHeaders.length) headers = globalHeaders;
          for (const row of gridRows) {
            const hs = [...row.querySelectorAll('[role="columnheader"]')].map(x => clean(x.innerText));
            if (hs.length) { headers = hs; break; }
          }
          for (const row of gridRows) {
            const cells = [...row.querySelectorAll('[role="gridcell"],[role="cell"]')];
            if (!cells.length) continue;
            const item = {};
            cells.forEach((cell, i) => item[headers[i] || `Column ${i + 1}`] = clean(cell.innerText));
            const link = row.querySelector('a[href]');
            if (link) item._url = link.href;
            if (Object.values(item).some(Boolean)) out.push(item);
          }
          return out;
        }""", contract
    )
    return records


async def _link_records(page: Page) -> list[dict[str, str]]:
    return await page.evaluate(
        """() => [...document.querySelectorAll('main a[href], article a[href], .content a[href]')]
          .filter(a => (a.innerText || '').trim().length > 8)
          .map(a => ({Title: a.innerText.replace(/\\s+/g, ' ').trim(), _url: a.href}))"""
    )

async def _mbta_frame_records(page: Page) -> list[dict[str, str]]:
    """MBTA currently renders the future-project table in a child frame."""
    records: list[dict[str, str]] = []
    for frame in page.frames:
        try:
            found = await frame.locator("table").evaluate_all(
                """tables => tables.flatMap(table => {
                  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
                  const rows = [...table.querySelectorAll('tr')];
                  if (!rows.length) return [];
                  const headers = [...rows[0].querySelectorAll('th,td')].map(x => clean(x.innerText));
                  return rows.slice(1).map(row => {
                    const cells = [...row.querySelectorAll('th,td')];
                    const item = {}; cells.forEach((cell,i) => item[headers[i] || `Column ${i+1}`] = clean(cell.innerText));
                    const link = row.querySelector('a[href]'); if (link) item._url = link.href;
                    return item;
                  }).filter(item => Object.values(item).some(Boolean));
                })"""
            )
            records.extend(found)
        except Exception:
            continue
    return records

async def _mbta_text_records(page: Page) -> list[dict[str, str]]:
    """Fallback for MBTA's accessibility-rendered pipe-delimited rows."""
    text = await page.locator("body").inner_text()
    rows = []
    for line in (clean(x) for x in text.splitlines()):
        parts = [clean(x) for x in line.split("|")]
        if len(parts) < 2 or not re.search(r"[A-Z0-9]{4,}[-A-Z0-9]*", parts[0]):
            continue
        if parts[0].lower() in {"contract number", "project name"}:
            continue
        rows.append({"Contract Number": parts[0], "Project Name": parts[1],
                     "Project Description": parts[2] if len(parts) > 2 else "",
                     "Anticipated Advertisement Date": parts[3] if len(parts) > 3 else ""})
    return rows

async def _marta_text_records(page: Page) -> list[dict[str, str]]:
    """Parse MARTA's accessible anticipated-procurement column stream.

    The page exposes the columns as text rather than a conventional table:
    contract/title, anticipated date, type, and department.  Navigation and
    explanatory copy are deliberately ignored.
    """
    text = await page.locator("body").inner_text()
    lines=[clean(x) for x in text.splitlines() if clean(x)]
    rows=[]; section=False; i=0
    for line in lines:
        if line.lower() == "contract description": section=True
        if section: break
    if section:
        i=lines.index(line)+1
    while i < len(lines):
        current=lines[i]
        if current.startswith("IMPORTANT") or current in {"Bid Overview","Current Opportunities","Bid Results"}: break
        # Contract IDs are embedded in the title (P50723, P50683, ...).
        if re.search(r"\bP\d{4,}\b", current, re.I):
            anticipated=lines[i+1] if i+1 < len(lines) else ""
            kind=lines[i+2] if i+2 < len(lines) else ""
            department=lines[i+3] if i+3 < len(lines) else ""
            if kind.upper() in {"RFP","IFB","RFQ","RFI","A&E"} and department:
                ident=re.search(r"\bP\d{4,}\b", current, re.I).group(0).upper()
                rows.append({"Contract Number":ident,"Project Name":current,
                             "Anticipated Date":anticipated,"Status":"Anticipated",
                             "Type":kind,"Department":department})
                i += 4
                continue
        i += 1
    return rows


async def _marta_current_records(page: Page) -> list[dict[str, str]]:
    """Parse active MARTA opportunity blocks from the current-opportunities page."""
    text = await page.locator("body").inner_text()
    lines=[clean(x) for x in text.splitlines() if clean(x)]
    rows=[]
    for i, line in enumerate(lines):
        ident=re.search(r"\b(?:RFP|RFQ|IFB|RFI|AE)\s*-?\s*[A-Z]?\d{4,}\b", line, re.I)
        if not ident: continue
        token=clean(ident.group(0).replace(" ", " "))
        deadline=""
        description=""
        for follow in lines[i+1:i+25]:
            if follow.lower().startswith("description:"):
                description=clean(follow.split(":",1)[1])
            if "proposal/quote submittal to:" in follow.lower():
                deadline=clean(follow.split(":",1)[1]); break
        if deadline:
            rows.append({"Solicitation Number":token,"Title":line,
                         "Description":description,"Due Date":deadline,
                         "Status":"Active"})
    return rows


async def _mta_text_records(page: Page) -> list[dict[str, str]]:
    """Fallback parser for MTA C&D's label/value accessibility rendering."""
    text = await page.locator("body").inner_text()
    rows=[]
    # MTA publishes repeated blocks headed by solicitation/contract number.
    pattern = re.compile(
        r"(?:Solicitation|Contract)\s+(?:number|no\.?)[\s:]+([A-Z0-9-]+).*?"
        r"(?:Title|Description)[\s:]+(.{3,240}?)(?=\s+(?:Current opening|Due|Document availability|Solicitation|Contract)\b|$)"
        r"(?:.*?(?:Current opening|Due)\s*(?:date)?[\s:]+([^\n]+))?",
        re.I | re.S,
    )
    for match in pattern.finditer(text):
        ident, title, due = (clean(x) for x in match.groups())
        if ident and title:
            rows.append({"Solicitation Number":ident,"Title":title,
                         "Current Opening/Due Date":due,"Status":"Active"})
    return rows


def _wmata_candidate_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep only WMATA supplier-portal solicitation rows, never portal UI."""
    out=[]
    for record in records:
        values=" ".join(clean(str(v)) for k,v in record.items() if not str(k).startswith("_"))
        ident=re.search(r"\bWMATA-\d{6,}\b", values, re.I)
        title=choose(record, ("solicitation name","title","description","project name"))
        if ident and title and len(title) > 3:
            item=dict(record)
            item["Solicitation ID"]=ident.group(0).upper()
            item["Solicitation Name"]=title
            out.append(item)
    return out


async def _check_once(browser: Browser, source: Source) -> list[Opportunity]:
    context = await browser.new_context()
    page = await context.new_page()
    try:
        await page.goto(source.url, wait_until="domcontentloaded", timeout=90_000)
        await page.wait_for_timeout(4_000)
        if source.url_pattern and not re.search(source.url_pattern, page.url, re.I):
            raise RuntimeError(f"Unexpected final URL: {page.url}")
        body = clean(await page.locator("body").inner_text(timeout=20_000))
        missing = [marker for marker in source.markers if marker.lower() not in body.lower()]
        if missing:
            raise RuntimeError(f"Missing success markers: {', '.join(missing)}")
        if source.agency == "MARTA" and "Current" in source.name:
            records = await _marta_current_records(page)
        elif source.agency == "MARTA" and "Anticipated" in source.name:
            records = await _marta_text_records(page)
        else:
            records = await (_link_records(page) if source.mode == "links" else _table_records(page, source))
            if source.agency == "MTA" and not records:
                records = await _mta_text_records(page)
            if source.agency == "WMATA":
                records = _wmata_candidate_records(records)
            if source.agency == "MBTA" and not records:
                records = await _mbta_frame_records(page)
            if source.agency == "MBTA" and not records:
                records = await _mbta_text_records(page)
        opportunities = normalize(source, records)
        if not opportunities:
            raise RuntimeError("Expected populated opportunity rows were not found.")
        return opportunities
    finally:
        await context.close()


async def check_source(browser: Browser, source: Source) -> CheckResult:
    if source.priority == "fallback":
        return CheckResult(source=source, status="Not Established")
    if source.priority == "secondary":
        if source.secondary_policy != "informational_only":
            return CheckResult(source=source, status="Secondary unavailable", error="Secondary source has no explicit policy")
        try:
            # Deliberately discard parsed rows: this page is evidence only and
            # cannot create, update, or remove workbook opportunities.
            await _check_once(browser, source)
            return CheckResult(source=source, status="Secondary informational")
        except Exception as exc:
            return CheckResult(source=source, status="Secondary unavailable", error=f"{type(exc).__name__}: {exc}")
    first_error = ""
    for attempt in range(2):
        try:
            opportunities = await _check_once(browser, source)
            return CheckResult(
                source=source,
                status="Successful after retry" if attempt else "Successful",
                opportunities=opportunities,
                retried=bool(attempt),
            )
        except Exception as exc:  # source failures are data, not job failures
            first_error = f"{type(exc).__name__}: {exc}"
            if attempt == 0:
                await asyncio.sleep(2)
    return CheckResult(source=source, status="Failed", error=first_error, retried=True)


async def run_checks(sources: Iterable[Source]) -> list[CheckResult]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            results = []
            for source in sources:
                results.append(await check_source(browser, source))
            return results
        finally:
            await browser.close()
