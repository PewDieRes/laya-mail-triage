"""Thin Gmail API wrapper. It only reads mail and adds labels."""
from __future__ import annotations

import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
RETRIES = 5
BATCH_LIMIT = 1000
PAGE_SIZE = 500


class AuthError(RuntimeError):
    """The saved token is missing or no longer works; re-run auth.py on the host."""


class ReadOnlyError(RuntimeError):
    """A write was attempted on a client opened in read-only (eval) mode."""


def load_credentials(token_path: Path):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not token_path.exists():
        raise AuthError(f"{token_path} not found - run `python auth.py` on the Mac first")
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            raise AuthError("token has no refresh token - re-run `python auth.py`")
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise AuthError(f"token refresh failed ({exc}) - re-run `python auth.py`") from exc
        token_path.write_text(creds.to_json())
        os.chmod(token_path, 0o600)
    return creds


class GmailClient:
    def __init__(self, service, read_only: bool):
        self.service = service
        self.read_only = read_only

    @classmethod
    def from_token(cls, token_path: Path, read_only: bool) -> "GmailClient":
        from googleapiclient.discovery import build

        creds = load_credentials(token_path)
        return cls(build("gmail", "v1", credentials=creds, cache_discovery=False), read_only)

    def _users(self):
        return self.service.users()

    def _guard(self) -> None:
        if self.read_only:
            raise ReadOnlyError("write attempted in read-only mode")

    def list_ids(self, query: str, limit: int | None = None) -> list[str]:
        ids: list[str] = []
        page_token = None
        while True:
            page_size = min(PAGE_SIZE, limit - len(ids)) if limit else PAGE_SIZE
            response = self._users().messages().list(
                userId="me", q=query, pageToken=page_token, maxResults=page_size
            ).execute(num_retries=RETRIES)
            ids.extend(m["id"] for m in response.get("messages", []))
            page_token = response.get("nextPageToken")
            if not page_token or (limit and len(ids) >= limit):
                return ids[:limit] if limit else ids

    def get(self, msg_id: str) -> dict:
        return self._users().messages().get(userId="me", id=msg_id, format="full").execute(
            num_retries=RETRIES
        )

    def get_label_ids(self, msg_id: str) -> list[str]:
        response = self._users().messages().get(userId="me", id=msg_id, format="minimal").execute(
            num_retries=RETRIES
        )
        return response.get("labelIds", [])

    def ensure_labels(self, names: list[str]) -> dict[str, str]:
        response = self._users().labels().list(userId="me").execute(num_retries=RETRIES)
        existing = {label["name"]: label["id"] for label in response.get("labels", [])}
        for name in names:
            parts = name.split("/")
            for depth in range(1, len(parts) + 1):
                path = "/".join(parts[:depth])
                if path not in existing:
                    existing[path] = self._create_label(path)
        return {name: existing[name] for name in names}

    def _create_label(self, name: str) -> str:
        self._guard()
        body = {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        return self._users().labels().create(userId="me", body=body).execute(num_retries=RETRIES)["id"]

    def add_labels(self, msg_ids: list[str], label_ids: list[str]) -> None:
        self._guard()
        for start in range(0, len(msg_ids), BATCH_LIMIT):
            body = {"ids": msg_ids[start:start + BATCH_LIMIT], "addLabelIds": label_ids}
            self._users().messages().batchModify(userId="me", body=body).execute(num_retries=RETRIES)
