"""Portable workbook publisher for GitHub/Linux.

Uses openpyxl (already declared in cloud-transit-monitor/requirements.txt).
The normalized payload is the sole source for derived sections; existing
Pursuit Management values/formulas are preserved while adding navigation links.
"""
from __future__ import annotations
import hashlib, json, sys
from copy import copy
from datetime import datetime, date
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.worksheet.table import Table, TableStyleInfo
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

def table_headers(ws, table):
    min_col, min_row, max_col, _ = range_boundaries(table.ref)
    return [ws.cell(min_row, c).value for c in range(min_col, max_col + 1)]

def priority_key(value):
    # Unknown/blank priorities sort below explicitly Low items.
    return {"High": 0, "Medium": 1, "Low": 2}.get(str(value or "").strip().title(), 3)

def valid_opportunity_row(row):
    """Defensive publication gate for payloads produced by older parsers."""
    agency = str(row.get("agency") or "").strip()
    oid = str(row.get("opportunity_id") or "").strip()
    title = str(row.get("project_name") or "").strip()
    if not agency or not oid or not title:
        return False
    if agency == "BART":
        # Never republish PeopleSoft search labels or generated AUTO IDs as
        # solicitation identifiers, even if an old payload contains them.
        import re
        return bool(re.fullmatch(r"BARTD[-\s][A-Z0-9]+(?:[-][A-Z0-9]+)*", oid, re.I))
    return True
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
    # Excel may remove a table during its repair/recovery prompt while leaving
    # the visible sheet data intact. Recreate the operational PM table so one
    # repaired download cannot permanently block the next run.
    if "PursuitManagement" not in tables:
        ws = wb["Pursuit Management"]
        end_row = max(2, ws.max_row)
        pm = Table(displayName="PursuitManagement", ref=f"A1:G{end_row}")
        pm.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showFirstColumn=False, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
        ws.add_table(pm)
        tables = table_map(wb)
    missing = [t for t in REQUIRED_TABLES if t not in tables]
    if missing: raise ValueError(f"missing required tables: {missing}")
    register_ws, register = tables["OpportunityRegister"]
    prior = {}
    reg_rows = rows_from_table(register_ws, register)
    if reg_rows:
        headers = {str(v).strip(): i for i, v in enumerate(reg_rows[0]) if v is not None}
        for row in reg_rows[1:]:
            if headers.get("Opportunity ID") is not None and headers.get("First Seen Date") is not None and row[headers["Opportunity ID"]]:
                prior[str(row[headers["Opportunity ID"]])] = row[headers["First Seen Date"]]
    opps = [o for o in payload.get("opportunities", []) if valid_opportunity_row(o)]
    changes = payload.get("changes", []); sources = payload.get("sources", [])
    run = payload.get("run", {}); run_date = typed_date(run.get("run_date"))
    agencies = sorted({x.get("agency") for x in sources + opps if x.get("agency")})
    high = [o for o in opps if o.get("priority") == "High"]
    failures = [s for s in sources if str(s.get("check_result", "")).lower() != "success"]
    failed_agencies = {s.get("agency") for s in failures}
    changes_agencies = {x.get("agency") for x in changes}
    # Keep the Summary table as a current-run record. The old implementation
    # left projected/template rows in place, which made the sheet look stale.
    summary_ws, summary_table = tables["DailySummary"]
    summary_headers = table_headers(summary_ws, summary_table)
    summary_values = {
        "Run Date": run_date,
        "Status": run.get("status", ""),
        "Report Status": run.get("status", ""),
        "Agencies Checked": len(agencies),
        "Agencies with Changes": len(changes_agencies),
        "Agencies With Changes": len(changes_agencies),
        "High Priority Changes": sum(1 for x in changes if x.get("priority") == "High"),
        "Medium Priority Changes": sum(1 for x in changes if x.get("priority") == "Medium"),
        "Failed Checks": len(failures),
        "Top Priorities": "; ".join(str(o.get("project_name") or "")[:120] for o in high[:5]),
        "Top Opportunities Summary": "; ".join(str(o.get("project_name") or "")[:120] for o in high[:5]),
        "Notes": "Current automated run; see designated agency sheets for solicitation detail.",
    }
    summary_row = [summary_values.get(str(h).strip(), None) for h in summary_headers]
    write_rows(summary_ws, summary_table, [summary_row])
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
    opportunity_locations = {}
    for agency in agencies:
        key = agency.replace(" ", "_"); ws = wb[key] if key in wb.sheetnames else (wb[agency] if agency in wb.sheetnames else None)
        if not ws: continue
        current = next((t for t in ws.tables.values() if t.name in (f"Current_{key}", f"Current_{agency}")), None)
        if current and agency not in failed_agencies:
            # Make the agency page readable: highest-priority solicitations
            # first, with stable tie-breakers so repeated runs do not shuffle.
            ao = sorted(
                [o for o in opps if o.get("agency") == agency],
                key=lambda o: (priority_key(o.get("priority")), str(o.get("opportunity_id") or ""), str(o.get("project_name") or "")),
            )
            write_rows(ws, current, [[agency,next((s.get("source_name") for s in sources if s.get("agency")==agency),""),o.get("source_url"),o.get("opportunity_id"),o.get("project_name"),o.get("description", ""),d(o.get("posted_date")),d(o.get("due_date")),o.get("status"),o.get("change_status", ""),o.get("priority"),o.get("pgh_wong_relevance", ""),d(o.get("last_seen_date") or run.get("run_date")),o.get("notes", "")] for o in ao])
            headers = table_headers(ws, current)
            id_offset = next((i for i, h in enumerate(headers) if str(h or "").strip().lower() in {"opportunity id", "solicitation id", "procurement id"}), None)
            min_col, min_row, _, _ = range_boundaries(current.ref)
            if id_offset is not None:
                for row_index, o in enumerate(ao, min_row + 1):
                    oid = str(o.get("opportunity_id") or "").strip()
                    if oid:
                        opportunity_locations[oid] = (ws.title, f"{get_column_letter(min_col + id_offset)}{row_index}")
        clog = next((t for t in ws.tables.values() if t.name in (f"ChangeLog_{key}", f"ChangeLog_{agency}")), None)
        if clog:
            ac = [c for c in changes if c.get("agency")==agency]
            write_rows(ws, clog, [[d(c.get("run_date") or run.get("run_date")),agency,c.get("source_name", ""),c.get("opportunity_id"),c.get("project_name", ""),c.get("change_type"),c.get("field_changed", ""),c.get("previous_value", ""),c.get("new_value", ""),c.get("status", ""),c.get("priority", ""),c.get("pgh_wong_relevance", ""),c.get("why_it_matters", ""),c.get("source_url"),c.get("check_result", "success"),c.get("notes", "")] for c in ac])

    # Rebuild the operational pursuit view from this run. The prior sheet was
    # intentionally protected, but that left July-era IDs and actions in place
    # after the source set changed. Manual history belongs in Change Logs; the
    # current pursuit table must describe current opportunities.
    pm_ws, pm_table = tables["PursuitManagement"]
    # Remove obsolete hand-maintained fields from the current view. They are
    # not supplied by live agency sources and therefore cannot be kept current.
    pm_headers = ["Agency", "Opportunity ID", "Priority", "Project Name", "Next Action", "Due Date", "Source URL"]
    pm_min_col, pm_min_row, pm_old_max_col, pm_old_max_row = range_boundaries(pm_table.ref)
    for col, header in enumerate(pm_headers, pm_min_col):
        pm_ws.cell(pm_min_row, col).value = header
    for row in range(pm_min_row + 1, max(pm_old_max_row, pm_min_row + len(opps) + 1) + 1):
        for col in range(pm_min_col + len(pm_headers), pm_old_max_col + 1):
            pm_ws.cell(row, col).value = None
    # Keep the XLSX table's serialized column list consistent with its new
    # seven-column range; Excel rejects a table whose ref and tableColumns
    # disagree and offers to repair/remove the table.
    pm_table.tableColumns = pm_table.tableColumns[:len(pm_headers)]
    for column, header in zip(pm_table.tableColumns, pm_headers):
        column.name = header
    pm_table.ref = f"{get_column_letter(pm_min_col)}{pm_min_row}:{get_column_letter(pm_min_col + len(pm_headers) - 1)}{max(pm_min_row + 1, pm_min_row + len(opps) + 1)}"
    pm_rows = []
    for o in sorted(opps, key=lambda x: (priority_key(x.get("priority")), str(x.get("agency") or ""), str(x.get("opportunity_id") or ""))):
        values = {
            "Agency": o.get("agency"),
            "Opportunity ID": o.get("opportunity_id"),
            "Priority": o.get("priority") or "Unclassified",
            "Project Name": o.get("project_name") or "",
            "Next Action": f"Review {o.get('opportunity_id') or 'solicitation'} — {o.get('project_name') or 'project'}; confirm scope, teaming, and go/no-go timing.",
            "Due Date": d(o.get("due_date")),
            "Source URL": o.get("source_url") or "",
        }
        pm_rows.append([values.get(str(h).strip(), "") for h in pm_headers])
    write_rows(pm_ws, pm_table, pm_rows)
    pm_min_col, pm_min_row, _, pm_max_row = range_boundaries(pm_table.ref)
    pm_id_offset = next((i for i, h in enumerate(pm_headers) if str(h or "").strip().lower() in {"opportunity id", "solicitation id", "procurement id"}), None)
    if pm_id_offset is not None:
        for row in range(pm_min_row + 1, pm_max_row + 1):
            cell = pm_ws.cell(row, pm_min_col + pm_id_offset)
            location = opportunity_locations.get(str(cell.value or "").strip())
            if location:
                cell.hyperlink = f"#'{location[0]}'!{location[1]}"
                cell.style = "Hyperlink"

    # Clear current snapshots on agency sheets no longer represented by this
    # run, preventing shelved/blocked agencies from looking current.
    for ws in wb.worksheets:
        for table in list(ws.tables.values()):
            if table.name.startswith("Current_"):
                suffix = table.name[len("Current_"):]
                if suffix.replace("_", " ") not in {a.replace(" ", "_") for a in agencies if a not in failed_agencies} and suffix not in {a for a in agencies if a not in failed_agencies}:
                    write_rows(ws, table, [])
    # Make human-facing columns readable.
    for ws in wb.worksheets:
        for col in range(1, ws.max_column + 1):
            values = [ws.cell(r, col).value for r in range(1, min(ws.max_row, 80) + 1)]
            width = max((len(str(v)) for v in values if v is not None), default=10)
            ws.column_dimensions[get_column_letter(col)].width = min(max(width + 2, 12), 48)
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and any(err in cell.value for err in ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A")): raise ValueError(f"formula error detected: {ws.title}!{cell.coordinate}")
    output.parent.mkdir(parents=True, exist_ok=True); wb.save(output)
    manifest = {"schema_version":"workbook-result-v1","source_id":payload.get("source_id"),"source_sha256":payload.get("source_sha256"),"pipeline":"workbook","canonical_path":str(canonical),"canonical_sha256":sha(canonical.read_bytes()),"output_path":str(output),"output_sha256":sha(output.read_bytes()),"output_size_bytes":output.stat().st_size}
    manifest_path.write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")

if __name__ == "__main__":
    try: main(*(Path(x) for x in sys.argv[1:5]))
    except Exception as exc: print(f"portable_publisher: {exc}", file=sys.stderr); raise SystemExit(1)
