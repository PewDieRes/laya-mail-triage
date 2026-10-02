"""Turn a Gmail API message (format="full") into Features for rules and Laya."""
from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.utils import parseaddr

from bs4 import BeautifulSoup

BODY_LIMIT = 1500
_AUTH_RE = re.compile(r"^\s*(spf|dkim|dmarc)=([a-z]+)", re.IGNORECASE)
_MAILFROM_RE = re.compile(r"smtp\.mailfrom=([^\s;]+)", re.IGNORECASE)
_DKIM_DOMAIN_RE = re.compile(r"header\.(?:i|d)=([^\s;]+)", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
# CSS rules ("selector { prop: value; }") that some senders leak into the text part.
_CSS_RULE_RE = re.compile(r"[^{}]{0,300}?\{[^{}]*\}")
_CSS_AT_RE = re.compile(r"@(media|font-face|import|charset)[^{;]*[;{]", re.IGNORECASE)
TRUSTED_AUTHSERV = "mx.google.com"
STUB_BODY_MIN = 40
_WROTE_RE = re.compile(r"^\s*On .+wrote:\s*$", re.MULTILINE)
_FAIL_RESULTS = {"fail", "softfail"}
_BULK_PRECEDENCE = {"bulk", "list", "junk"}


def _aligned(a: str, b: str) -> bool:
    a, b = a.lower().strip("."), b.lower().strip(".")
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


@dataclass(frozen=True)
class Auth:
    spf: str = "none"
    dkim: str = "none"
    dmarc: str = "none"
    spf_domain: str = ""
    dkim_domains: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        """DMARC pass only; alignment needs a From domain, see verified_for."""
        return self.dmarc == "pass"

    def verified_for(self, from_domain: str) -> bool:
        if self.dmarc == "pass":
            return True
        if self.spf == "pass" and _aligned(self.spf_domain, from_domain):
            return True
        return any(_aligned(d, from_domain) for d in self.dkim_domains)


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

    @property
    def verified(self) -> bool:
        return self.auth.verified_for(self.from_domain)


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


def _charset(part: dict) -> str:
    for header in part.get("headers") or []:
        if header.get("name", "").lower() == "content-type":
            match = re.search(r"charset\s*=\s*\"?([^\s\";]+)", header.get("value", ""), re.IGNORECASE)
            if match:
                return match.group(1)
    return "utf-8"


def _decode_part(part: dict) -> str:
    data = part["body"]["data"]
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    try:
        return raw.decode(_charset(part), errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _find_part(payload: dict, mime: str) -> str | None:
    if payload.get("mimeType") == mime and payload.get("body", {}).get("data"):
        return _decode_part(payload)
    for part in payload.get("parts") or []:
        found = _find_part(part, mime)
        if found is not None:
            return found
    return None


def _strip_invisible(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf" and ch != "\u034f")


def clean_body(text: str) -> str:
    text = _URL_RE.sub(" ", _strip_invisible(text))
    text = _CSS_RULE_RE.sub(" ", _CSS_AT_RE.sub(" ", text)).replace("{", " ").replace("}", " ")
    match = _WROTE_RE.search(text)
    if match:
        text = text[: match.start()]
    lines = [line for line in text.splitlines() if not line.lstrip().startswith(">")]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()[:BODY_LIMIT]


def _html_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "blockquote"]):
        tag.decompose()
    return clean_body(soup.get_text(" "))


def _body(payload: dict) -> str:
    plain = _find_part(payload, "text/plain")
    cleaned = clean_body(plain) if plain is not None else ""
    if plain is not None and len(cleaned) >= STUB_BODY_MIN:
        return cleaned
    html = _find_part(payload, "text/html")
    if html is None:
        return cleaned
    from_html = _html_text(html)
    return from_html if len(from_html) > len(cleaned) else cleaned


def parse_auth(values: list[str]) -> Auth:
    found: dict[str, str] = {}
    spf_domain = ""
    dkim_results: list[str] = []
    dkim_domains: list[str] = []
    trusted = [v for v in values if v.split(";", 1)[0].strip().lower() == TRUSTED_AUTHSERV]
    # Gmail prepends its own header, so only the topmost one is trustworthy.
    for value in trusted[:1]:
        for segment in value.split(";")[1:]:
            match = _AUTH_RE.match(segment)
            if not match:
                continue
            mech, raw = match.group(1).lower(), match.group(2).lower()
            result = "pass" if raw == "pass" else "fail" if raw in _FAIL_RESULTS else "none"
            if mech == "dkim":
                dkim_results.append(result)
                domain = _DKIM_DOMAIN_RE.search(segment)
                if result == "pass" and domain:
                    dkim_domains.append(domain.group(1).lstrip("@").strip("<>\"'").lower())
                continue
            if mech in found:
                continue
            found[mech] = result
            if mech == "spf":
                sender = _MAILFROM_RE.search(segment)
                if sender:
                    spf_domain = sender.group(1).strip("<>\"'").rsplit("@", 1)[-1].lower()
    if "pass" in dkim_results:
        found["dkim"] = "pass"
    elif "fail" in dkim_results:
        found["dkim"] = "fail"
    elif dkim_results:
        found["dkim"] = "none"
    return Auth(**found, spf_domain=spf_domain, dkim_domains=tuple(dkim_domains))


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


def _one_line(text: str) -> str:
    """Collapse line breaks and invisible format chars so a header can't forge extra state lines."""
    text = "".join(" " if ch in "\r\n" or unicodedata.category(ch) == "Cf" else ch for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def to_state(f: Features, body_limit: int | None = None) -> str:
    name = _one_line(f.from_name)
    email = _one_line(f.from_email)
    sender = f"{name} <{email}>" if name else email
    return (
        f"From: {sender}\n"
        f"Subject: {_one_line(f.subject)}\n"
        f"Sender verified: {_yes_no(f.verified)}\n"
        f"Bulk sender: {_yes_no(f.is_bulk)}\n"
        f"Body: {f.body if body_limit is None else f.body[:body_limit]}"
    )
