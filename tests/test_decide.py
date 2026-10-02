import pytest

from tests.helpers import make_laya
from triage.decide import Decision, decide
from triage.rules import RuleHits


def test_informational_mail_gets_type_only(cfg):
    decision = decide(make_laya(), RuleHits(), cfg)
    assert decision == Decision("finance", None, False)
    assert decision.label_names(cfg) == ["Laya/Finance"]


def test_action_today_is_act_now(cfg):
    decision = decide(make_laya(needs_action=0.8, urgency=3.0), RuleHits(), cfg)
    assert decision.priority == "act_now"
    assert decision.label_names(cfg) == ["Laya/Finance", "Laya/!Act Now"]


def test_action_this_week(cfg):
    assert decide(make_laya(needs_action=0.8, urgency=2.0), RuleHits(), cfg).priority == "this_week"


def test_action_without_deadline_has_no_priority(cfg):
    assert decide(make_laya(needs_action=0.8, urgency=1.0), RuleHits(), cfg).priority is None


def test_urgent_but_no_action_has_no_priority(cfg):
    assert decide(make_laya(needs_action=0.5, urgency=4.0), RuleHits(), cfg).priority is None


def test_threshold_edges_are_inclusive(cfg):
    assert decide(make_laya(needs_action=0.6, urgency=2.5), RuleHits(), cfg).priority == "act_now"
    assert decide(make_laya(needs_action=0.6, urgency=1.5), RuleHits(), cfg).priority == "this_week"


def test_vip_forces_act_now(cfg):
    assert decide(make_laya(), RuleHits(vip=True, names=("vip",)), cfg).priority == "act_now"


def test_no_priority_for_promotions_even_from_vip(cfg):
    decision = decide(make_laya(type="promotions", needs_action=0.9, urgency=4.0),
                      RuleHits(vip=True, names=("vip",)), cfg)
    assert decision.priority is None


def test_forced_type_overrides_laya(cfg):
    hits = RuleHits(forced_type="suspicious", vip=True, names=("auth_fail", "vip"))
    decision = decide(make_laya(type=None, type_conf=1.0, top2=()), hits, cfg)
    assert decision == Decision("suspicious", None, False)
    assert decision.label_names(cfg) == ["Laya/Suspicious"]


def test_low_confidence_marks_unsure(cfg):
    decision = decide(make_laya(type_conf=0.4), RuleHits(), cfg)
    assert decision.unsure
    assert decision.label_names(cfg) == ["Laya/?Unsure"]  # no type guess shown


def test_missing_type_raises(cfg):
    with pytest.raises(ValueError):
        decide(make_laya(type=None), RuleHits(), cfg)


def test_none_scores_mean_no_laya_priority(cfg):
    assert decide(make_laya(needs_action=None, urgency=None), RuleHits(), cfg).priority is None


def test_vip_still_act_now_when_scores_none(cfg):
    laya = make_laya(needs_action=None, urgency=None)
    assert decide(laya, RuleHits(vip=True, names=("vip",)), cfg).priority == "act_now"


def test_priority_choice_is_used(cfg):
    assert decide(make_laya(priority="this_week"), RuleHits(), cfg).priority == "this_week"
    assert decide(make_laya(priority="none"), RuleHits(), cfg).priority is None
    assert decide(make_laya(priority="none"), RuleHits(vip=True, names=("vip",)), cfg).priority == "act_now"


def test_priority_choice_ignored_for_no_priority_types(cfg):
    assert decide(make_laya(type="promotions", priority="act_now"), RuleHits(), cfg).priority is None


def signal_cfg(cfg):
    import dataclasses
    return dataclasses.replace(
        cfg, priority_signals={"wait": "q1", "task": "q2"},
        priority_rules={"act_now": (("wait",), 0.6), "this_week": (("task",), 0.5)})


def test_priority_signals_rules(cfg):
    c = signal_cfg(cfg)
    assert decide(make_laya(signals=(("wait", 0.7), ("task", 0.9))), RuleHits(), c).priority == "act_now"
    assert decide(make_laya(signals=(("wait", 0.2), ("task", 0.5))), RuleHits(), c).priority == "this_week"
    assert decide(make_laya(signals=(("wait", 0.2), ("task", 0.1))), RuleHits(), c).priority is None
    assert decide(make_laya(signals=(("wait", 0.2), ("task", 0.1))), RuleHits(vip=True), c).priority == "act_now"
    assert decide(make_laya(type="promotions", signals=(("wait", 0.9),)), RuleHits(), c).priority is None


def test_unsure_vip_keeps_act_now(cfg):
    decision = decide(make_laya(type_conf=0.2), RuleHits(vip=True, names=("vip",)), cfg)
    assert decision.label_names(cfg) == ["Laya/?Unsure", "Laya/!Act Now"]
