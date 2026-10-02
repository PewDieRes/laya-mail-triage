"""Builders and fakes shared by tests."""
import base64
from dataclasses import replace

from triage.extract import Auth, Features


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def make_msg(msg_id="m1", headers=None, plain=None, html=None, label_ids=("INBOX",)):
    """A Gmail API users.messages.get(format="full") response."""
    parts = []
    if plain is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": b64(plain)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html)}})
    return {
        "id": msg_id,
        "threadId": f"t-{msg_id}",
        "internalDate": "1790000000000",
        "labelIds": list(label_ids),
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": k, "value": v} for k, v in (headers or {}).items()],
            "body": {},
            "parts": parts,
        },
    }


def make_features(**overrides) -> Features:
    base = Features(
        msg_id="m1",
        thread_id="t1",
        internal_date=1790000000000,
        from_name="Alice",
        from_email="alice@example.com",
        from_domain="example.com",
        reply_to_domain=None,
        subject="Hello",
        body="Hi there",
        is_bulk=False,
        auth=Auth(spf="pass", dkim="pass", dmarc="pass"),
        label_ids=("INBOX",),
    )
    return replace(base, **overrides)
