"""Combine Laya output and rule hits into the labels to apply."""
from __future__ import annotations

from dataclasses import dataclass

from triage.classifier import LayaResult
from triage.config import Config
from triage.rules import RuleHits


@dataclass(frozen=True)
class Decision:
    type: str
    priority: str | None
    unsure: bool

    def label_names(self, cfg: Config) -> list[str]:
        if self.unsure:  # never show a type guess Laya is not confident about
            return [cfg.priority_labels["unsure"]] + (
                [cfg.priority_labels[self.priority]] if self.priority else [])
        names = [cfg.type_labels[self.type]]
        if self.priority:
            names.append(cfg.priority_labels[self.priority])
        return names


def decide(laya: LayaResult, hits: RuleHits, cfg: Config) -> Decision:
    t = cfg.thresholds
    type_ = hits.forced_type or laya.type
    if type_ is None:
        raise ValueError("no type from rules or Laya")
    needed = (cfg.type_conf_by_type or {}).get(type_, t.type_conf)
    unsure = hits.forced_type is None and laya.type_conf < needed
    priority = None
    if type_ not in cfg.no_priority_types and cfg.priority_rules and laya.signals:
        sig = dict(laya.signals)
        for level in ("act_now", "this_week"):
            names, threshold = cfg.priority_rules[level]
            if any(sig.get(n, 0.0) >= threshold for n in names):
                priority = level
                break
        if hits.vip:
            priority = "act_now"
    elif type_ not in cfg.no_priority_types and laya.priority is not None:
        priority = "act_now" if hits.vip else (None if laya.priority == "none" else laya.priority)
    elif type_ not in cfg.no_priority_types:
        scored = laya.needs_action is not None and laya.urgency is not None
        needs_action = scored and laya.needs_action >= t.needs_action
        if hits.vip or (needs_action and laya.urgency >= t.urgency_act_now):
            priority = "act_now"
        elif needs_action and laya.urgency >= t.urgency_this_week:
            priority = "this_week"
    return Decision(type=type_, priority=priority, unsure=unsure)
