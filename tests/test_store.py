import json
import sqlite3

import pytest

from tests.helpers import make_features, make_laya
from triage.rules import RuleHits
from triage.store import Store


def test_new_message_is_not_done(tmp_path):
    with Store(tmp_path / "s.db") as store:
        assert not store.is_done("m1")
        assert store.attempts("m1") == 0


def test_ok_and_skipped_are_done(tmp_path):
    with Store(tmp_path / "s.db") as store:
        store.mark("m1", "ok", ["Laya/Finance"])
        store.mark("m2", "skipped", [])
        assert store.is_done("m1") and store.is_done("m2")


def test_errors_count_attempts_and_queue_retry(tmp_path):
    with Store(tmp_path / "s.db") as store:
        store.mark("m1", "error", [])
        store.mark("m1", "error", [])
        assert store.attempts("m1") == 2
        assert not store.is_done("m1")
        assert store.retry_ids() == ["m1"]


def test_ok_after_error_clears_retry(tmp_path):
    with Store(tmp_path / "s.db") as store:
        store.mark("m1", "error", [])
        store.mark("m1", "ok", ["Laya/?Unsure"])
        assert store.retry_ids() == []
        assert store.attempts("m1") == 1


def test_last_run_roundtrip(tmp_path):
    with Store(tmp_path / "s.db") as store:
        assert store.get_last_run() is None
        store.set_last_run(123)
        store.set_last_run(456)
        assert store.get_last_run() == 456


def test_log_prediction_writes_row(tmp_path):
    with Store(tmp_path / "s.db") as store:
        store.log_prediction("run1", make_features(), make_laya(), RuleHits(vip=True, names=("vip",)),
                             ["Laya/Finance", "Laya/!Act Now"])
        row = store.db.execute("SELECT msg_id, run_id, type, rule_hits, labels FROM predictions").fetchone()
        assert row[:3] == ("m1", "run1", "finance")
        assert json.loads(row[3]) == ["vip"]
        assert json.loads(row[4]) == ["Laya/Finance", "Laya/!Act Now"]


def test_state_persists_across_instances(tmp_path):
    with Store(tmp_path / "s.db") as first:
        first.mark("m1", "ok", [])
    with Store(tmp_path / "s.db") as second:
        assert second.is_done("m1")


def test_close_releases_connection(tmp_path):
    store = Store(tmp_path / "s.db")
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        store.db.execute("SELECT 1")


def test_context_manager_closes(tmp_path):
    with Store(tmp_path / "s.db") as store:
        store.mark("m1", "ok", [])
    with pytest.raises(sqlite3.ProgrammingError):
        store.db.execute("SELECT 1")
    with Store(tmp_path / "s.db") as again:
        assert again.is_done("m1")


def test_failed_is_terminal(tmp_path):
    with Store(tmp_path / "s.db") as store:
        store.mark("m1", "error", [])
        store.mark("m1", "failed", [])
        assert store.is_done("m1")
        assert store.retry_ids() == []
        assert store.attempts("m1") == 1


def test_sender_memory_roundtrip(tmp_path):
    with Store(tmp_path / "s.db") as store:
        assert store.recall_sender("a@x.com") is None
        store.remember_sender("A@X.com", "finance")
        store.remember_sender("a@x.com", "orders")
        assert store.recall_sender("a@x.com") == "orders"


def test_recent_labelled_returns_sender_and_labels(tmp_path):
    with Store(tmp_path / "s.db") as store:
        store.log_prediction("r", make_features(msg_id="m1", from_email="bank@x.com"), make_laya(),
                             RuleHits(), ["Laya/Finance"])
        store.mark("m1", "ok", ["Laya/Finance"])
        store.mark("m2", "skipped", [])
        assert store.recent_labelled(0) == [("m1", "bank@x.com", ["Laya/Finance"])]
        assert store.recent_labelled(2**40) == []
