"""Laya wrapper. Two calls per email keep each call inside Laya's option-text
budget: the 10-way type choice, then needs_action + urgency."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from triage.config import Config


class Model(Protocol):
    def predict(self, state: str, questions: dict) -> dict: ...


@dataclass(frozen=True)
class LayaResult:
    type: str | None
    type_conf: float
    top2: tuple[tuple[str, float], ...]
    needs_action: float | None
    urgency: float | None
    model: str


def load_router() -> Model:
    # Imported here so host-side tests run without laya/torch installed.
    from laya import Router

    return Router(device="cpu")


class Classifier:
    def __init__(self, cfg: Config, model: Model):
        self.cfg = cfg
        self.model = model

    def type_questions(self) -> dict:
        return {
            "type": {
                "type": "choice",
                "instructions": self.cfg.type_question,
                "criteria": dict(self.cfg.type_criteria),
            }
        }

    def action_questions(self) -> dict:
        return {
            "needs_action": {"type": "noul", "instructions": self.cfg.needs_action_question},
            "urgency": {
                "type": "score",
                "instructions": self.cfg.urgency_question,
                "criteria": list(self.cfg.urgency_levels),
            },
        }

    def classify(self, state: str, skip_type: bool = False,
                 skip_action_types: frozenset[str] = frozenset()) -> LayaResult:
        if skip_type:
            return LayaResult(type=None, type_conf=1.0, top2=(), needs_action=None,
                              urgency=None, model="rules")
        result = self.model.predict(state, self.type_questions())
        answer = result["answers"]["type"]
        type_ = answer["choice"]
        type_conf = float(answer["answer_confidence"])
        ranked = sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])
        top2 = tuple((label, float(p)) for label, p in ranked[:2])
        model_name = result.get("routing", {}).get("model", "unknown")
        if type_ in skip_action_types:
            return LayaResult(type_, type_conf, top2, None, None, model_name)
        result = self.model.predict(state, self.action_questions())
        answers = result["answers"]
        return LayaResult(
            type=type_,
            type_conf=type_conf,
            top2=top2,
            needs_action=float(answers["needs_action"]["noul"]),
            urgency=float(answers["urgency"]["score"]),
            model=result.get("routing", {}).get("model", "unknown"),
        )
