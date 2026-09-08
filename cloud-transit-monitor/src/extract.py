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
DUE_HEADERS = ("due date", "close date", "end date", "finish date", "response deadline")
STATUS_HEADERS = ("status", "event status")

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
        opportunity_id = choose(record, ID_HEADERS)
        title = choose(record, TITLE_HEADERS)
        if not title:
            continue
        if not opportunity_id:
            match = re.search(r"\b(?:RFP|RFQ|IFB|RFI|ITB|P|Q|AE|OP|RQ)[-\s]?[A-Z0-9()]{4,}\b", title, re.I)
            opportunity_id = clean(match.group(0)) if match else f"SRC-{index:04d}"
        description = choose(record, ("short description", "scope", "description"))
        posted = parse_date(choose(record, POSTED_HEADERS))
        due = parse_date(choose(record, DUE_HEADERS))
        status = choose(record, STATUS_HEADERS) or ("Future opportunity" if "anticipated" in source.name.lower() else "Active")
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


async def _table_records(page: Page) -> list[dict[str, str]]:
    records = await page.evaluate(
        """() => {
          const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
          const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
          const tables = [...document.querySelectorAll('table')].filter(visible);
          const out = [];
          for (const table of tables) {
            const headerNodes = [...table.querySelectorAll('thead th')];
            let headers = headerNodes.map(x => clean(x.innerText));
            let rows = [...table.querySelectorAll('tbody tr')];
            if (!headers.length) {
              const first = table.querySelector('tr');
              if (first) headers = [...first.querySelectorAll('th,td')].map(x => clean(x.innerText));
              rows = [...table.querySelectorAll('tr')].slice(1);
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
          const gridRows = [...document.querySelectorAll('[role="row"]')].filter(visible);
          let headers = [];
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
        }"""
    )
    return records


async def _link_records(page: Page) -> list[dict[str, str]]:
    return await page.evaluate(
        """() => [...document.querySelectorAll('main a[href], article a[href], .content a[href]')]
          .filter(a => (a.innerText || '').trim().length > 8)
          .map(a => ({Title: a.innerText.replace(/\\s+/g, ' ').trim(), _url: a.href}))"""
    )

async def _marta_text_records(page: Page) -> list[dict[str, str]]:
    """MARTA renders anticipated procurements as accessible text, not a table."""
    text = await page.locator("body").inner_text()
    rows=[]; section=False
    skip={"TBD","RFP","IFB","A&E","Department","Infrastructure","Operations and Urban Planning"}
    for line in (clean(x) for x in text.splitlines()):
        if line == "Contract Description": section=True; continue
        if not section: continue
        if line.startswith("IMPORTANT") or line in {"Bid Overview","Current Opportunities","Bid Results"}: break
        if len(line)>12 and line not in skip and not line.startswith("Estimated Value"):
            rows.append({"Title":line})
    return rows


async def _check_once(browser: Browser, source: Source) -> list[Opportunity]:
    context = await browser.new_context()
    page = await context.new_page()
    try:
        await page.goto(source.url, wait_until="domcontentloaded", timeout=90_000)
        await page.wait_for_timeout(4_000)
        body = clean(await page.locator("body").inner_text(timeout=20_000))
        missing = [marker for marker in source.markers if marker.lower() not in body.lower()]
        if missing:
            raise RuntimeError(f"Missing success markers: {', '.join(missing)}")
        if source.agency == "MARTA" and "Anticipated" in source.name:
            records = await _marta_text_records(page)
        else:
            records = await (_link_records(page) if source.mode == "links" else _table_records(page))
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
