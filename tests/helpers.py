"""Builders and fakes shared by tests."""
import base64
from dataclasses import replace

from triage.classifier import LayaResult
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

    def predict(self, state, questions, **kwargs):
        self.calls.append((state, questions))
        self.kwargs = kwargs
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


def make_laya(**overrides) -> LayaResult:
    base = LayaResult(type="finance", type_conf=0.9, top2=(("finance", 0.9),),
                      needs_action=0.1, urgency=0.5, model="english")
    return replace(base, **overrides)


class FakeGmail:
    """In-memory GmailClient. A message value that is an Exception is raised by get()."""

    def __init__(self, messages, list_once=False):
        self.messages = messages
        self.list_once = list_once
        self.queries = []
        self.added = []
        self.label_ids = {}

    def list_ids(self, query, limit=None):
        self.queries.append(query)
        if self.list_once and len(self.queries) > 1:
            return []
        ids = list(self.messages)
        return ids[:limit] if limit else ids

    def get(self, msg_id):
        msg = self.messages[msg_id]
        if isinstance(msg, Exception):
            raise msg
        return msg

    def ensure_labels(self, names):
        for name in names:
            self.label_ids.setdefault(name, f"id:{name}")
        return {name: self.label_ids[name] for name in names}

    def add_labels(self, msg_ids, label_ids):
        self.added.append((sorted(msg_ids), sorted(label_ids)))


def bank_msg(msg_id):
    return make_msg(
        msg_id,
        headers={"From": "HDFC Bank <alerts@hdfcbank.net>", "Subject": "Statement",
                 "Authentication-Results": "mx.google.com; dkim=pass; spf=pass; dmarc=pass"},
        plain="Your statement is ready",
    )


class FakePriorityModel(FakeModel):
    """FakeModel that also answers a priority choice question."""

    def __init__(self, priority="none", **kwargs):
        super().__init__(**kwargs)
        self.priority = priority

    def predict(self, state, questions, **kwargs):
        result = super().predict(state, questions, **kwargs)
        if "priority" in questions:
            result["answers"]["priority"] = {"choice": self.priority, "answer_confidence": 0.9,
                                             "probabilities": {self.priority: 0.9}}
        return result
