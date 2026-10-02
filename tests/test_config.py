from pathlib import Path

import pytest
import yaml

from triage.config import load_config, load_vip

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "config"


def write_config(tmp_path, mutate):
    raw = yaml.safe_load((FIXTURE / "config.yaml").read_text())
    mutate(raw)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw))
    return tmp_path


def test_loads_fixture_config():
    cfg = load_config(FIXTURE)
    assert len(cfg.type_criteria) == 10
    assert set(cfg.type_criteria) == set(cfg.type_labels)
    assert all(name.startswith("Laya/") for name in cfg.all_label_names())
    assert len(cfg.all_label_names()) == 13
    assert cfg.no_priority_types == {"promotions", "newsletters", "suspicious"}
    assert cfg.urgency_levels[0] == "no deadline"
    assert cfg.thresholds.type_conf == 0.55


def test_vip_parsing(tmp_path):
    path = tmp_path / "vip.txt"
    path.write_text("# family\nMom@Example.com\n\n  @family.org  \n")
    assert load_vip(path) == {"mom@example.com", "@family.org"}


def test_missing_vip_file_means_empty(tmp_path):
    assert load_vip(tmp_path / "vip.txt") == frozenset()


def test_unknown_no_priority_type_rejected(tmp_path):
    config_dir = write_config(tmp_path, lambda raw: raw.update(no_priority_types=["promotions", "spam"]))
    with pytest.raises(ValueError, match="spam"):
        load_config(config_dir)


def test_suspicious_type_required(tmp_path):
    def drop_suspicious(raw):
        del raw["types"]["suspicious"]
        raw["no_priority_types"] = ["promotions"]

    with pytest.raises(ValueError, match="suspicious"):
        load_config(write_config(tmp_path, drop_suspicious))


def test_priority_criteria_must_have_three_keys(tmp_path):
    def bad(raw):
        raw["priority_question"] = "q"
        raw["priority_criteria"] = {"act_now": "a", "none": "n"}

    with pytest.raises(ValueError, match="priority_criteria"):
        load_config(write_config(tmp_path, bad))


def test_priority_criteria_optional():
    assert load_config(FIXTURE).priority_criteria is None


def test_type_groups_must_cover_every_type(tmp_path):
    def bad(raw):
        raw["type_groups"] = {"jobs": {"criteria": "work", "members": ["career"]}}

    with pytest.raises(ValueError, match="type_groups"):
        load_config(write_config(tmp_path, bad))


def test_priority_rules_reject_unknown_signal(tmp_path):
    def bad(raw):
        raw["priority_signals"] = {"wait": "Is someone waiting?"}
        raw["priority_rules"] = {"act_now": {"signals": ["nope"], "threshold": 0.5},
                                 "this_week": {"signals": ["wait"], "threshold": 0.5}}

    with pytest.raises(ValueError, match="unknown signals"):
        load_config(write_config(tmp_path, bad))


def test_priority_disabled_has_no_this_week_label(tmp_path):
    cfg = load_config(write_config(tmp_path, lambda raw: raw.update(priority_enabled=False)))
    assert cfg.priority_enabled is False
    assert "Laya/!This Week" not in cfg.all_label_names()
    assert {"Laya/!Act Now", "Laya/?Unsure"} <= set(cfg.all_label_names())


def test_production_config_is_valid():
    cfg = load_config(REPO / "config")
    assert cfg.type_groups is not None and not cfg.priority_enabled
    assert all(name.startswith("Laya/") for name in cfg.all_label_names())
    assert cfg.no_priority_types <= set(cfg.type_criteria)
