import pytest

from tests.helpers import FakeGmail, FakeModel, bank_msg, make_msg
from triage.classifier import Classifier
from triage.gmail_client import AuthError
from triage.pipeline import build_query, classify_message, run_once, should_skip
from triage.store import Store

NOW = 1_790_000_000


def test_build_query_first_run_looks_back_a_day_plus_overlap():
    assert build_query(None, NOW) == f"in:inbox after:{NOW - 86400 - 3600}"


def test_build_query_overlaps_last_run_by_an_hour():
    assert build_query(NOW - 300, NOW) == f"in:inbox after:{NOW - 300 - 3600}"


def test_should_skip():
    assert should_skip(make_msg(label_ids=("SPAM",)), set())
    assert should_skip(make_msg(label_ids=("SENT",)), set())
    assert should_skip(make_msg(label_ids=("INBOX", "L9")), {"L9"})
    assert not should_skip(make_msg(label_ids=("INBOX",)), {"L9"})


def test_classify_message_end_to_end(cfg):
    out = classify_message(bank_msg("m1"), cfg, Classifier(cfg, FakeModel(type_="finance")))
    assert out.features.from_email == "alerts@hdfcbank.net"
    assert out.decision.label_names(cfg) == ["Laya/Finance"]


def test_labels_new_mail_and_records_state(tmp_path, cfg):
    gmail = FakeGmail({"m1": bank_msg("m1"), "m2": bank_msg("m2")})
    store = Store(tmp_path / "s.db")
    counts = run_once(gmail, store, cfg, Classifier(cfg, FakeModel(type_="finance")), now=NOW)
    assert counts == {"labelled": 2, "skipped": 0, "errors": 0}
    assert gmail.added == [(["m1", "m2"], ["id:Laya/Finance"])]
    assert store.is_done("m1") and store.is_done("m2")
    assert store.get_last_run() == NOW


def test_second_run_does_not_relabel(tmp_path, cfg):
    gmail = FakeGmail({"m1": bank_msg("m1")})
    store = Store(tmp_path / "s.db")
    classifier = Classifier(cfg, FakeModel())
    run_once(gmail, store, cfg, classifier, now=NOW)
    run_once(gmail, store, cfg, classifier, now=NOW + 300)
    assert len(gmail.added) == 1


def test_skips_spam_and_already_labelled(tmp_path, cfg):
    gmail = FakeGmail({
        "spam": make_msg("spam", label_ids=("SPAM",)),
        "done": make_msg("done", label_ids=("INBOX", "id:Laya/Orders")),
    })
    counts = run_once(gmail, Store(tmp_path / "s.db"), cfg, Classifier(cfg, FakeModel()), now=NOW)
    assert counts == {"labelled": 0, "skipped": 2, "errors": 0}
    assert gmail.added == []


def test_errors_retry_then_fall_back_to_unsure(tmp_path, cfg):
    gmail = FakeGmail({"bad": RuntimeError("boom")}, list_once=True)
    store = Store(tmp_path / "s.db")
    classifier = Classifier(cfg, FakeModel())
    run_once(gmail, store, cfg, classifier, now=NOW)
    run_once(gmail, store, cfg, classifier, now=NOW + 300)
    assert gmail.added == []
    run_once(gmail, store, cfg, classifier, now=NOW + 600)
    assert gmail.added == [(["bad"], ["id:Laya/?Unsure"])]
    assert store.is_done("bad")


def test_auth_error_propagates(tmp_path, cfg):
    gmail = FakeGmail({"m1": AuthError("revoked")})
    with pytest.raises(AuthError):
        run_once(gmail, Store(tmp_path / "s.db"), cfg, Classifier(cfg, FakeModel()), now=NOW)
