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

    def all_label_names(self) -> list[str]:
        return list(self.type_labels.values()) + list(self.priority_labels.values())


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
    )
