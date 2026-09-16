"""Validate and canonicalize the monitor-to-workbook contract.

The output is deterministic JSON. It is the only input accepted by the
workbook publisher; no section-specific data is generated upstream.
"""
import hashlib, json, re, sys
from datetime import datetime
from pathlib import Path

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T[^\s]+)?$")
PRIORITIES = {"High", "Medium", "Low"}

def fail(message):
    raise ValueError(message)

def date_value(value, field, required=False):
    if value in (None, ""):
        if required: fail(f"missing required date: {field}")
        return None
    if not isinstance(value, str) or not DATE_RE.match(value): fail(f"invalid date: {field}")
    try: datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError: fail(f"invalid date: {field}")
    return value

def required(obj, field):
    value = obj.get(field)
    if not isinstance(value, str) or not value.strip(): fail(f"missing required field: {field}")
    return value.strip()

def main(source, destination):
    raw = json.loads(Path(source).read_text(encoding="utf-8"))
    if raw.get("schema_version") not in (None, "monitor-workbook-payload-v1", "monitor-workbook-acquisition-v1"):
        fail("unsupported schema_version")
    run = raw.get("run") or {}
    run_id = required(run, "run_id")
    run_date = date_value(run.get("run_date"), "run.run_date", True)
    status = run.get("status", "Complete")
    if status not in {"Complete", "Partial", "Failed"}: fail(f"unknown run status: {status}")
    sources, source_agencies = [], {}
    for source_item in raw.get("sources", []):
        agency = required(source_item, "agency"); source_name = required(source_item, "source_name")
        source_url = required(source_item, "source_url"); check = required(source_item, "check_result")
        checked = date_value(source_item.get("checked_at"), "sources.checked_at", True)
        last_success = date_value(source_item.get("last_successful_check"), "sources.last_successful_check")
        source_agencies[agency] = source_name
        sources.append({"agency":agency,"source_name":source_name,"source_url":source_url,"checked_at":checked,"check_result":check,"last_successful_check":last_success,"items_found":int(source_item.get("items_found",0)),"consecutive_failures":int(source_item.get("consecutive_failures",0)),"failure_reason":str(source_item.get("failure_reason", "")),"health_indicator":str(source_item.get("health_indicator", "Unknown")),"source_sha256":str(source_item.get("source_sha256", ""))})
    opportunities, ids, exact_keys = [], set(), set()
    for item in raw.get("opportunities", []):
        agency = required(item, "agency"); oid = required(item, "opportunity_id")
        project_name = required(item, "project_name")
        # An agency may reuse an identifier across distinct procurements. Only
        # an exact duplicate source row is invalid; title/source context keeps
        # separate records distinguishable without inventing a new ID.
        exact_key = (agency, str(item.get("source_name", "")), oid, project_name)
        if exact_key in exact_keys: fail(f"duplicate opportunity row: {agency} / {oid} / {project_name}")
        exact_keys.add(exact_key); ids.add(oid)
        priority = required(item, "priority")
        if priority not in PRIORITIES: fail(f"unknown priority: {priority}")
        item_status = required(item, "status")
        first_seen = date_value(item.get("first_seen_date") or run_date, "opportunities.first_seen_date", True)
        last_seen = date_value(item.get("last_seen_date") or run_date, "opportunities.last_seen_date", True)
        opportunities.append({"agency":agency,"opportunity_id":oid,"project_name":project_name,"description":str(item.get("description", "")),"posted_date":date_value(item.get("posted_date"),"opportunities.posted_date"),"due_date":date_value(item.get("due_date"),"opportunities.due_date"),"status":item_status,"priority":priority,"pgh_wong_relevance":str(item.get("pgh_wong_relevance", "")),"change_status":str(item.get("change_status", "")),"first_seen_date":first_seen,"last_seen_date":last_seen,"source_url":required(item,"source_url"),"opportunity_url":str(item.get("opportunity_url", item.get("source_url"))),"why_it_matters":str(item.get("why_it_matters", item.get("pgh_wong_relevance", ""))),"next_step":str(item.get("next_step", "Review solicitation")),"notes":str(item.get("notes", ""))})
    changes = []
    for change in raw.get("changes", []):
        oid = required(change, "opportunity_id")
        change_type = required(change, "change_type")
        if oid not in ids and not change_type.lower().startswith("removed"): fail(f"change references unknown opportunity_id: {oid}")
        fallback = next((x for x in opportunities if x["opportunity_id"]==oid), {})
        changes.append({"run_date":date_value(change.get("run_date") or run_date,"changes.run_date",True),"agency":required(change,"agency"),"source_name":str(change.get("source_name", source_agencies.get(change["agency"], ""))),"opportunity_id":oid,"project_name":str(change.get("project_name", fallback.get("project_name", ""))),"change_type":change_type,"field_changed":str(change.get("field_changed", "")),"previous_value":str(change.get("previous_value", "")),"new_value":str(change.get("new_value", "")),"status":str(change.get("status", fallback.get("status", "Closed"))),"priority":str(change.get("priority", fallback.get("priority", "Low"))),"pgh_wong_relevance":str(change.get("pgh_wong_relevance", fallback.get("pgh_wong_relevance", ""))),"why_it_matters":str(change.get("why_it_matters", "")),"source_url":required(change,"source_url"),"check_result":str(change.get("check_result", "success")),"notes":str(change.get("notes", ""))})
    normalized = {"schema_version":"monitor-workbook-payload-v1","run":{"run_id":run_id,"run_date":run_date,"status":status},"sources":sorted(sources,key=lambda x:(x["agency"],x["source_name"])),"opportunities":sorted(opportunities,key=lambda x:(x["agency"],x["opportunity_id"])),"changes":sorted(changes,key=lambda x:(x["agency"],x["opportunity_id"],x["change_type"])),"writes":raw.get("writes",[]),"required_values":raw.get("required_values",[]),"required_sheets":raw.get("required_sheets",[]),"required_tables":raw.get("required_tables",[])}
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    normalized["payload_sha256"] = hashlib.sha256(canonical).hexdigest()
    Path(destination).write_text(json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2)+"\n", encoding="utf-8")

if __name__ == "__main__":
    try: main(sys.argv[1], sys.argv[2])
    except Exception as exc: print(f"payload_validation: {exc}", file=sys.stderr); sys.exit(2)
