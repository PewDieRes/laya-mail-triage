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
    assert decision.label_names(cfg) == ["Laya/Finance", "Laya/?Unsure"]


def test_missing_type_raises(cfg):
    with pytest.raises(ValueError):
        decide(make_laya(type=None), RuleHits(), cfg)
