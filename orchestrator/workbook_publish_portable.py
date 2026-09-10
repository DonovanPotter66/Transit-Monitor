"""Portable workbook publisher for GitHub/Linux.

Uses openpyxl (already declared in cloud-transit-monitor/requirements.txt) and
keeps Pursuit Management untouched. The normalized payload is the sole source
for all derived sections.
"""
from __future__ import annotations
import hashlib, json, sys
from copy import copy
from datetime import datetime, date
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.utils.cell import range_boundaries, get_column_letter

REQUIRED_SHEETS = ["Dashboard", "Summary", "High Priority Pursuit List", "Opportunity Register", "Source Health", "Pursuit Management"]
REQUIRED_TABLES = ["AgencyOverview", "DailySummary", "HighPriorityPursuits", "OpportunityRegister", "SourceHealth", "PursuitManagement"]

def sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
def typed_date(value):
    if not value: return None
    if isinstance(value, (datetime, date)): return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
def matrix_hash(rows): return sha(json.dumps(rows, default=str, sort_keys=True, ensure_ascii=False).encode())
def table_map(wb):
    out = {}
    for ws in wb.worksheets:
        for table in ws.tables.values(): out[table.name] = (ws, table)
    return out
def rows_from_table(ws, table):
    min_col, min_row, max_col, max_row = range_boundaries(table.ref)
    return [[ws.cell(r, c).value for c in range(min_col, max_col + 1)] for r in range(min_row, max_row + 1)]
def write_rows(ws, table, rows):
    min_col, min_row, max_col, max_row = range_boundaries(table.ref)
    width = max_col - min_col + 1
    # Keep the header and replace the body, expanding the table when needed.
    for r in range(min_row + 1, max_row + 1):
        for c in range(min_col, max_col + 1): ws.cell(r, c).value = None
    for i, row in enumerate(rows, min_row + 1):
        for j, value in enumerate(row[:width], min_col): ws.cell(i, j).value = value
    end = max(min_row + 1, min_row + len(rows) + 1)
    table.ref = f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{end}"
def set_matrix(ws, start_cell, rows):
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
    col, row = coordinate_from_string(start_cell); c0 = column_index_from_string(col)
    for i, values in enumerate(rows):
        for j, value in enumerate(values): ws.cell(row + i, c0 + j).value = value

def main(canonical: Path, payload_path: Path, output: Path, manifest_path: Path):
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    wb = load_workbook(canonical)
    missing = [s for s in REQUIRED_SHEETS if s not in wb.sheetnames]
    if missing: raise ValueError(f"missing required sheets: {missing}")
    tables = table_map(wb)
    missing = [t for t in REQUIRED_TABLES if t not in tables]
    if missing: raise ValueError(f"missing required tables: {missing}")
    protected = wb["Pursuit Management"]
    protected_before = [[cell.value for cell in row] for row in protected.iter_rows()]
    register_ws, register = tables["OpportunityRegister"]
    prior = {}
    reg_rows = rows_from_table(register_ws, register)
    if reg_rows:
        headers = {str(v).strip(): i for i, v in enumerate(reg_rows[0]) if v is not None}
        for row in reg_rows[1:]:
            if headers.get("Opportunity ID") is not None and headers.get("First Seen Date") is not None and row[headers["Opportunity ID"]]:
                prior[str(row[headers["Opportunity ID"]])] = row[headers["First Seen Date"]]
    opps = payload.get("opportunities", []); changes = payload.get("changes", []); sources = payload.get("sources", [])
    run = payload.get("run", {}); run_date = typed_date(run.get("run_date"))
    agencies = sorted({x.get("agency") for x in sources + opps if x.get("agency")})
    high = [o for o in opps if o.get("priority") == "High"]
    failures = [s for s in sources if str(s.get("check_result", "")).lower() != "success"]
    changes_agencies = {x.get("agency") for x in changes}
    # Summary and dashboard banners.
    # Row 23 previously contained misplaced internal metadata; keep it clear.
    set_matrix(wb["Summary"], "A23", [[None] * 9])
    set_matrix(wb["Dashboard"], "A2", [[f"Latest run: {run.get('run_date','')} | Status: {run.get('status','')} | Agencies checked: {len(agencies)} | Agencies with changes: {len(changes_agencies)} | Failed checks: {len(failures)}"]])
    set_matrix(wb["Dashboard"], "A21", [[f"{run.get('run_date','')}: {len(changes)} material changes"]])
    def d(v): return typed_date(v)
    write_rows(*tables["OpportunityRegister"], [[o.get("agency"),o.get("opportunity_id"),o.get("project_name"),o.get("description", ""),d(o.get("posted_date")),d(o.get("due_date")),o.get("status"),o.get("priority"),o.get("pgh_wong_relevance", ""),o.get("change_status", ""),prior.get(str(o.get("opportunity_id")), d(o.get("first_seen_date") or run.get("run_date"))),d(o.get("last_seen_date") or run.get("run_date")),o.get("source_url"),o.get("opportunity_url", o.get("source_url"))] for o in opps])
    write_rows(*tables["HighPriorityPursuits"], [[run_date,o.get("agency"),o.get("opportunity_id"),o.get("project_name"),o.get("priority"),o.get("why_it_matters", ""),f"{o.get('next_step') or 'Review solicitation'} [{o.get('opportunity_id')}] — {o.get('project_name')}",o.get("source_url")] for o in high])
    write_rows(*tables["SourceHealth"], [[s.get("agency"),s.get("source_name"),d(s.get("checked_at")),s.get("check_result"),d(s.get("last_successful_check")),s.get("items_found",0),s.get("consecutive_failures",0),s.get("failure_reason", ""),s.get("health_indicator", "Unknown")] for s in sources])
    write_rows(*tables["AgencyOverview"], [[a,sum(o.get("agency")==a for o in opps),sum(o.get("agency")==a and o.get("priority")=="High" for o in opps),run_date if any(c.get("agency")==a for c in changes) else None,"; ".join(c.get("change_type", "") for c in changes if c.get("agency")==a),next((o.get("project_name") for o in opps if o.get("agency")==a),None),next((o.get("priority") for o in opps if o.get("agency")==a),"No high-priority opportunities found"),d(next((o.get("due_date") for o in opps if o.get("agency")==a),None)),next((s.get("source_url") for s in sources if s.get("agency")==a),None)] for a in agencies])
    for agency in agencies:
        key = agency.replace(" ", "_"); ws = wb[key] if key in wb.sheetnames else (wb[agency] if agency in wb.sheetnames else None)
        if not ws: continue
        current = next((t for t in ws.tables.values() if t.name in (f"Current_{key}", f"Current_{agency}")), None)
        if current:
            ao = [o for o in opps if o.get("agency")==agency]
            write_rows(ws, current, [[agency,next((s.get("source_name") for s in sources if s.get("agency")==agency),""),o.get("source_url"),o.get("opportunity_id"),o.get("project_name"),o.get("description", ""),d(o.get("posted_date")),d(o.get("due_date")),o.get("status"),o.get("change_status", ""),o.get("priority"),o.get("pgh_wong_relevance", ""),d(o.get("last_seen_date") or run.get("run_date")),o.get("notes", "")] for o in ao])
        clog = next((t for t in ws.tables.values() if t.name in (f"ChangeLog_{key}", f"ChangeLog_{agency}")), None)
        if clog:
            ac = [c for c in changes if c.get("agency")==agency]
            write_rows(ws, clog, [[d(c.get("run_date") or run.get("run_date")),agency,c.get("source_name", ""),c.get("opportunity_id"),c.get("project_name", ""),c.get("change_type"),c.get("field_changed", ""),c.get("previous_value", ""),c.get("new_value", ""),c.get("status", ""),c.get("priority", ""),c.get("pgh_wong_relevance", ""),c.get("why_it_matters", ""),c.get("source_url"),c.get("check_result", "success"),c.get("notes", "")] for c in ac])
    # Make human-facing columns readable without changing the protected sheet.
    for ws in wb.worksheets:
        for col in range(1, ws.max_column + 1):
            values = [ws.cell(r, col).value for r in range(1, min(ws.max_row, 80) + 1)]
            width = max((len(str(v)) for v in values if v is not None), default=10)
            ws.column_dimensions[get_column_letter(col)].width = min(max(width + 2, 12), 48)
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and any(err in cell.value for err in ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A")): raise ValueError(f"formula error detected: {ws.title}!{cell.coordinate}")
    protected_after = [[cell.value for cell in row] for row in protected.iter_rows()]
    if matrix_hash(protected_before) != matrix_hash(protected_after): raise ValueError("protected Pursuit Management changed")
    output.parent.mkdir(parents=True, exist_ok=True); wb.save(output)
    manifest = {"schema_version":"workbook-result-v1","source_id":payload.get("source_id"),"source_sha256":payload.get("source_sha256"),"pipeline":"workbook","canonical_path":str(canonical),"canonical_sha256":sha(canonical.read_bytes()),"output_path":str(output),"output_sha256":sha(output.read_bytes()),"output_size_bytes":output.stat().st_size}
    manifest_path.write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")

if __name__ == "__main__":
    try: main(*(Path(x) for x in sys.argv[1:5]))
    except Exception as exc: print(f"portable_publisher: {exc}", file=sys.stderr); raise SystemExit(1)
