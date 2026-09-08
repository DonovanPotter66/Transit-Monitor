import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
const path = process.argv[2];
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(path));
console.log((await wb.inspect({kind:"workbook,sheet,table",maxChars:12000,tableMaxRows:3,tableMaxCols:10,tableMaxCellChars:80})).ndjson);
for (const name of ["Summary","Opportunity Register","High Priority Pursuit List","Source Health","Pursuit Management","Dashboard"]) {
  const sheet = wb.worksheets.getItem(name);
  console.log(`---${name}---`);
  console.log((await wb.inspect({kind:"region",sheetId:name,range:"A1:Z12",maxChars:6000,tableMaxRows:12,tableMaxCols:26,tableMaxCellChars:100})).ndjson);
  console.log("FORMULAS", JSON.stringify(sheet.getRange("A1:Z12").formulas));
}
