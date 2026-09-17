"""Read-only DOM evidence capture for source-specific parser design.

This diagnostic never writes to a workbook or follows a solicitation link. It
records enough sanitized structure to define a source-specific row boundary,
ID field, and link pattern without inferring values from page labels.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cloud-transit-monitor"))
from src.config import SOURCES  # type: ignore
from src.extract import _link_records, _table_records, normalize  # type: ignore


def clean(value: str, limit: int = 4000) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value[:limit]


async def capture(page, source):
    response = None
    error = ""
    try:
        response = await page.goto(source.url, wait_until="domcontentloaded", timeout=90_000)
        await page.wait_for_timeout(5_000)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    body = clean(await page.locator("body").inner_text(timeout=20_000) if await page.locator("body").count() else "")
    structure = await page.evaluate(
        """() => {
          const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
          const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
          const selectors = ['table', 'table tbody tr', '[role="grid"]', '[role="row"]',
            '[role="gridcell"]', 'article', '.table', '.grid', '.results', '.opportunity'];
          const counts = Object.fromEntries(selectors.map(s => [s, [...document.querySelectorAll(s)].filter(visible).length]));
          const samples = [];
          for (const row of [...document.querySelectorAll('tr,[role="row"],article,.opportunity,.results > *')].filter(visible).slice(0, 12)) {
            const cells = [...row.querySelectorAll(':scope > th,:scope > td,[role="columnheader"],[role="gridcell"]')]
              .map(x => clean(x.innerText)).filter(Boolean).slice(0, 20);
            const links = [...row.querySelectorAll('a[href]')].map(a => ({text: clean(a.innerText), href: a.href})).slice(0, 20);
            samples.push({tag: row.tagName.toLowerCase(), classes: String(row.className || '').slice(0, 200),
              cells, links, outer_html: row.outerHTML.slice(0, 3000)});
          }
          const headers = [...document.querySelectorAll('th,[role="columnheader"]')].filter(visible)
            .map(x => clean(x.innerText)).filter(Boolean).slice(0, 100);
          const links = [...document.querySelectorAll('a[href]')].filter(visible)
            .map(a => ({text: clean(a.innerText), href: a.href})).filter(x => x.text || x.href).slice(0, 200);
          return {counts, headers, samples, links};
        }"""
    )
    try:
        table_records = await _table_records(page)
    except Exception:
        table_records = []
    try:
        link_records = await _link_records(page)
    except Exception:
        link_records = []
    table_parsed = normalize(source, table_records)
    link_parsed = normalize(source, link_records)
    return {
        "agency": source.agency,
        "source_name": source.name,
        "source_url": source.url,
        "mode_configured": source.mode,
        "final_url": page.url,
        "http_status": response.status if response else None,
        "page_title": await page.title(),
        "body_text_length": len(body),
        "body_text_sample": body[:4000],
        "error": error,
        "dom": structure,
        "extraction_tests": {
            "table_records": len(table_records),
            "table_publishable": len(table_parsed),
            "link_records": len(link_records),
            "link_publishable": len(link_parsed),
            "table_sample_records": table_records[:5],
            "link_sample_records": link_records[:5],
        },
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


async def run(output: Path):
    results = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        for source in SOURCES:
            page = await browser.new_page()
            try:
                results.append(await capture(page, source))
            finally:
                await page.close()
        await browser.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"schema_version": "source-structure-diagnostic-v1", "sources": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    asyncio.run(run(Path(args.output)))
