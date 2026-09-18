"""Build a static transit monitor website from the workbook payload."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import UTC, datetime
from html import escape
from pathlib import Path


SOUND_SAFE_PREFIXES = r"(?:RFP|RFQ|IFB|RFI|P|AE|RP|GC|CN|DB|IB)"


def valid_opportunity_row(row: dict) -> bool:
    agency = str(row.get("agency") or "").strip()
    oid = str(row.get("opportunity_id") or "").strip()
    title = str(row.get("project_name") or "").strip()
    if not agency or not oid or not title:
        return False
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}(?:\s+.*)?", oid):
        return False
    if not re.search(r"\d", oid):
        return False
    if re.fullmatch(r"(?:[A-Za-z ]+\|)?(?:SRC|AUTO)[-_][A-Za-z0-9-]+", oid, re.I):
        return False
    if oid.casefold() == title.casefold() and not re.search(r"\d", oid):
        return False
    if oid.casefold() in {
        "program", "philadelphia", "status", "contract type", "event name",
        "solicitation id", "end date", "start date", "description", "project", "title",
    }:
        return False
    if re.search(r"\s", oid) and not re.fullmatch(
        rf"{SOUND_SAFE_PREFIXES}\s*[-#]?\s*[A-Z0-9-]*\d[A-Z0-9-]*", oid, re.I
    ):
        return False
    if agency == "BART":
        return bool(re.fullmatch(r"BARTD[-\s][A-Z0-9]+(?:[-][A-Z0-9]+)*", oid, re.I))
    return True


def priority_rank(value: str) -> int:
    return {"High": 0, "Medium": 1, "Low": 2}.get(str(value or "").strip().title(), 3)


def date_key(value: str) -> str:
    value = str(value or "")
    return value or "9999-12-31"


def clean_opportunity(item: dict) -> dict:
    return {
        "agency": str(item.get("agency") or ""),
        "source_name": str(item.get("source_name") or ""),
        "source_url": str(item.get("source_url") or ""),
        "opportunity_id": str(item.get("opportunity_id") or ""),
        "project_name": str(item.get("project_name") or ""),
        "description": str(item.get("description") or ""),
        "posted_date": str(item.get("posted_date") or ""),
        "due_date": str(item.get("due_date") or ""),
        "status": str(item.get("status") or ""),
        "priority": str(item.get("priority") or "Low"),
        "pgh_wong_relevance": str(item.get("pgh_wong_relevance") or item.get("why_it_matters") or ""),
        "change_status": str(item.get("change_status") or ""),
        "last_seen_date": str(item.get("last_seen_date") or ""),
        "source_url": str(item.get("source_url") or ""),
        "opportunity_url": str(item.get("opportunity_url") or item.get("source_url") or ""),
        "notes": str(item.get("notes") or ""),
    }


def load_payload(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    opportunities = [
        clean_opportunity(item)
        for item in payload.get("opportunities", [])
        if isinstance(item, dict) and valid_opportunity_row(item)
    ]
    opportunities.sort(key=lambda item: (
        priority_rank(item.get("priority", "")),
        date_key(item.get("due_date", "")),
        item.get("agency", ""),
        item.get("opportunity_id", ""),
    ))
    sources = [item for item in payload.get("sources", []) if isinstance(item, dict)]
    agencies = sorted({item.get("agency") for item in sources + opportunities if item.get("agency")})
    run = payload.get("run", {}) if isinstance(payload.get("run"), dict) else {}
    return {
        "schema_version": "transit-monitor-site-v1",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "run": run,
        "sources": sources,
        "opportunities": opportunities,
        "agencies": agencies,
    }


def metric_cards(data: dict) -> str:
    opportunities = data["opportunities"]
    sources = data["sources"]
    failed = [s for s in sources if str(s.get("check_result", "")).lower() != "success"]
    counts = Counter(item.get("priority") for item in opportunities)
    cards = [
        ("Opportunities", len(opportunities)),
        ("High Priority", counts.get("High", 0)),
        ("Agencies", len(data["agencies"])),
        ("Source Issues", len(failed)),
    ]
    return "\n".join(
        f'<article class="metric"><span>{escape(label)}</span><strong>{value}</strong></article>'
        for label, value in cards
    )


def source_rows(data: dict) -> str:
    rows = []
    for source in sorted(data["sources"], key=lambda item: (str(item.get("agency")), str(item.get("source_name")))):
        result = str(source.get("check_result") or "unknown")
        state = "ok" if result.lower() == "success" else "bad"
        rows.append(
            "<tr>"
            f"<td>{escape(str(source.get('agency') or ''))}</td>"
            f"<td>{escape(str(source.get('source_name') or ''))}</td>"
            f'<td><span class="pill {state}">{escape(result.title())}</span></td>'
            f"<td>{escape(str(source.get('items_found') or 0))}</td>"
            f"<td>{escape(str(source.get('failure_reason') or ''))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def html(data: dict) -> str:
    run_date = escape(str(data.get("run", {}).get("run_date") or ""))
    generated = escape(str(data.get("generated_at") or ""))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Transit Agency Monitor</title>
  <meta name="description" content="Daily transit procurement monitor dashboard.">
  <link rel="icon" type="image/svg+xml" href="favicon.svg">
  <link rel="stylesheet" href="assets/site.css">
</head>
<body>
  <header class="topbar">
    <div>
      <p class="eyebrow">Daily procurement monitor</p>
      <h1>Transit Agency Monitor</h1>
    </div>
    <div class="run-meta">
      <span>Run date: <strong>{run_date}</strong></span>
      <span>Generated: <strong>{generated}</strong></span>
    </div>
  </header>

  <main>
    <section class="metrics" aria-label="Monitor summary">
      {metric_cards(data)}
    </section>

    <section class="controls" aria-label="Opportunity filters">
      <label>
        Search
        <input id="search" type="search" placeholder="Agency, ID, title, scope">
      </label>
      <label>
        Agency
        <select id="agencyFilter"><option value="">All agencies</option></select>
      </label>
      <label>
        Priority
        <select id="priorityFilter">
          <option value="">All priorities</option>
          <option>High</option>
          <option>Medium</option>
          <option>Low</option>
        </select>
      </label>
      <label>
        Status
        <select id="statusFilter"><option value="">All statuses</option></select>
      </label>
    </section>

    <section class="panel">
      <div class="panel-heading">
        <h2>Current Opportunities</h2>
        <p><span id="visibleCount">0</span> shown</p>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Agency</th>
              <th>ID</th>
              <th>Project</th>
              <th>Due</th>
              <th>Status</th>
              <th>Priority</th>
              <th>Relevance</th>
            </tr>
          </thead>
          <tbody id="opportunityRows"></tbody>
        </table>
      </div>
    </section>

    <section class="panel source-panel">
      <div class="panel-heading">
        <h2>Source Health</h2>
        <p>Latest acquisition check by agency source</p>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Agency</th>
              <th>Source</th>
              <th>Result</th>
              <th>Rows</th>
              <th>Issue</th>
            </tr>
          </thead>
          <tbody>
            {source_rows(data)}
          </tbody>
        </table>
      </div>
    </section>
  </main>

  <script src="data/opportunities.json"></script>
  <script src="assets/site.js"></script>
</body>
</html>
"""


CSS = """
:root {
  color-scheme: light;
  --ink: #15202b;
  --muted: #5f6f7c;
  --line: #d7e0e7;
  --panel: #ffffff;
  --page: #f4f7f9;
  --accent: #0f6b63;
  --accent-strong: #084b45;
  --warn: #9b3d14;
  --bad: #9f1d2f;
  --good: #1e6f45;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--page);
  color: var(--ink);
}
.topbar {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  align-items: end;
  padding: 24px 32px 18px;
  background: #ffffff;
  border-bottom: 1px solid var(--line);
}
.eyebrow {
  margin: 0 0 4px;
  color: var(--accent);
  font-size: 14px;
  font-weight: 700;
  text-transform: uppercase;
}
h1, h2 { margin: 0; letter-spacing: 0; }
h1 { font-size: 28px; }
h2 { font-size: 18px; }
.run-meta {
  display: grid;
  gap: 4px;
  color: var(--muted);
  font-size: 14px;
  text-align: right;
}
main {
  padding: 20px 32px 36px;
  display: grid;
  gap: 18px;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.metric {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 16px;
}
.metric span {
  display: block;
  color: var(--muted);
  font-size: 14px;
  margin-bottom: 8px;
}
.metric strong { font-size: 30px; }
.controls {
  display: grid;
  grid-template-columns: minmax(220px, 1.6fr) repeat(3, minmax(150px, 1fr));
  gap: 12px;
  align-items: end;
}
label {
  display: grid;
  gap: 6px;
  color: var(--muted);
  font-size: 14px;
  font-weight: 650;
}
input, select {
  width: 100%;
  min-height: 42px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  color: var(--ink);
  font: inherit;
  padding: 8px 10px;
}
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}
.panel-heading {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 16px;
  border-bottom: 1px solid var(--line);
}
.panel-heading p {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
}
.table-wrap { overflow-x: auto; }
table {
  width: 100%;
  border-collapse: collapse;
  min-width: 980px;
}
th, td {
  padding: 11px 12px;
  text-align: left;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
  font-size: 14px;
}
th {
  color: #33424f;
  background: #edf3f5;
  font-size: 13px;
  text-transform: uppercase;
}
td.project { min-width: 280px; font-weight: 650; }
td.relevance { max-width: 360px; color: var(--muted); }
a { color: var(--accent-strong); text-decoration-thickness: 1px; }
.pill {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef3f5;
  color: #344854;
  font-size: 13px;
  font-weight: 700;
  white-space: nowrap;
}
.pill.high { background: #fbe8df; color: var(--warn); }
.pill.medium { background: #e6f0f9; color: #22577a; }
.pill.low { background: #eef3f5; color: #4d5d68; }
.pill.ok { background: #e3f3e9; color: var(--good); }
.pill.bad { background: #f8e2e6; color: var(--bad); }
.empty {
  padding: 24px;
  color: var(--muted);
}
@media (max-width: 900px) {
  .topbar { display: grid; padding: 20px; }
  .run-meta { text-align: left; }
  main { padding: 16px; }
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .controls { grid-template-columns: 1fr; }
}
@media (max-width: 560px) {
  .metrics { grid-template-columns: 1fr; }
  h1 { font-size: 24px; }
}
"""


JS = """
const DATA = window.TRANSIT_MONITOR_DATA || { opportunities: [], agencies: [] };
const rowsEl = document.getElementById("opportunityRows");
const visibleCount = document.getElementById("visibleCount");
const search = document.getElementById("search");
const agencyFilter = document.getElementById("agencyFilter");
const priorityFilter = document.getElementById("priorityFilter");
const statusFilter = document.getElementById("statusFilter");

function unique(values) {
  return [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function option(value) {
  const el = document.createElement("option");
  el.value = value;
  el.textContent = value;
  return el;
}

unique(DATA.opportunities.map(item => item.agency)).forEach(value => agencyFilter.append(option(value)));
unique(DATA.opportunities.map(item => item.status)).forEach(value => statusFilter.append(option(value)));

function pillClass(priority) {
  return String(priority || "").toLowerCase();
}

function matches(item) {
  const q = search.value.trim().toLowerCase();
  const haystack = [item.agency, item.opportunity_id, item.project_name, item.description, item.pgh_wong_relevance]
    .join(" ")
    .toLowerCase();
  return (!q || haystack.includes(q))
    && (!agencyFilter.value || item.agency === agencyFilter.value)
    && (!priorityFilter.value || item.priority === priorityFilter.value)
    && (!statusFilter.value || item.status === statusFilter.value);
}

function render() {
  const visible = DATA.opportunities.filter(matches);
  visibleCount.textContent = visible.length;
  if (!visible.length) {
    rowsEl.innerHTML = `<tr><td colspan="7" class="empty">No opportunities match these filters.</td></tr>`;
    return;
  }
  rowsEl.innerHTML = visible.map(item => {
    const href = item.opportunity_url || item.source_url || "#";
    const title = escapeHtml(item.project_name || "");
    return `<tr>
      <td>${escapeHtml(item.agency || "")}</td>
      <td><a href="${escapeAttr(href)}" target="_blank" rel="noopener">${escapeHtml(item.opportunity_id || "")}</a></td>
      <td class="project">${title}<div class="subtext">${escapeHtml(item.description || "")}</div></td>
      <td>${escapeHtml(item.due_date || "")}</td>
      <td>${escapeHtml(item.status || "")}</td>
      <td><span class="pill ${pillClass(item.priority)}">${escapeHtml(item.priority || "")}</span></td>
      <td class="relevance">${escapeHtml(item.pgh_wong_relevance || "")}</td>
    </tr>`;
  }).join("");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  }[char]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#096;");
}

[search, agencyFilter, priorityFilter, statusFilter].forEach(el => el.addEventListener("input", render));
render();
"""


def write_site(data: dict, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "assets").mkdir(exist_ok=True)
    (destination / "data").mkdir(exist_ok=True)
    (destination / ".nojekyll").write_text("", encoding="utf-8")
    (destination / "index.html").write_text(html(data), encoding="utf-8")
    (destination / "assets" / "site.css").write_text(CSS.strip() + "\n", encoding="utf-8")
    (destination / "assets" / "site.js").write_text(JS.strip() + "\n", encoding="utf-8")
    (destination / "data" / "opportunities.json").write_text(
        "window.TRANSIT_MONITOR_DATA = "
        + json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    favicon = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#0f6b63"/><path d="M8 9h16v8H8z" fill="#fff"/><path d="M10 20h12M12 24h8" stroke="#fff" stroke-width="2" stroke-linecap="round"/><path d="M11 12h10" stroke="#0f6b63" stroke-width="2" stroke-linecap="round"/></svg>"""
    (destination / "favicon.svg").write_text(favicon, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    data = load_payload(args.payload)
    write_site(data, args.destination)
    print(f"site_publish: wrote {len(data['opportunities'])} opportunities to {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
