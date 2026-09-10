"""Acquire multiple configured agency sources and emit one workbook payload.

This adapter performs acquisition only. It never opens Excel or writes the KB.
Each source receives its own deterministic evidence hash inside ``sources``.
"""
from __future__ import annotations
import argparse, asyncio, hashlib, json, re, sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cloud-transit-monitor"))
from src.config import SOURCES  # type: ignore
from src.extract import run_checks  # type: ignore

def iso(value): return value.isoformat() if isinstance(value, date) else value

def looks_like_date_id(value: str) -> bool:
    """Reject a date/time scraped from a row as an opportunity identifier."""
    return bool(re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}(?:\s+.*)?", value.strip()))

async def collect(names, source_names):
    selected = [s for s in SOURCES if (not names or s.agency in names) and (not source_names or s.name in source_names)]
    if not selected: raise RuntimeError(f"no configured sources for {sorted(names)}")
    results = await run_checks(selected)
    checked = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    opportunities, sources = [], []
    for result in results:
        rows=[]
        for item in result.opportunities:
            oid = str(item.opportunity_id or "").strip()
            # Generic parser placeholders are not globally unique. Scope only
            # those placeholders by agency; preserve every real source ID.
            if looks_like_date_id(oid) or re.fullmatch(r"SRC[-_]?\d+", oid, flags=re.I) or oid.lower() in {"procurement","description","project","title","status","tbd"}:
                if looks_like_date_id(oid) or not re.fullmatch(r"SRC[-_]?\d+", oid, flags=re.I):
                    seed = f"{item.agency}|{item.source_name}|{item.project_name}".encode("utf-8", "replace")
                    oid = f"{item.agency}|AUTO-{hashlib.sha256(seed).hexdigest()[:12]}"
                else:
                    oid = f"{item.agency}|{oid.upper()}"
            rows.append({"agency":item.agency,"source_name":item.source_name,"source_url":item.source_url,"opportunity_id":oid,"project_name":item.project_name,"description":item.description,"posted_date":iso(item.posted_date),"due_date":iso(item.due_date),"status":"Open" if item.status.lower() in {"active","open","listed"} else ("Closed" if "closed" in item.status.lower() else "Unknown"),"priority":item.priority,"pgh_wong_relevance":item.relevance,"change_status":item.change_status,"last_seen_date":checked.date().isoformat(),"opportunity_url":item.opportunity_url or item.source_url,"notes":item.notes})
        opportunities.extend(rows)
        evidence=json.dumps(rows,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
        success=result.status != "Failed"
        sources.append({"agency":result.source.agency,"source_name":result.source.name,"source_url":result.source.url,"checked_at":checked.isoformat(),"check_result":"success" if success else "failed","last_successful_check":checked.isoformat() if success else None,"items_found":len(rows),"consecutive_failures":0 if success else 1,"failure_reason":"" if success else result.error,"health_indicator":"Healthy" if success else "Failed","source_sha256":hashlib.sha256(evidence).hexdigest()})
    # Enforce globally unique keys without rewriting genuine IDs that are
    # unique. Duplicate parser/header values are deterministically scoped by
    # agency, source, and title; exact duplicate rows are collapsed.
    grouped={}
    for row in opportunities: grouped.setdefault(str(row.get("opportunity_id","")), []).append(row)
    fixed=[]; seen_exact=set()
    for oid, group in grouped.items():
        for row in group:
            exact=(row.get("agency"),row.get("source_name"),row.get("project_name"),oid)
            if exact in seen_exact: continue
            seen_exact.add(exact)
            if len(group)>1:
                seed=f"{row.get('agency')}|{row.get('source_name')}|{row.get('project_name')}|{oid}".encode("utf-8","replace")
                row=dict(row); row["opportunity_id"]=f"{row.get('agency')}|AUTO-{hashlib.sha256(seed).hexdigest()[:12]}"
            fixed.append(row)
    opportunities=fixed
    failed = [s for s in sources if s["check_result"] != "success"]
    return {"schema_version":"monitor-workbook-acquisition-v1","run":{"run_id":f"multi-live-{checked.strftime('%Y%m%d')}","run_date":checked.date().isoformat(),"status":"Partial" if failed else "Complete"},"sources":sources,"opportunities":opportunities,"changes":[]}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--agency",action="append",default=[]); p.add_argument("--source-name",action="append",default=[]); p.add_argument("--output",required=True); a=p.parse_args()
    try:
        payload=asyncio.run(collect(set(a.agency),set(a.source_name))); out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,sort_keys=True,indent=2,ensure_ascii=False)+"\n",encoding="utf-8"); return 0
    except Exception as exc:
        print(f"multi_agency_acquisition_failed: {exc}",file=sys.stderr); return 1

if __name__ == "__main__": raise SystemExit(main())
