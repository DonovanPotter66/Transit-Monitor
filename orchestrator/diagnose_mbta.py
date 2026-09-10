"""Cloud-only MBTA page diagnostics; never writes or publishes a workbook."""
import argparse, asyncio, json, re, sys
from pathlib import Path
from playwright.async_api import async_playwright
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cloud-transit-monitor"))
from src.extract import _table_records, _mbta_frame_records, _mbta_text_records

URL = "https://bc.mbta.com/business_center/bidding_solicitations/future_prof_services_solicitations/"

async def collect():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        redirects = []
        page.on("response", lambda response: redirects.append({"url": response.url, "status": response.status}) if response.request.is_navigation_request() else None)
        await page.goto(URL, wait_until="domcontentloaded", timeout=90_000)
        await page.wait_for_timeout(4_000)
        body = await page.locator("body").inner_text()
        sample = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "[redacted-email]", body)
        sample = re.sub(r"https?://\\S+", "[redacted-url]", sample)
        sample = re.sub(r"\\s+", " ", sample).strip()[:3000]
        report = {
            "source_url": URL, "final_url": page.url, "redirect_chain": redirects,
            "title": await page.title(), "body_text_length": len(body),
            "body_text_sample": sample, "frame_urls": [frame.url for frame in page.frames],
            "table_count": await page.locator("table").count(),
            "grid_row_count": await page.locator("[role='row']").count(),
            "parser_counts": {"table": len(await _table_records(page)), "frame_table": len(await _mbta_frame_records(page)), "text_rows": len(await _mbta_text_records(page))},
        }
        await browser.close()
        return report

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--output", required=True); args = parser.parse_args()
    report = asyncio.run(collect()); path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))

if __name__ == "__main__": main()
