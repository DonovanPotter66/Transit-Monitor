import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(process.argv[2]));
for (const name of ["Summary","Opportunity Register","High Priority Pursuit List","Source Health","Dashboard","Pursuit Management"]) {
  const s=wb.worksheets.getItem(name);
  for (const t of s.tables.items) console.log(JSON.stringify({sheet:name,name:t.name,range:t.getRange().address,headers:t.getHeaderRowRange().values}));
}
