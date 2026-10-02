"""Laya wrapper. Two calls per email keep each call inside Laya's option-text
budget: the 10-way type choice, then needs_action + urgency."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from triage.config import Config


class Model(Protocol):
    def predict(self, state: str | dict, questions: dict, **kwargs) -> dict: ...


@dataclass(frozen=True)
class LayaResult:
    type: str | None
    type_conf: float
    top2: tuple[tuple[str, float], ...]
    needs_action: float | None
    urgency: float | None
    model: str
    priority: str | None = None  # set only in priority-choice mode
    priority_probs: tuple[tuple[str, float], ...] = ()
    signals: tuple[tuple[str, float], ...] = ()  # P(yes) per configured priority signal


def load_router() -> Model:
    # Imported here so host-side tests run without laya/torch installed.
    from laya import Router

    return Router(device="cpu")


class Classifier:
    def __init__(self, cfg: Config, model: Model):
        self.cfg = cfg
        self.model = model

    def _predict(self, state: str, questions: dict) -> dict:
        if self.cfg.head_max_len:
            return self.model.predict(state, questions, head_max_len=self.cfg.head_max_len)
        return self.model.predict(state, questions)

    def type_questions(self, exclude_types: frozenset[str] = frozenset()) -> dict:
        if self.cfg.type_groups is not None:
            return self._group_questions(exclude_types)
        criteria = {k: v for k, v in self.cfg.type_criteria.items() if k not in exclude_types}
        return {
            "type": {
                "type": "choice",
                "instructions": self.cfg.type_question,
                "criteria": criteria,
            }
        }

    def _group_questions(self, exclude_types: frozenset[str]) -> dict:
        groups, questions = {}, {}
        for name, (criteria, members) in self.cfg.type_groups.items():
            kept = [m for m in members if m not in exclude_types]
            if not kept:
                continue
            groups[name] = criteria
            if len(kept) > 1:
                questions[f"sub_{name}"] = {
                    "type": "choice",
                    "instructions": self.cfg.type_question,
                    "criteria": {m: self.cfg.type_criteria[m] for m in kept},
                }
        questions["group"] = {"type": "choice", "instructions": "Which area of life is this email about?",
                              "criteria": groups}
        return questions

    def _type_distribution(self, answers: dict, exclude_types: frozenset[str]) -> dict[str, float]:
        """Flat type -> probability, from either the flat type answer or the group hierarchy."""
        if "type" in answers:
            return {k: float(v) for k, v in answers["type"]["probabilities"].items()}
        dist = {}
        for group, p_group in answers["group"]["probabilities"].items():
            members = [m for m in self.cfg.type_groups[group][1] if m not in exclude_types]
            sub = answers.get(f"sub_{group}")
            for m in members:
                p_sub = float(sub["probabilities"][m]) if sub else 1.0
                dist[m] = float(p_group) * p_sub
        return dist

    def action_questions(self) -> dict:
        if self.cfg.priority_signals is not None:
            return {f"sig_{k}": {"type": "noul", "instructions": q} for k, q in self.cfg.priority_signals.items()}
        if self.cfg.priority_criteria is not None:
            return {
                "priority": {
                    "type": "choice",
                    "instructions": self.cfg.priority_question,
                    "criteria": dict(self.cfg.priority_criteria),
                }
            }
        return {
            "needs_action": {"type": "noul", "instructions": self.cfg.needs_action_question},
            "urgency": {
                "type": "score",
                "instructions": self.cfg.urgency_question,
                "criteria": list(self.cfg.urgency_levels),
            },
        }

    def classify(self, state: str, skip_type: bool = False,
                 skip_action_types: frozenset[str] = frozenset(),
                 exclude_types: frozenset[str] = frozenset(),
                 known_type: str | None = None) -> LayaResult:
        if skip_type:
            return LayaResult(type=None, type_conf=1.0, top2=(), needs_action=None,
                              urgency=None, model="rules")
        if known_type is not None:  # type given by the caller (dev tuning): only the action question
            if known_type in skip_action_types:
                return LayaResult(known_type, 1.0, ((known_type, 1.0),), None, None, "given")
            return self._with_action(state, known_type, 1.0, ((known_type, 1.0),), "given")
        result = self._predict(state, self.type_questions(exclude_types))
        dist = self._type_distribution(result["answers"], exclude_types)
        ranked = sorted(dist.items(), key=lambda kv: -kv[1])
        type_, type_conf = ranked[0][0], float(ranked[0][1])
        top2 = tuple((label, float(p)) for label, p in ranked[:2])
        model_name = result.get("routing", {}).get("model", "unknown")
        if type_ in skip_action_types:
            return LayaResult(type_, type_conf, top2, None, None, model_name)
        return self._with_action(state, type_, type_conf, top2, None)

    def _with_action(self, state: str, type_: str, type_conf: float,
                     top2: tuple[tuple[str, float], ...], model_name: str | None) -> LayaResult:
        result = self._predict(state, self.action_questions())
        answers = result["answers"]
        model_name = model_name or result.get("routing", {}).get("model", "unknown")
        if self.cfg.priority_signals is not None:
            signals = tuple((qid[4:], float(a["noul"])) for qid, a in answers.items() if qid.startswith("sig_"))
            return LayaResult(type_, type_conf, top2, None, None, model_name, signals=signals)
        if "priority" in answers:
            probs = tuple((k, float(v)) for k, v in answers["priority"].get("probabilities", {}).items())
            return LayaResult(type_, type_conf, top2, None, None, model_name,
                              priority=answers["priority"]["choice"], priority_probs=probs)
        return LayaResult(
            type=type_,
            type_conf=type_conf,
            top2=top2,
            needs_action=float(answers["needs_action"]["noul"]),
            urgency=float(answers["urgency"]["score"]),
            model=model_name,
        )
