import json

from tests.helpers import make_features, make_laya
from triage.rules import RuleHits
from triage.store import Store


def test_new_message_is_not_done(tmp_path):
    store = Store(tmp_path / "s.db")
    assert not store.is_done("m1")
    assert store.attempts("m1") == 0


def test_ok_and_skipped_are_done(tmp_path):
    store = Store(tmp_path / "s.db")
    store.mark("m1", "ok", ["Laya/Finance"])
    store.mark("m2", "skipped", [])
    assert store.is_done("m1") and store.is_done("m2")


def test_errors_count_attempts_and_queue_retry(tmp_path):
    store = Store(tmp_path / "s.db")
    store.mark("m1", "error", [])
    store.mark("m1", "error", [])
    assert store.attempts("m1") == 2
    assert not store.is_done("m1")
    assert store.retry_ids() == ["m1"]


def test_ok_after_error_clears_retry(tmp_path):
    store = Store(tmp_path / "s.db")
    store.mark("m1", "error", [])
    store.mark("m1", "ok", ["Laya/?Unsure"])
    assert store.retry_ids() == []
    assert store.attempts("m1") == 1


def test_last_run_roundtrip(tmp_path):
    store = Store(tmp_path / "s.db")
    assert store.get_last_run() is None
    store.set_last_run(123)
    store.set_last_run(456)
    assert store.get_last_run() == 456


def test_log_prediction_writes_row(tmp_path):
    store = Store(tmp_path / "s.db")
    store.log_prediction("run1", make_features(), make_laya(), RuleHits(vip=True, names=("vip",)),
                         ["Laya/Finance", "Laya/!Act Now"])
    row = store.db.execute("SELECT msg_id, run_id, type, rule_hits, labels FROM predictions").fetchone()
    assert row[:3] == ("m1", "run1", "finance")
    assert json.loads(row[3]) == ["vip"]
    assert json.loads(row[4]) == ["Laya/Finance", "Laya/!Act Now"]


def test_state_persists_across_instances(tmp_path):
    Store(tmp_path / "s.db").mark("m1", "ok", [])
    assert Store(tmp_path / "s.db").is_done("m1")
