import csv

from tests.helpers import FakeGmail, FakeModel, bank_msg
from triage.classifier import Classifier
from triage.evaluate import CSV_FIELDS, run_eval, score_rows
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
