"""Write deterministic, staging-only shadow comparison reports."""
from __future__ import annotations
import hashlib, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""

def _payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def write_report(config: dict, config_path: str | Path, run_id: str, status: str, error: str | None = None) -> Path:
    base = Path(config_path).resolve().parent
    report_dir = Path(config.get("shadow_report_dir", "state/shadow-reports"))
    report_dir = report_dir if report_dir.is_absolute() else base / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    db = Path(config["database_path"]); db = db if db.is_absolute() else base / db
    canonical = Path(config.get("preflight",{}).get("workbook",{}).get("path", "")); canonical = canonical if canonical.is_absolute() else base / canonical
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    current = conn.execute("select * from source_versions where source_id=? order by observed_at desc limit 1", ("multiagency-live",)).fetchone()
    previous = conn.execute("select * from source_versions where source_id=? order by observed_at desc limit 1 offset 1", ("multiagency-live",)).fetchone()
    conn.close()
    report = {"schema_version":"shadow-comparison-v1","run_id":run_id,"created_at":datetime.now(timezone.utc).isoformat(),"status":status,"canonical_sha256":_sha(canonical),"canonical_modified":False,"comparison":{},"error":error or ""}
    if current:
        cur_path=Path(json.loads(current["metadata_json"])["path"]); cur=_payload(cur_path)
        report["current"]={"source_hash":current["content_hash"],"path":str(cur_path),"sources":len(cur.get("sources",[])),"opportunities":len(cur.get("opportunities",[])),"changes":len(cur.get("changes",[])),"agencies":sorted({x.get("agency") for x in cur.get("opportunities",[])})}
        report["source_hashes"]={x.get("agency"):x.get("source_sha256","") for x in cur.get("sources",[])}
        if previous:
            prev_path=Path(json.loads(previous["metadata_json"])["path"]); prev=_payload(prev_path)
            old={str(x.get("opportunity_id")) for x in prev.get("opportunities",[]) if x.get("opportunity_id")}; new={str(x.get("opportunity_id")) for x in cur.get("opportunities",[]) if x.get("opportunity_id")}
            report["comparison"]={"baseline_source_hash":previous["content_hash"],"added_ids":sorted(new-old),"removed_ids":sorted(old-new),"unchanged_or_updated_ids":sorted(new&old),"baseline_opportunities":len(old),"current_opportunities":len(new)}
        else: report["comparison"]={"baseline":"none","note":"first successful staging acquisition"}
    if status != "completed":
        report["exception"]={"reason":error or "orchestrator run failed","action":"investigate; do not promote"}
    stamp=datetime.now().strftime("%Y%m%dT%H%M%S")
    out=report_dir/(f"{stamp}-{run_id}-{'exception' if status!='completed' else 'comparison'}.json")
    out.write_text(json.dumps(report,sort_keys=True,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return out
