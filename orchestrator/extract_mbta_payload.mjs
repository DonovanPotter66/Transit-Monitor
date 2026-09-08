import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [snapshot, output] = process.argv.slice(2);
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(snapshot));
const serialDate = value => {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "number") return new Date(Date.UTC(1899, 11, 30) + value * 86400000).toISOString().slice(0, 10);
  const d = new Date(value); return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
};
const table = name => {
  for (const sheet of wb.worksheets.items) for (const t of sheet.tables.items) if (t.name === name) return t.getRange().values;
  throw new Error(`missing table ${name}`);
};
const current = table("Current_MBTA");
const changesTable = table("ChangeLog_MBTA");
const summary = table("DailySummary");
const lastSummary = [...summary.slice(1)].reverse().find(r => r.some(v => v !== null && v !== "")) || [];
const runDate = serialDate(lastSummary[0]) || new Date().toISOString().slice(0, 10);
const sourceName = current.slice(1).find(r => r[1])?.[1] || "MBTA Current Solicitations";
const sourceUrl = current.slice(1).find(r => r[2])?.[2] || "";
if (!sourceUrl) throw new Error("MBTA source URL missing");
const normalizeStatus = value => { const s=String(value||"").toLowerCase(); if (s.includes("active")||s.includes("open")||s.includes("listed")||s.includes("solicitation")) return "Open"; if (s.includes("award")||s.includes("closed")) return "Closed"; return "Unknown"; };
const normalizePriority = value => ["High","Medium","Low"].includes(value) ? value : "Low";
const opportunities = current.slice(1).filter(r => r[3]).map(r => ({
  agency: "MBTA", source_name: r[1] || sourceName, source_url: r[2] || sourceUrl,
  opportunity_id: String(r[3]), project_name: String(r[4] || ""), description: String(r[5] || ""),
  posted_date: serialDate(r[6]), due_date: serialDate(r[7]), status: normalizeStatus(r[8]),
  priority: normalizePriority(r[10]), pgh_wong_relevance: String(r[11] || ""), change_status: String(r[9] || ""),
  first_seen_date: runDate, last_seen_date: serialDate(r[12]) || runDate, opportunity_url: r[2] || sourceUrl,
  why_it_matters: String(r[11] || ""), next_step: "Review solicitation", notes: String(r[13] || "")
}));
const ids = new Set(opportunities.map(o => o.opportunity_id));
const changes = changesTable.slice(1).filter(r => r[3] && ids.has(String(r[3]))).map(r => ({
  run_date: serialDate(r[0]) || runDate, agency: "MBTA", source_name: String(r[2] || sourceName), opportunity_id: String(r[3]),
  project_name: String(r[4] || ""), change_type: String(r[5] || "Change Detected"), field_changed: String(r[6] || ""),
  previous_value: String(r[7] || ""), new_value: String(r[8] || ""), status: normalizeStatus(r[9]), priority: normalizePriority(r[10]),
  pgh_wong_relevance: String(r[11] || ""), why_it_matters: String(r[12] || ""), source_url: String(r[13] || sourceUrl),
  check_result: String(r[14] || "success").toLowerCase() === "successful" ? "success" : String(r[14] || "success"), notes: String(r[15] || "")
}));
const payload = { schema_version:"monitor-workbook-payload-v1", run:{run_id:`mbta-snapshot-${runDate}`,run_date:runDate,status:"Complete"}, sources:[{agency:"MBTA",source_name:sourceName,source_url:sourceUrl,checked_at:`${runDate}T00:00:00Z`,check_result:"success",last_successful_check:`${runDate}T00:00:00Z`,items_found:opportunities.length,consecutive_failures:0,failure_reason:"",health_indicator:"Healthy"}], opportunities, changes };
await fs.writeFile(output, JSON.stringify(payload,null,2)+"\n", "utf8");
