"""Upload one verified workbook to an existing OneDrive/SharePoint item.

Credentials are read only from environment variables.  The client uses the
Microsoft Graph client-credentials flow and verifies the uploaded bytes by
downloading the item again before writing an audit record.
"""
from __future__ import annotations
import hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from validate_xlsx_package import validate as validate_xlsx_package

def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value

def request(method: str, url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None) -> bytes:
    req = Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urlopen(req, timeout=60) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[-1000:]
        raise RuntimeError(f"Graph request failed ({exc.code}): {detail}") from exc

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python orchestrator/upload_onedrive.py <verified-workbook>", file=sys.stderr)
        return 2
    workbook = Path(sys.argv[1]).resolve()
    if not workbook.is_file() or workbook.stat().st_size == 0:
        raise RuntimeError(f"workbook missing or empty: {workbook}")
    # Do not send a package that Excel for the web cannot interpret.
    validate_xlsx_package(workbook)
    tenant, client, secret = required("GRAPH_TENANT_ID"), required("GRAPH_CLIENT_ID"), required("GRAPH_CLIENT_SECRET")
    drive, item = required("ONEDRIVE_DRIVE_ID"), required("ONEDRIVE_ITEM_ID")
    token_body = urlencode({"client_id": client, "client_secret": secret, "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}).encode()
    token = json.loads(request("POST", f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", data=token_body, headers={"Content-Type": "application/x-www-form-urlencoded"}))["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    expected = sha(workbook)
    request("PUT", f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{item}/content", data=workbook.read_bytes(), headers=headers)
    downloaded = request("GET", f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{item}/content", headers={"Authorization": f"Bearer {token}"})
    actual = hashlib.sha256(downloaded).hexdigest()
    if actual != expected:
        raise RuntimeError(f"OneDrive verification hash mismatch: expected {expected}, got {actual}")
    audit = Path(os.environ.get("DISTRIBUTION_AUDIT", "orchestrator/state/github-distribution-audit.jsonl"))
    audit.parent.mkdir(parents=True, exist_ok=True)
    with audit.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"status":"published_verified","workbook":str(workbook),"drive_id":drive,"item_id":item,"sha256":expected,"published_at":datetime.now(timezone.utc).isoformat()}, sort_keys=True) + "\n")
    print(json.dumps({"status":"published_verified","sha256":expected,"audit":str(audit)}))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
