import logging

import httplib2
import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from tests.helpers import FakeGmail, FakeModel, bank_msg, make_msg
from triage.classifier import Classifier
from triage.gmail_client import AuthError
from triage.pipeline import build_query, classify_message, run_once, should_skip
from triage.store import Store

NOW = 1_790_000_000


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "s.db") as s:
        yield s


class SubjectModel(FakeModel):
    """Picks the type from the state: 'order' text means orders, else finance."""

    def predict(self, state, questions):
        self.type_ = "orders" if "order" in state.lower() else "finance"
        return super().predict(state, questions)


class FlakyGmail(FakeGmail):
    """add_labels raises for the given label-id sets."""

    def __init__(self, messages, bad_labels=(), exc=None, **kw):
        super().__init__(messages, **kw)
        self.bad_labels = {tuple(sorted(b)) for b in bad_labels}
        self.exc = exc or RuntimeError("api down")

    def add_labels(self, msg_ids, label_ids):
        if tuple(sorted(label_ids)) in self.bad_labels:
            raise self.exc
        super().add_labels(msg_ids, label_ids)


def order_msg(msg_id):
    return make_msg(
        msg_id,
        headers={"From": "Shop <orders@shop.example>", "Subject": "Your order shipped",
                 "Authentication-Results": "mx.google.com; dkim=pass; spf=pass; dmarc=pass"},
        plain="Your order has shipped",
    )


def http_error(status):
    return HttpError(httplib2.Response({"status": status}), b"x")


def rows(store, msg_id):
    return store.db.execute(
        "SELECT status, attempts FROM processed WHERE msg_id = ?", (msg_id,)).fetchone()


def predictions(store):
    return store.db.execute("SELECT msg_id FROM predictions ORDER BY msg_id").fetchall()


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


def test_labels_new_mail_and_records_state(store, cfg):
    gmail = FakeGmail({"m1": bank_msg("m1"), "m2": bank_msg("m2")})
    counts = run_once(gmail, store, cfg, Classifier(cfg, FakeModel(type_="finance")), now=NOW)
    assert counts == {"labelled": 2, "skipped": 0, "errors": 0, "failed": 0, "aborted": False}
    assert gmail.added == [(["m1", "m2"], ["id:Laya/Finance"])]
    assert store.is_done("m1") and store.is_done("m2")
    assert store.get_last_run() == NOW


def test_second_run_does_not_relabel(store, cfg):
    gmail = FakeGmail({"m1": bank_msg("m1")})
    classifier = Classifier(cfg, FakeModel())
    run_once(gmail, store, cfg, classifier, now=NOW)
    run_once(gmail, store, cfg, classifier, now=NOW + 300)
    assert len(gmail.added) == 1


def test_skips_spam_trash_and_already_labelled(store, cfg):
    gmail = FakeGmail({
        "spam": make_msg("spam", label_ids=("SPAM",)),
        "trash": make_msg("trash", label_ids=("TRASH",)),
        "done": make_msg("done", label_ids=("INBOX", "id:Laya/Orders")),
    })
    counts = run_once(gmail, store, cfg, Classifier(cfg, FakeModel()), now=NOW)
    assert counts == {"labelled": 0, "skipped": 3, "errors": 0, "failed": 0, "aborted": False}
    assert gmail.added == []


def test_two_label_groups_in_one_pass(store, cfg):
    gmail = FakeGmail({"m1": bank_msg("m1"), "o1": order_msg("o1"), "m2": bank_msg("m2")})
    counts = run_once(gmail, store, cfg, Classifier(cfg, SubjectModel()), now=NOW)
    assert counts["labelled"] == 3
    assert sorted(gmail.added) == [(["m1", "m2"], ["id:Laya/Finance"]), (["o1"], ["id:Laya/Orders"])]
    assert len(predictions(store)) == 3


def test_bad_message_does_not_block_good_ones(store, cfg):
    gmail = FakeGmail({"bad": RuntimeError("boom"), "m1": bank_msg("m1")})
    counts = run_once(gmail, store, cfg, Classifier(cfg, FakeModel()), now=NOW)
    assert counts == {"labelled": 1, "skipped": 0, "errors": 1, "failed": 0, "aborted": False}
    assert gmail.added == [(["m1"], ["id:Laya/Finance"])]
    assert rows(store, "bad") == ("error", 1)
    assert store.get_last_run() == NOW


def test_errors_retry_then_fail_unlabelled(store, cfg):
    gmail = FakeGmail({"bad": RuntimeError("boom")}, list_once=True)
    classifier = Classifier(cfg, FakeModel())
    run_once(gmail, store, cfg, classifier, now=NOW)
    run_once(gmail, store, cfg, classifier, now=NOW + 300)
    assert rows(store, "bad") == ("error", 2)
    counts = run_once(gmail, store, cfg, classifier, now=NOW + 600)
    assert counts == {"labelled": 0, "skipped": 0, "errors": 0, "failed": 1, "aborted": False}
    assert gmail.added == []
    assert rows(store, "bad")[0] == "failed"
    assert store.is_done("bad") and store.retry_ids() == []
    assert predictions(store) == []


def test_gmail_404_is_skipped_without_error(store, cfg):
    gmail = FakeGmail({"gone": http_error(404), "m1": bank_msg("m1")})
    counts = run_once(gmail, store, cfg, Classifier(cfg, FakeModel()), now=NOW)
    assert counts == {"labelled": 1, "skipped": 1, "errors": 0, "failed": 0, "aborted": False}
    assert rows(store, "gone") == ("skipped", 0)


def test_other_http_error_counts_as_error(store, cfg):
    gmail = FakeGmail({"x": http_error(500), "m1": bank_msg("m1")})
    counts = run_once(gmail, store, cfg, Classifier(cfg, FakeModel()), now=NOW)
    assert counts["errors"] == 1 and rows(store, "x") == ("error", 1)


def test_add_labels_failure_in_one_group_does_not_block_other(store, cfg, caplog):
    gmail = FlakyGmail({"m1": bank_msg("m1"), "o1": order_msg("o1")},
                       bad_labels=[["id:Laya/Orders"]])
    with caplog.at_level(logging.ERROR):
        counts = run_once(gmail, store, cfg, Classifier(cfg, SubjectModel()), now=NOW)
    assert counts == {"labelled": 1, "skipped": 0, "errors": 1, "failed": 0, "aborted": False}
    assert gmail.added == [(["m1"], ["id:Laya/Finance"])]
    assert rows(store, "o1") == ("error", 1)
    assert rows(store, "m1")[0] == "ok"
    assert predictions(store) == [("m1",)]


def test_add_labels_failure_at_cap_fails_message(store, cfg):
    gmail = FlakyGmail({"m0": bank_msg("m0"), "o1": order_msg("o1")},
                       bad_labels=[["id:Laya/Orders"]])
    classifier = Classifier(cfg, SubjectModel())
    for n in range(3):
        gmail.messages[f"m{n + 1}"] = bank_msg(f"m{n + 1}")  # keeps one group succeeding each pass
        counts = run_once(gmail, store, cfg, classifier, now=NOW + n * 300)
    assert counts["failed"] == 1 and counts["errors"] == 0
    assert rows(store, "o1")[0] == "failed"
    assert all(label == ["id:Laya/Finance"] for _, label in gmail.added)


def test_auth_error_propagates(store, cfg):
    gmail = FakeGmail({"m1": AuthError("revoked")})
    with pytest.raises(AuthError):
        run_once(gmail, store, cfg, Classifier(cfg, FakeModel()), now=NOW)


def test_refresh_error_propagates_from_get(store, cfg):
    gmail = FakeGmail({"m1": RefreshError("expired")})
    with pytest.raises(RefreshError):
        run_once(gmail, store, cfg, Classifier(cfg, FakeModel()), now=NOW)


def test_refresh_error_propagates_from_add_labels(store, cfg):
    gmail = FlakyGmail({"m1": bank_msg("m1")}, bad_labels=[["id:Laya/Finance"]],
                       exc=RefreshError("expired"))
    with pytest.raises(RefreshError):
        run_once(gmail, store, cfg, Classifier(cfg, FakeModel()), now=NOW)
    assert predictions(store) == []


def test_circuit_breaker_trips_on_mass_failure(store, cfg, caplog):
    messages = {f"bad{i}": RuntimeError("down") for i in range(5)}
    messages["m1"] = bank_msg("m1")
    gmail = FakeGmail(messages)
    store.set_last_run(NOW - 1000)
    with caplog.at_level(logging.WARNING):
        counts = run_once(gmail, store, cfg, Classifier(cfg, FakeModel()), now=NOW)
    assert counts["aborted"] is True
    assert counts["labelled"] == 1 and counts["errors"] == 5
    assert "circuit breaker: 5/6 failed, aborting pass" in caplog.text
    assert gmail.added == [(["m1"], ["id:Laya/Finance"])]
    for i in range(5):
        assert store.attempts(f"bad{i}") == 0
        assert rows(store, f"bad{i}") is None
    assert store.get_last_run() == NOW - 1000


def test_breaker_needs_majority_and_three_errors(store, cfg):
    messages = {"bad1": RuntimeError("x"), "bad2": RuntimeError("x")}
    gmail = FakeGmail(messages)
    counts = run_once(gmail, store, cfg, Classifier(cfg, FakeModel()), now=NOW)
    assert counts["aborted"] is False and counts["errors"] == 2
    assert store.attempts("bad1") == 1
    assert store.get_last_run() == NOW


def test_retry_only_failures_reach_failed_even_with_breaker(store, cfg):
    ids = ["r1", "r2", "r3"]
    for i in ids:
        store.mark(i, "error", [])
    gmail = FakeGmail({i: RuntimeError("down") for i in ids}, list_once=True)
    classifier = Classifier(cfg, FakeModel())
    for n in range(3):
        counts = run_once(gmail, store, cfg, classifier, now=NOW + n * 300)
        assert counts["aborted"] is False
    for i in ids:
        assert rows(store, i)[0] == "failed"
    assert gmail.added == []


def test_breaker_counts_only_new_candidates_but_bumps_retries(store, cfg):
    for i in ("r1", "r2", "r3"):
        store.mark(i, "error", [])
    messages = {i: RuntimeError("down") for i in ("r1", "r2", "r3", "n1", "n2", "n3", "n4")}
    gmail = FakeGmail(messages)
    store.set_last_run(NOW - 1000)
    counts = run_once(gmail, store, cfg, Classifier(cfg, FakeModel()), now=NOW)
    assert counts["aborted"] is True
    assert store.get_last_run() == NOW - 1000
    for i in ("r1", "r2", "r3"):
        assert rows(store, i) == ("error", 2)
    for i in ("n1", "n2", "n3", "n4"):
        assert rows(store, i) is None


def test_all_groups_failing_is_outage_not_bump(store, cfg, caplog):
    gmail = FlakyGmail({"m1": bank_msg("m1"), "o1": order_msg("o1")},
                       bad_labels=[["id:Laya/Finance"], ["id:Laya/Orders"]])
    classifier = Classifier(cfg, SubjectModel())
    store.set_last_run(NOW - 1000)
    for n in range(3):
        with caplog.at_level(logging.WARNING):
            counts = run_once(gmail, store, cfg, classifier, now=NOW + n * 300)
        assert counts["labelled"] == 0 and counts["aborted"] is True
    for i in ("m1", "o1"):
        assert rows(store, i) is None or rows(store, i)[0] != "failed"
        assert store.attempts(i) == 0
    assert gmail.added == []
    assert store.get_last_run() == NOW - 1000
    assert "outage" in caplog.text


def test_verified_sender_is_not_offered_suspicious(cfg):
    model = FakeModel(type_="finance")
    classify_message(bank_msg("m1"), cfg, Classifier(cfg, model))
    assert "suspicious" not in model.calls[0][1]["type"]["criteria"]


def test_unverified_sender_is_offered_suspicious(cfg):
    model = FakeModel(type_="promotions")
    msg = make_msg("m1", headers={"From": "Prize Desk <win@prize.example>", "Subject": "You won"},
                   plain="Claim your prize now by sending your card details")
    classify_message(msg, cfg, Classifier(cfg, model))
    assert "suspicious" in model.calls[0][1]["type"]["criteria"]


def test_dict_state_format_is_passed_to_laya(cfg):
    import dataclasses
    model = FakeModel()
    classify_message(bank_msg("m1"), dataclasses.replace(cfg, state_format="dict"), Classifier(cfg, model))
    assert isinstance(model.calls[0][0], dict) and model.calls[0][0]["subject"] == "Statement"
