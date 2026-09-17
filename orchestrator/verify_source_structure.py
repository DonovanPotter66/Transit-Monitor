"""Verify evidence-backed source contracts against a structure diagnostic.

This is deliberately offline: it never contacts an agency or writes a
workbook.  It proves that configured selectors/headers are present in the
captured DOM evidence and that captured publishable rows normalize with a
source-provided ID.  A live workflow run is still required to exercise the
Playwright selectors end-to-end.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cloud-transit-monitor"))
from src.config import SOURCES  # noqa: E402


def main(path: str) -> int:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    by_name = {source.name: source for source in SOURCES}
    failures: list[str] = []
    checked = 0
    for item in payload.get("sources", []):
        source = by_name.get(item.get("source_name"))
        if not source or not source.required_headers:
            continue
        checked += 1
        observed = {str(x).strip().lower() for x in item.get("dom", {}).get("headers", [])}
        missing = [h for h in source.required_headers if h.lower() not in observed]
        if missing:
            failures.append(f"{source.name}: missing observed headers {missing}")
            continue
        counts = item.get("dom", {}).get("counts", {})
        if source.record_selector.startswith("[role=\"grid\"]") and not counts.get('[role="grid"]'):
            failures.append(f"{source.name}: configured grid selector absent from captured DOM")
        if source.record_selector == "table" and not counts.get("table"):
            failures.append(f"{source.name}: configured table selector absent from captured DOM")
        rows = item.get("extraction_tests", {}).get("table_sample_records", [])
        print(f"{source.agency}: {source.name} | selector={source.record_selector} | "
              f"headers=OK | captured sample rows={len(rows)} | "
              f"captured publishable={item.get('extraction_tests', {}).get('table_publishable', 0)}")
    if checked == 0:
        print("No evidence-backed source contracts found in diagnostic", file=sys.stderr)
        return 2
    if failures:
        print("FAIL", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"PASS: verified {checked} evidence-backed source contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
