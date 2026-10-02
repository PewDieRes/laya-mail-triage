"""Turn a Gmail API message (format="full") into Features for rules and Laya."""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.utils import parseaddr

from bs4 import BeautifulSoup

BODY_LIMIT = 1500
_AUTH_RE = re.compile(r"\b(spf|dkim|dmarc)=([a-z]+)", re.IGNORECASE)
_WROTE_RE = re.compile(r"^\s*On .+wrote:\s*$", re.MULTILINE)
_FAIL_RESULTS = {"fail", "softfail"}
_BULK_PRECEDENCE = {"bulk", "list", "junk"}


@dataclass(frozen=True)
class Auth:
    spf: str = "none"
    dkim: str = "none"
    dmarc: str = "none"

    @property
    def verified(self) -> bool:
        return self.dmarc == "pass" or (self.spf == "pass" and self.dkim == "pass")


@dataclass(frozen=True)
class Features:
    msg_id: str
    thread_id: str
    internal_date: int
    from_name: str
    from_email: str
    from_domain: str
    reply_to_domain: str | None
    subject: str
    body: str
    is_bulk: bool
    auth: Auth
    label_ids: tuple[str, ...]


def _headers(payload: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for header in payload.get("headers", []):
        out.setdefault(header["name"].lower(), []).append(header["value"])
    return out


def _decode_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _decode_data(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _find_part(payload: dict, mime: str) -> str | None:
    if payload.get("mimeType") == mime and payload.get("body", {}).get("data"):
        return _decode_data(payload["body"]["data"])
    for part in payload.get("parts") or []:
        found = _find_part(part, mime)
        if found is not None:
            return found
    return None


def clean_body(text: str) -> str:
    match = _WROTE_RE.search(text)
    if match:
        text = text[: match.start()]
    lines = [line for line in text.splitlines() if not line.lstrip().startswith(">")]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()[:BODY_LIMIT]


def _body(payload: dict) -> str:
    plain = _find_part(payload, "text/plain")
    if plain is not None:
        return clean_body(plain)
    html = _find_part(payload, "text/html")
    if html is None:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return clean_body(soup.get_text("\n"))


def parse_auth(values: list[str]) -> Auth:
    found: dict[str, str] = {}
    for value in values:
        for mech, result in _AUTH_RE.findall(value):
            mech, result = mech.lower(), result.lower()
            if mech in found:
                continue
            if result == "pass":
                found[mech] = "pass"
            elif result in _FAIL_RESULTS:
                found[mech] = "fail"
            else:
                found[mech] = "none"
    return Auth(**found)


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def parse_message(msg: dict) -> Features:
    payload = msg.get("payload", {})
    headers = _headers(payload)

    def first(name: str) -> str:
        return headers.get(name, [""])[0]

    from_name, from_email = parseaddr(first("from"))
    _, reply_to = parseaddr(first("reply-to"))
    precedence = first("precedence").strip().lower()
    return Features(
        msg_id=msg["id"],
        thread_id=msg.get("threadId", ""),
        internal_date=int(msg.get("internalDate", 0)),
        from_name=_decode_header(from_name),
        from_email=from_email.lower(),
        from_domain=_domain(from_email),
        reply_to_domain=_domain(reply_to) or None,
        subject=_decode_header(first("subject")),
        body=_body(payload),
        is_bulk="list-unsubscribe" in headers or precedence in _BULK_PRECEDENCE,
        auth=parse_auth(headers.get("authentication-results", [])),
        label_ids=tuple(msg.get("labelIds", [])),
    )


def _yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


def to_state(f: Features) -> str:
    sender = f"{f.from_name} <{f.from_email}>" if f.from_name else f.from_email
    return (
        f"From: {sender}\n"
        f"Subject: {f.subject}\n"
        f"Sender verified: {_yes_no(f.auth.verified)}\n"
        f"Bulk sender: {_yes_no(f.is_bulk)}\n"
        f"Body: {f.body}"
    )
