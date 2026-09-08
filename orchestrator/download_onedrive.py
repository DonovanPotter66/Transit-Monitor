"""Download the canonical workbook for a cloud run using Graph app credentials."""
from __future__ import annotations
import os, sys
from pathlib import Path
from upload_onedrive import required, request
from urllib.parse import urlencode
import json

def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python orchestrator/download_onedrive.py <destination>", file=sys.stderr)
        return 2
    destination = Path(sys.argv[1]).resolve()
    tenant, client, secret = required("GRAPH_TENANT_ID"), required("GRAPH_CLIENT_ID"), required("GRAPH_CLIENT_SECRET")
    drive, item = required("ONEDRIVE_DRIVE_ID"), required("ONEDRIVE_ITEM_ID")
    body = urlencode({"client_id": client, "client_secret": secret, "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}).encode()
    token = json.loads(request("POST", f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", data=body, headers={"Content-Type":"application/x-www-form-urlencoded"}))["access_token"]
    data = request("GET", f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{item}/content", headers={"Authorization":f"Bearer {token}"})
    if not data:
        raise RuntimeError("Graph returned an empty workbook")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    print(f"downloaded={destination} bytes={len(data)}")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
