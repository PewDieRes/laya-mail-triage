import csv

import pytest

from tests.helpers import FakeGmail, FakeModel, bank_msg
from triage.classifier import Classifier
from triage.evaluate import CSV_FIELDS, outcome_row, run_eval, score_rows
from triage.gmail_client import AuthError
from triage.pipeline import classify_message
from tests.helpers import make_msg
from triage.main import main


def read_rows(path):
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def test_run_eval_writes_csv_without_touching_gmail(tmp_path, cfg):
    gmail = FakeGmail({"m1": bank_msg("m1"), "m2": bank_msg("m2")})
    out = tmp_path / "eval.csv"
    n = run_eval(gmail, cfg, Classifier(cfg, FakeModel(type_="finance", conf=0.9)), out, limit=10)
    rows = read_rows(out)
    assert n == 2
    assert list(rows[0]) == CSV_FIELDS
    assert rows[0]["pred_type"] == "finance"
    assert rows[0]["from"] == "alerts@hdfcbank.net"
    assert rows[0]["type_conf"] == "0.90"
    assert rows[0]["true_type"] == ""
    assert gmail.added == []
    assert gmail.queries == ["in:inbox"]


def test_reeval_keeps_true_labels(tmp_path, cfg):
    gmail = FakeGmail({"m1": bank_msg("m1")})
    first = tmp_path / "first.csv"
    run_eval(gmail, cfg, Classifier(cfg, FakeModel(type_="orders")), first)
    rows = read_rows(first)
    rows[0]["true_type"] = "finance"
    rows[0]["true_priority"] = "none"
    with first.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    second = tmp_path / "second.csv"
    run_eval(gmail, cfg, Classifier(cfg, FakeModel(type_="finance")), second, from_csv=first)
    row = read_rows(second)[0]
    assert (row["pred_type"], row["true_type"], row["true_priority"]) == ("finance", "finance", "none")


def row(pred, true, pred_priority="", true_priority=""):
    return {"pred_type": pred, "true_type": true, "pred_priority": pred_priority,
            "true_priority": true_priority, "subject": "subj"}


def test_score_accuracy_and_confusions():
    report = score_rows([row("finance", "finance"), row("orders", "finance"), row("updates", "")])
    assert "Type accuracy: 1/2 = 50.0%" in report
    assert "finance -> orders: 1" in report


def test_score_counts_false_suspicious():
    assert "False Suspicious: 1" in score_rows([row("suspicious", "finance")])


def test_score_act_now_precision():
    rows = [row("finance", "finance", "act_now", "act_now"), row("finance", "finance", "act_now", "none")]
    assert "Act Now precision: 1/2 = 50.0%" in score_rows(rows)


def test_score_without_labels():
    assert score_rows([row("finance", "")]) == "No rows have true_type filled in."


def test_main_score_command(tmp_path, capsys):
    path = tmp_path / "labelled.csv"
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerow({**dict.fromkeys(CSV_FIELDS, ""), **row("finance", "finance")})
    assert main(["score", str(path)]) == 0
    assert "Type accuracy: 1/1 = 100.0%" in capsys.readouterr().out


def test_csv_fields_order():
    assert CSV_FIELDS[CSV_FIELDS.index("pred_priority") + 1] == "unsure"
    assert CSV_FIELDS[-2:] == ["true_type", "true_priority"]


def test_csv_formula_injection_is_neutralised(tmp_path, cfg):
    msg = make_msg("m1", headers={"From": "=cmd@evil.example", "Subject": '=HYPERLINK("x")'}, plain="hi")
    out = tmp_path / "e.csv"
    run_eval(FakeGmail({"m1": msg}), cfg, Classifier(cfg, FakeModel()), out)
    row = read_rows(out)[0]
    assert row["subject"] == "'=HYPERLINK(\"x\")"
    assert row["from"] == "'=cmd@evil.example"


def test_fallback_row_neutralises_formulas(tmp_path, cfg):
    first = tmp_path / "first.csv"
    with first.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerow({**dict.fromkeys(CSV_FIELDS, ""), "msg_id": "m1", "from": "=a@b.c",
                    "subject": "+SUM(1)", "true_type": "finance", "true_priority": "none"})
    second = tmp_path / "second.csv"
    run_eval(FakeGmail({"m1": RuntimeError("boom")}), cfg, Classifier(cfg, FakeModel()), second,
             from_csv=first)
    row = read_rows(second)[0]
    assert row["from"] == "'=a@b.c" and row["subject"] == "'+SUM(1)"


def test_sweep_counts_blank_rows_as_misses():
    rows = [srow("act_now", "0.80", "3.00", "act_now"), srow("act_now", "", "")]
    report = score_rows(rows)
    assert "needs_action>=0.5 urgency>=2.5: precision 1/1 = 100.0% recall 1/2 = 50.0%" in report


def test_outcome_row_blank_for_none_priority_values(cfg):
    msg = make_msg("m1", headers={"From": "a@example.com", "Subject": "hi"}, plain="hi")
    out = classify_message(msg, cfg, Classifier(cfg, FakeModel(type_="finance")))
    from dataclasses import replace
    out = replace(out, laya=replace(out.laya, needs_action=None, urgency=None))
    r = outcome_row(out)
    assert r["needs_action"] == "" and r["urgency"] == ""


def test_run_eval_streams_rows_before_auth_error(tmp_path, cfg):
    gmail = FakeGmail({"m1": bank_msg("m1"), "m2": AuthError("expired")})
    out = tmp_path / "e.csv"
    with pytest.raises(AuthError):
        run_eval(gmail, cfg, Classifier(cfg, FakeModel()), out)
    rows = read_rows(out)
    assert [r["msg_id"] for r in rows] == ["m1"]


def _labelled_csv(path, ids):
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for i in ids:
            w.writerow({**dict.fromkeys(CSV_FIELDS, ""), "msg_id": i, "from": "x@y.z", "subject": "s",
                        "pred_type": "orders", "needs_action": "0.50", "true_type": "finance",
                        "true_priority": "none"})


def test_reeval_failed_row_written_through_as_error(tmp_path, cfg, caplog):
    first = tmp_path / "first.csv"
    _labelled_csv(first, ["m1", "m2"])
    gmail = FakeGmail({"m1": bank_msg("m1"), "m2": RuntimeError("boom")})
    second = tmp_path / "second.csv"
    n = run_eval(gmail, cfg, Classifier(cfg, FakeModel(type_="finance")), second, from_csv=first)
    rows = {r["msg_id"]: r for r in read_rows(second)}
    assert n == 2
    assert rows["m2"]["pred_type"] == "ERROR"
    assert (rows["m2"]["true_type"], rows["m2"]["true_priority"]) == ("finance", "none")
    assert rows["m2"]["needs_action"] == "0.50"
    assert rows["m1"]["pred_type"] == "finance"
    assert "1 failure" in caplog.text


def test_reeval_requires_label_columns(tmp_path, cfg):
    bad = tmp_path / "bad.csv"
    bad.write_text("msg_id,subject\nm1,x\n")
    with pytest.raises(ValueError, match="true_type"):
        run_eval(FakeGmail({}), cfg, Classifier(cfg, FakeModel()), tmp_path / "o.csv", from_csv=bad)


def srow(true_p, na, urg, pred_p="", type_conf="0.9", unsure=""):
    return {**row("finance", "finance", pred_p, true_p), "needs_action": na, "urgency": urg,
            "type_conf": type_conf, "unsure": unsure}


def test_score_distribution_and_sweep():
    rows = [srow("act_now", "0.80", "3.00", "act_now"), srow("act_now", "0.60", "2.00"),
            srow("this_week", "0.50", "1.00"), srow("none", "0.10", "0.50"), srow("none", "", "")]
    report = score_rows(rows)
    assert "needs_action by true_priority" in report
    assert "act_now: min 0.60 median 0.70 max 0.80 (n=2)" in report
    assert "none: min 0.10 median 0.10 max 0.10 (n=1)" in report
    assert "urgency by true_priority" in report
    assert "act_now precision/recall sweep" in report
    assert "needs_action>=0.5 urgency>=2.5: precision 1/1 = 100.0% recall 1/2 = 50.0%" in report
    assert "Best F1: needs_action>=0.3 urgency>=1.5 (F1 1.00)" in report


def test_score_type_conf_and_unsure_rate():
    rows = [srow("none", "", "", type_conf="0.20", unsure="yes"), srow("none", "", "", type_conf="0.60"),
            srow("none", "", "", type_conf="0.45")]
    report = score_rows(rows)
    assert "type_conf: min 0.20 median 0.45 max 0.60" in report
    assert "Unsure rate: 1/3 = 33.3%" in report
    assert "type_conf<0.3: 1/3 = 33.3%" in report
    assert "type_conf<0.5: 2/3 = 66.7%" in report
