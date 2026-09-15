"""Read-only diagnostics for deferred agency portals.

No workbook, database, or source configuration is changed.  The report makes
the distinction between page evidence, parser candidates, and publishable rows.
"""
from __future__ import annotations
import argparse, asyncio, json, re, sys
from pathlib import Path
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cloud-transit-monitor"))
from src.config import SOURCES  # type: ignore
from src.extract import (_table_records, _link_records, _mbta_frame_records,
                         _mbta_text_records, _marta_text_records,
                         _mta_text_records, _wmata_candidate_records, normalize)  # type: ignore

AGENCIES=("MBTA","WMATA","MTA","LA Metro")

def sanitize(value: str) -> str:
    value=re.sub(r"https?://\S+", "[redacted-url]", value or "")
    value=re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[redacted-email]", value)
    return re.sub(r"\s+", " ", value).strip()[:1500]

async def collect(source):
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        page=await browser.new_page()
        nav=[]
        page.on("response", lambda r: nav.append({"url":r.url,"status":r.status}) if r.request.is_navigation_request() else None)
        response=None; error=""
        try:
            response=await page.goto(source.url, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(4000)
            body=await page.locator("body").inner_text(timeout=20000)
        except Exception as exc:
            error=f"{type(exc).__name__}: {exc}"
            body=""
        tables=await page.locator("table").count() if page.url else 0
        grids=await page.locator("[role='row']").count() if page.url else 0
        generic=await (_link_records(page) if source.mode=="links" else _table_records(page)) if page.url else []
        agency=generic
        if source.agency=="MBTA":
            agency=await _mbta_frame_records(page) or await _mbta_text_records(page)
        elif source.agency=="MARTA":
            agency=await _marta_text_records(page)
        elif source.agency=="MTA" and not agency:
            agency=await _mta_text_records(page)
        elif source.agency=="WMATA":
            agency=_wmata_candidate_records(generic)
        publishable=normalize(source, agency) if agency else []
        report={"agency":source.agency,"source_name":source.name,"requested_url":source.url,
                "final_url":page.url,"navigation_status":response.status if response else None,
                "redirect_chain":nav,"title":await page.title() if page.url else "",
                "body_text_length":len(body),"body_text_sample":sanitize(body),
                "frame_urls":[f.url for f in page.frames],"table_count":tables,
                "grid_row_count":grids,"parser_counts":{"generic_records":len(generic),
                "agency_candidates":len(agency),"publishable_rows":len(publishable)},"error":error}
        await browser.close()
        return report

async def main(output):
    reports=[]
    for agency in AGENCIES:
        source=next(s for s in SOURCES if s.agency==agency)
        reports.append(await collect(source))
    path=Path(output); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(reports,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(reports,indent=2,ensure_ascii=False))

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--output",required=True)
    asyncio.run(main(parser.parse_args().output))
