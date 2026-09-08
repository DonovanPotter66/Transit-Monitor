"""Fail-closed structural validator for cloud-published xlsx files."""
from __future__ import annotations

import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"


def validate(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("workbook is missing or empty")
    with zipfile.ZipFile(path) as package:
        if package.testzip() is not None:
            raise ValueError("workbook contains a corrupt ZIP member")
        names = set(package.namelist())
        required = {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
        missing = required - names
        if missing:
            raise ValueError(f"workbook missing package parts: {sorted(missing)}")
        types = package.read("[Content_Types].xml").decode("utf-8-sig")
        if 'PartName="/xl/workbook.xml"' not in types or TYPE not in types:
            raise ValueError("workbook lacks explicit OOXML content type for xl/workbook.xml")
        for name in names:
            if name.endswith((".xml", ".rels")):
                ET.fromstring(package.read(name))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python orchestrator/validate_xlsx_package.py <workbook>")
    try:
        validate(Path(sys.argv[1]))
    except Exception as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"VALID: {sys.argv[1]}")
