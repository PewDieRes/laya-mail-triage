from pathlib import Path

import pytest
import yaml

from triage.config import load_config, load_vip

REPO = Path(__file__).resolve().parent.parent


def write_config(tmp_path, mutate):
    raw = yaml.safe_load((REPO / "config" / "config.yaml").read_text())
    mutate(raw)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw))
    return tmp_path


def test_loads_repo_config():
    cfg = load_config(REPO / "config")
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
