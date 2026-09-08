import fs from "node:fs/promises";
import crypto from "node:crypto";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [canonical, payloadPath, outputPath, manifestPath] = process.argv.slice(2);
const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
const sha = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
const matrixHash = (values, formulas) => sha(Buffer.from(JSON.stringify({ values, formulas })));

const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(canonical));
const requiredSheets = payload.required_sheets || ["Dashboard","Summary","High Priority Pursuit List","Opportunity Register","Source Health","Pursuit Management"];
const requiredTables = payload.required_tables || ["AgencyOverview","DailySummary","HighPriorityPursuits","OpportunityRegister","SourceHealth","PursuitManagement"];
const sheets = wb.worksheets.items;
const sheetNames = sheets.map(s => s.name);
for (const name of requiredSheets) if (!sheetNames.includes(name)) throw new Error(`missing required sheet: ${name}`);
const tables = sheets.flatMap(s => s.tables.items.map(t => ({sheet:s.name,name:t.name,range:t.getRange().address})));
for (const name of requiredTables) if (!tables.some(t => t.name === name)) throw new Error(`missing required table: ${name}`);

const protectedSheet = wb.worksheets.getItem("Pursuit Management");
const protectedRange = protectedSheet.getUsedRange();
const protectedBefore = { values: protectedRange.values, formulas: protectedRange.formulas };
const protectedBeforeHash = matrixHash(protectedBefore.values, protectedBefore.formulas);

const protectedAfter = { values: protectedRange.values, formulas: protectedRange.formulas };
if (matrixHash(protectedAfter.values, protectedAfter.formulas) !== protectedBeforeHash) throw new Error("protected Pursuit Management changed");

// Derive all operational sections from the same normalized records. Dates are
// converted to Date objects so Excel receives typed date serials.
// Excel date serial (with the workbook's existing date formatting) keeps the
// values numeric/typed instead of writing locale-dependent date strings.
const asDate = v => { if (!v) return null; const d=new Date(v); return (Date.UTC(d.getUTCFullYear(),d.getUTCMonth(),d.getUTCDate(),d.getUTCHours(),d.getUTCMinutes(),d.getUTCSeconds())-Date.UTC(1899,11,30))/86400000; };
const opps = payload.opportunities || [], changes = payload.changes || [], sources = payload.sources || [];
const existingRegister = wb.worksheets.getItem("Opportunity Register").tables.items.find(t=>t.name==="OpportunityRegister").getRange().values || [];
const priorFirstSeen = new Map(existingRegister.slice(1).map(r=>[r[1],r[10]]).filter(x=>x[0]));
const derivedWrites = [];
const add = (sheet, range, values) => derivedWrites.push({sheet,range,values});
const colName = n => { let s=""; while(n){ const r=(n-1)%26; s=String.fromCharCode(65+r)+s; n=Math.floor((n-1)/26); } return s; };
// Clear the prior derived table body before writing this run.  This prevents
// stale rows from a larger prior run remaining visible during a partial
// (for example, two-agency) staging run.  Manual/Pursuit Management data is
// never touched here.
const clearDerivedBody = (sheetName, tableName, startRow, cols) => {
  const sheet = wb.worksheets.getItem(sheetName);
  const table = sheet.tables.items.find(t=>t.name===tableName);
  if (!table) return;
  const match = String(table.getRange().address||"").match(/^[A-Z]+\d+:[A-Z]+(\d+)$/);
  const endRow = match ? Number(match[1]) : startRow - 1;
  if (endRow < startRow) return;
  sheet.getRange(`A${startRow}:${colName(cols)}${endRow}`).values = Array.from({length:endRow-startRow+1},()=>Array(cols).fill(null));
};
const runDate = asDate(payload.run.run_date);
const high = opps.filter(o=>o.priority === "High");
const agencies = [...new Set([...sources.map(s=>s.agency), ...opps.map(o=>o.agency)])].sort();
const failures = sources.filter(s=>s.check_result.toLowerCase() !== "success");
clearDerivedBody("Opportunity Register","OpportunityRegister",2,14);
clearDerivedBody("High Priority Pursuit List","HighPriorityPursuits",2,8);
clearDerivedBody("Source Health","SourceHealth",2,9);
clearDerivedBody("Dashboard","AgencyOverview",6,10);
add("Summary", "A23:I23", [[runDate,payload.run.status,agencies.length,new Set(changes.map(c=>c.agency)).size,changes.filter(c=>c.priority==="High").length,changes.filter(c=>c.priority==="Medium").length,failures.length,high.slice(0,5).map(o=>o.project_name).join("; "),"Normalized payload validated"]]);
// Keep the dashboard's run banner synchronized with the same normalized
// payload.  The agency overview table below is derived from these records;
// leaving the historical banner in place would incorrectly report 12 agencies
// for a deliberately shelved nine-agency publication.
add("Dashboard", "A2", [[`Latest run: ${payload.run.run_date || ""} | Status: ${payload.run.status || ""} | Agencies checked: ${agencies.length} | Agencies with changes: ${new Set(changes.map(c=>c.agency)).size} | Failed checks: ${failures.length}`]]);
add("Dashboard", "A21", [[`${payload.run.run_date || ""}: ${changes.length} material changes`]]);
add("Opportunity Register", `A2:N${Math.max(2,opps.length+1)}`, opps.map(o=>[o.agency,o.opportunity_id,o.project_name,o.description,asDate(o.posted_date),asDate(o.due_date),o.status,o.priority,o.pgh_wong_relevance,o.change_status,priorFirstSeen.get(o.opportunity_id)||asDate(o.first_seen_date),asDate(o.last_seen_date),o.source_url,o.opportunity_url]));
add("High Priority Pursuit List", `A2:H${Math.max(2,high.length+1)}`, high.map(o=>[runDate,o.agency,o.opportunity_id,o.project_name,o.priority,o.why_it_matters,o.next_step,o.source_url]));
add("Source Health", `A2:I${Math.max(2,sources.length+1)}`, sources.map(s=>[s.agency,s.source_name,asDate(s.checked_at),s.check_result,asDate(s.last_successful_check),s.items_found,s.consecutive_failures,s.failure_reason,s.health_indicator]));
add("Dashboard", `A6:J${Math.max(6,agencies.length+5)}`, agencies.map(agency=>{const ao=opps.filter(o=>o.agency===agency), ac=changes.filter(c=>c.agency===agency), top=[...ao].sort((a,b)=>(a.due_date||"9999").localeCompare(b.due_date||"9999"))[0], src=sources.find(s=>s.agency===agency); return [agency,ao.length,ao.filter(o=>o.priority==="High").length,ac[0]?runDate:null,ac.map(c=>c.change_type).join("; "),top?top.project_name:null,top?top.priority:null,top?asDate(top.due_date):null,src?src.source_url:null,"Derived from normalized payload"]; }));
for (const agency of agencies) {
  const sheetName = agency.replace(/ /g,"_");
  const current = wb.worksheets.items.find(s=>s.name===sheetName || s.name===agency);
  const agencyOpps = opps.filter(o=>o.agency===agency);
  if (current) {
    const currentTable=current.tables.items.find(t=>t.name===`Current_${sheetName}` || t.name===`Current_${agency}`);
    if (currentTable && agencyOpps.length) add(current.name,`A2:N${agencyOpps.length+1}`,agencyOpps.map(o=>[o.agency,sources.find(s=>s.agency===agency)?.source_name||"",o.source_url,o.opportunity_id,o.project_name,o.description,asDate(o.posted_date),asDate(o.due_date),o.status,o.change_status,o.priority,o.pgh_wong_relevance,asDate(o.last_seen_date),o.notes]));
    const changeTable=current.tables.items.find(t=>t.name===`ChangeLog_${sheetName}` || t.name===`ChangeLog_${agency}`);
    const agencyChanges=changes.filter(c=>c.agency===agency);
    if (changeTable && agencyChanges.length) {
      const addr=changeTable.getRange().address, start=Number((addr.match(/^[A-Z]+(\d+)/)||[])[1]||120)+1;
      add(current.name,`A${start}:P${start+agencyChanges.length-1}`,agencyChanges.map(c=>[asDate(c.run_date),c.agency,c.source_name,c.opportunity_id,c.project_name,c.change_type,c.field_changed,c.previous_value,c.new_value,c.status,c.priority,c.pgh_wong_relevance,c.why_it_matters,c.source_url,c.check_result,c.notes]));
    }
  }
}
payload.writes = [...derivedWrites, ...(payload.writes || [])];
for (const write of (payload.writes || [])) {
  const range = wb.worksheets.getItem(write.sheet).getRange(write.range);
  if (write.values) range.values = write.values;
  if (write.formulas) range.formulas = write.formulas;
}

const formulaError = /#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A/;
for (const sheet of sheets) {
  const used = sheet.getUsedRange();
  const values = used.values || [];
  const formulas = used.formulas || [];
  for (const row of [...values, ...formulas]) {
    for (const cell of (row || [])) if (typeof cell === "string" && formulaError.test(cell)) throw new Error(`formula error detected on ${sheet.name}: ${cell}`);
  }
}
for (const check of (payload.required_values || [])) {
  const values = wb.worksheets.getItem(check.sheet).getRange(check.range).values;
  if (JSON.stringify(values) !== JSON.stringify(check.values)) throw new Error(`content check failed: ${check.sheet}!${check.range}`);
}

const outDir = path.dirname(outputPath); await fs.mkdir(outDir,{recursive:true});
for (const sheet of sheets) {
  const preview = await wb.render({sheetName:sheet.name,autoCrop:"all",scale:0.25,format:"png"});
  await fs.writeFile(path.join(outDir,`render-${sheet.name.replace(/[^A-Za-z0-9_-]/g,"_")}.png`),new Uint8Array(await preview.arrayBuffer()));
}
const xlsx = await SpreadsheetFile.exportXlsx(wb); await xlsx.save(outputPath);
const outBytes = await fs.readFile(outputPath);
const canonicalBytes = await fs.readFile(canonical);
const manifest = { schema_version:"workbook-result-v1", source_id:payload.source_id, source_sha256:payload.source_sha256, pipeline:"workbook", canonical_path:canonical, canonical_sha256:sha(canonicalBytes), output_path:outputPath, output_sha256:sha(outBytes), output_size_bytes:outBytes.length, required_sheets:requiredSheets, required_tables:requiredTables, protected_pursuit_management_sha256:protectedBeforeHash, writes:payload.writes || [] };
await fs.writeFile(manifestPath,JSON.stringify(manifest,null,2)+"\n","utf8");
// artifact-tool can fault during Node shutdown on very large imported workbooks;
// the export and manifest are complete at this point, so terminate cleanly.
process.exit(0);
