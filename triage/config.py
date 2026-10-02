"""Load triage settings from config/config.yaml and config/vip.txt."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Thresholds:
    type_conf: float
    needs_action: float
    urgency_act_now: float
    urgency_this_week: float


@dataclass(frozen=True)
class Config:
    interval_minutes: int
    type_question: str
    type_criteria: dict[str, str]
    type_labels: dict[str, str]
    no_priority_types: frozenset[str]
    needs_action_question: str
    urgency_question: str
    urgency_levels: tuple[str, ...]
    priority_labels: dict[str, str]
    thresholds: Thresholds
    vip: frozenset[str]
    # Optional: when set, priority is one Laya choice (act_now/this_week/none) instead of
    # the needs_action + urgency scores and their thresholds.
    priority_question: str | None = None
    priority_criteria: dict[str, str] | None = None
    # Optional hierarchy: group -> (criteria, member types). When set, Laya picks a group and,
    # in the same call, a member within each multi-member group.
    type_groups: dict[str, tuple[str, tuple[str, ...]]] | None = None
    # Optional concrete yes/no priority signals: name -> question, plus which signals
    # (and P(yes) threshold) make an email act_now or this_week.
    priority_signals: dict[str, str] | None = None
    priority_rules: dict[str, tuple[tuple[str, ...], float]] | None = None
    head_max_len: int | None = None  # Laya option-text token budget per call (model default 192)
    body_limit: int | None = None
    # Stricter confidence for specific Laya-chosen types (e.g. suspicious hides real mail).
    type_conf_by_type: dict[str, float] | None = None
    priority_enabled: bool = True  # False: no Laya priority question; only VIP senders get act_now
    state_format: str = "text"  # "text" (one string) or "dict" (Laya's subject/from/body fields)  # characters of body passed to Laya (default: extract.BODY_LIMIT)

    def all_label_names(self) -> list[str]:
        priority = [name for key, name in self.priority_labels.items()
                    if self.priority_enabled or key != "this_week"]
        return list(self.type_labels.values()) + priority


def load_vip(path: Path) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    entries = set()
    for line in path.read_text().splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            entries.add(line)
    return frozenset(entries)


def load_config(config_dir: Path) -> Config:
    raw = yaml.safe_load((config_dir / "config.yaml").read_text())
    types = raw["types"]
    if "suspicious" not in types:
        raise ValueError("types must include 'suspicious' (used by the auth rules)")
    unknown = set(raw["no_priority_types"]) - set(types)
    if unknown:
        raise ValueError(f"no_priority_types has unknown types: {sorted(unknown)}")
    return Config(
        interval_minutes=int(raw["interval_minutes"]),
        type_question=raw["type_question"],
        type_criteria={key: spec["criteria"] for key, spec in types.items()},
        type_labels={key: spec["label"] for key, spec in types.items()},
        no_priority_types=frozenset(raw["no_priority_types"]),
        needs_action_question=raw["needs_action_question"],
        urgency_question=raw["urgency_question"],
        urgency_levels=tuple(raw["urgency_levels"]),
        priority_labels=dict(raw["priority_labels"]),
        thresholds=Thresholds(**raw["thresholds"]),
        vip=load_vip(config_dir / "vip.txt"),
        priority_question=raw.get("priority_question"),
        priority_criteria=_priority_criteria(raw.get("priority_criteria")),
        type_groups=_type_groups(raw.get("type_groups"), set(types)),
        priority_signals=raw.get("priority_signals"),
        priority_rules=_priority_rules(raw.get("priority_rules"), raw.get("priority_signals")),
        head_max_len=raw.get("head_max_len"),
        body_limit=raw.get("body_limit"),
        state_format=_state_format(raw.get("state_format", "text")),
        priority_enabled=bool(raw.get("priority_enabled", True)),
        type_conf_by_type=_type_conf_by_type(raw.get("type_conf_by_type"), set(types)),
    )


def _priority_criteria(raw: dict | None) -> dict[str, str] | None:
    if raw is None:
        return None
    if set(raw) != {"act_now", "this_week", "none"}:
        raise ValueError("priority_criteria must have exactly act_now, this_week and none")
    return dict(raw)


def _type_groups(raw: dict | None, types: set[str]) -> dict[str, tuple[str, tuple[str, ...]]] | None:
    if raw is None:
        return None
    groups = {name: (spec["criteria"], tuple(spec["members"])) for name, spec in raw.items()}
    members = [m for _, ms in groups.values() for m in ms]
    if sorted(members) != sorted(types):
        raise ValueError("type_groups members must list every type exactly once")
    return groups


def _priority_rules(raw: dict | None, signals: dict | None) -> dict[str, tuple[tuple[str, ...], float]] | None:
    if raw is None:
        return None
    if not signals or set(raw) != {"act_now", "this_week"}:
        raise ValueError("priority_rules needs priority_signals and exactly act_now and this_week")
    rules = {k: (tuple(v["signals"]), float(v["threshold"])) for k, v in raw.items()}
    unknown = {s for names, _ in rules.values() for s in names} - set(signals)
    if unknown:
        raise ValueError(f"priority_rules uses unknown signals: {sorted(unknown)}")
    return rules


def _state_format(value: str) -> str:
    if value not in ("text", "dict"):
        raise ValueError("state_format must be 'text' or 'dict'")
    return value


def _type_conf_by_type(raw: dict | None, types: set[str]) -> dict[str, float] | None:
    if raw is None:
        return None
    unknown = set(raw) - types
    if unknown:
        raise ValueError(f"type_conf_by_type has unknown types: {sorted(unknown)}")
    return {k: float(v) for k, v in raw.items()}
