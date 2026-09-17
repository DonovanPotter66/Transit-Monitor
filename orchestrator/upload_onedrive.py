"""Upload one verified workbook to an existing OneDrive/SharePoint item.

Credentials are read only from environment variables.  The client uses the
Microsoft Graph client-credentials flow and verifies the uploaded bytes by
downloading the item again before writing an audit record.
"""
from __future__ import annotations
import hashlib, json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from validate_xlsx_package import validate as validate_xlsx_package


class GraphRequestError(RuntimeError):
    def __init__(self, code: int, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"Graph request failed ({code}): {detail}")

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
        raise GraphRequestError(exc.code, detail) from exc

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def upload_with_lock_retries(url: str, workbook: Path, headers: dict[str, str], retries: int) -> None:
    for attempt in range(retries):
        try:
            request("PUT", url, data=workbook.read_bytes(), headers=headers)
            return
        except GraphRequestError as exc:
            if exc.code != 423 or attempt == retries - 1:
                raise
            delay = min(300, 15 * (attempt + 1))
            print(f"OneDrive item is locked; retrying upload in {delay}s (attempt {attempt + 2}/{retries})", file=sys.stderr)
            time.sleep(delay)


def locked_fallback_upload(workbook: Path, drive: str, item: str, headers: dict[str, str], token: str) -> tuple[str, str]:
    metadata = json.loads(request(
        "GET",
        f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{item}?$select=name,parentReference",
        headers={"Authorization": f"Bearer {token}"},
    ))
    parent_id = metadata.get("parentReference", {}).get("id")
    if not parent_id:
        raise RuntimeError("Cannot create fallback workbook because OneDrive parent folder was not returned.")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    source_name = metadata.get("name") or workbook.name
    fallback_name = f"{Path(source_name).stem} - locked fallback {timestamp}{workbook.suffix}"
    fallback_url = f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{parent_id}:/{quote(fallback_name)}:/content"
    request("PUT", fallback_url, data=workbook.read_bytes(), headers=headers)
    return fallback_name, f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{parent_id}:/{quote(fallback_name)}:/content"

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
    upload_url = f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{item}/content"
    published_item = item
    published_name = workbook.name
    status = "published_verified"
    verify_url = upload_url
    try:
        upload_with_lock_retries(upload_url, workbook, headers, int_env("ONEDRIVE_LOCK_RETRIES", 10))
    except GraphRequestError as exc:
        if exc.code != 423 or not bool_env("ONEDRIVE_LOCK_FALLBACK", True):
            raise
        print("OneDrive item remained locked; publishing verified fallback copy in the same folder.", file=sys.stderr)
        published_name, verify_url = locked_fallback_upload(workbook, drive, item, headers, token)
        published_item = ""
        status = "published_verified_fallback"
    downloaded = request("GET", verify_url, headers={"Authorization": f"Bearer {token}"})
    actual = hashlib.sha256(downloaded).hexdigest()
    if actual != expected:
        raise RuntimeError(f"OneDrive verification hash mismatch: expected {expected}, got {actual}")
    audit = Path(os.environ.get("DISTRIBUTION_AUDIT", "orchestrator/state/github-distribution-audit.jsonl"))
    audit.parent.mkdir(parents=True, exist_ok=True)
    with audit.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"status":status,"workbook":str(workbook),"drive_id":drive,"item_id":published_item,"name":published_name,"sha256":expected,"published_at":datetime.now(timezone.utc).isoformat()}, sort_keys=True) + "\n")
    print(json.dumps({"status":status,"sha256":expected,"audit":str(audit),"name":published_name}))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
