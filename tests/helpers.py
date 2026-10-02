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


class FakeModel:
    """Stands in for laya.Router: fixed answers, records every call."""

    def __init__(self, type_="finance", conf=0.9, needs_action=0.1, urgency=0.5, probs=None):
        self.type_ = type_
        self.conf = conf
        self.needs_action = needs_action
        self.urgency = urgency
        self.probs = probs
        self.calls = []

    def predict(self, state, questions):
        self.calls.append((state, questions))
        answers = {}
        if "type" in questions:
            answers["type"] = {
                "choice": self.type_,
                "answer_confidence": self.conf,
                "probabilities": self.probs or {self.type_: self.conf},
            }
        if "needs_action" in questions:
            answers["needs_action"] = {"noul": self.needs_action}
        if "urgency" in questions:
            answers["urgency"] = {"score": self.urgency}
        return {"answers": answers, "routing": {"model": "english"}}
