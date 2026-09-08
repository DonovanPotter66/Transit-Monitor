"""Repair the minimal OPC content-type defect in an xlsx package.

The generated workbook had no explicit Override for /xl/workbook.xml and
declared every XML file as the workbook main part. Excel for the web rejects
that package even though the ZIP members themselves are readable.
"""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

WORKBOOK_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"


def repair(source: Path, destination: Path) -> bool:
    with zipfile.ZipFile(source, "r") as zin:
        content = zin.read("[Content_Types].xml")
        text = content.decode("utf-8-sig")
        marker = 'PartName="/xl/workbook.xml"'
        changed = False
        if marker not in text:
            insertion = (
                f'<Override PartName="/xl/workbook.xml" '
                f'ContentType="{WORKBOOK_TYPE}" />'
            )
            text = text.replace("</Types>", insertion + "</Types>", 1)
            changed = True
        # The old package used the workbook type as the default for all XML.
        # Use the OPC-safe generic XML default once workbook.xml is explicit.
        old_default = '<Default Extension="xml" ContentType="' + WORKBOOK_TYPE + '" />'
        if old_default in text:
            text = text.replace(old_default, '<Default Extension="xml" ContentType="application/xml" />', 1)
            changed = True
        if not changed:
            destination.write_bytes(source.read_bytes())
            return False
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = text.encode("utf-8") if item.filename == "[Content_Types].xml" else zin.read(item.filename)
                zout.writestr(item, data)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if not args.source.is_file():
        raise SystemExit(f"missing source workbook: {args.source}")
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    changed = repair(args.source, args.destination)
    print(f"repaired={changed} output={args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
