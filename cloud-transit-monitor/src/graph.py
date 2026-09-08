from __future__ import annotations

import json
from pathlib import Path

import httpx
from azure.identity import DefaultAzureCredential


class GraphConflictError(RuntimeError):
    pass


class GraphClient:
    def __init__(self, drive_id: str, item_id: str) -> None:
        self.drive_id = drive_id
        self.item_id = item_id
        self.credential = DefaultAzureCredential()
        self.base = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}"

    def _headers(self) -> dict[str, str]:
        token = self.credential.get_token("https://graph.microsoft.com/.default")
        return {"Authorization": f"Bearer {token.token}"}

    def metadata(self) -> dict:
        with httpx.Client(timeout=60) as client:
            response = client.get(self.base, headers=self._headers())
            response.raise_for_status()
            return response.json()

    def download(self, destination: Path) -> str:
        metadata = self.metadata()
        with httpx.Client(timeout=180, follow_redirects=True) as client:
            response = client.get(f"{self.base}/content", headers=self._headers())
            response.raise_for_status()
            destination.write_bytes(response.content)
        return metadata["eTag"]

    def upload(self, source: Path, expected_etag: str) -> dict:
        current = self.metadata()
        if current.get("eTag") != expected_etag:
            raise GraphConflictError(
                "Workbook changed after download; refusing to overwrite a newer human or automated edit."
            )
        headers = self._headers() | {
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "If-Match": expected_etag,
        }
        with httpx.Client(timeout=300) as client:
            response = client.put(f"{self.base}/content", headers=headers, content=source.read_bytes())
            response.raise_for_status()
            return response.json()

    def send_mail(self, sender: str, recipients: tuple[str, ...], subject: str, body: str) -> None:
        if not sender or not recipients:
            return
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": value}} for value in recipients],
            },
            "saveToSentItems": False,
        }
        with httpx.Client(timeout=60) as client:
            response = client.post(
                f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
                headers=self._headers() | {"Content-Type": "application/json"},
                content=json.dumps(payload),
            )
            response.raise_for_status()
