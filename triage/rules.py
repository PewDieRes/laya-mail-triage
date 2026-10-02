"""Deterministic rules that run before Laya. They decide facts Laya cannot see
from text alone: sender authentication and the owner's VIP list. The VIP boost
applies only to verified senders; unverified VIP mail is recorded as
"vip_unverified" and classified normally."""
from __future__ import annotations

from dataclasses import dataclass

from triage.extract import Features

SUSPICIOUS = "suspicious"


@dataclass(frozen=True)
class RuleHits:
    forced_type: str | None = None
    vip: bool = False
    names: tuple[str, ...] = ()


def is_vip(email: str, vip: frozenset[str]) -> bool:
    email = email.lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    return email in vip or (bool(domain) and f"@{domain}" in vip)


def apply_rules(f: Features, vip: frozenset[str]) -> RuleHits:
    names: list[str] = []
    forced_type = None
    if f.auth.dmarc == "fail" or (f.auth.spf == "fail" and f.auth.dkim == "fail"):
        names.append("auth_fail")
        forced_type = SUSPICIOUS
    if f.reply_to_domain and f.reply_to_domain != f.from_domain and not f.auth.verified:
        names.append("reply_to_mismatch")
        forced_type = SUSPICIOUS
    vip_hit = False
    if is_vip(f.from_email, vip):
        vip_hit = f.auth.verified
        names.append("vip" if vip_hit else "vip_unverified")
    return RuleHits(forced_type=forced_type, vip=vip_hit, names=tuple(names))
