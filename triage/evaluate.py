"""Read-only evaluation: classify inbox mail into a CSV, then score hand labels."""
from __future__ import annotations

import csv
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from google.auth.exceptions import RefreshError

from triage.classifier import Classifier
from triage.config import Config
from triage.gmail_client import AuthError
from triage.pipeline import Outcome, classify_message

log = logging.getLogger(__name__)

CSV_FIELDS = [
    "msg_id", "date", "from", "subject", "pred_type", "type_conf", "top2", "needs_action",
    "urgency", "pred_priority", "rules", "true_type", "true_priority",
]


def outcome_row(out: Outcome) -> dict[str, str]:
    f, laya = out.features, out.laya
    return {
        "msg_id": f.msg_id,
        "date": datetime.fromtimestamp(f.internal_date / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "from": f.from_email,
        "subject": f.subject,
        "pred_type": out.decision.type,
        "type_conf": f"{laya.type_conf:.2f}",
        "top2": "; ".join(f"{label}={p:.2f}" for label, p in laya.top2),
        "needs_action": f"{laya.needs_action:.2f}",
        "urgency": f"{laya.urgency:.2f}",
        "pred_priority": out.decision.priority or "",
        "rules": ",".join(out.hits.names),
        "true_type": "",
        "true_priority": "",
    }


def _read_rows(path: Path) -> list[dict]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def run_eval(gmail, cfg: Config, classifier: Classifier, out_path: Path, limit: int = 500,
             from_csv: Path | None = None) -> int:
    prior = {r["msg_id"]: r for r in _read_rows(from_csv)} if from_csv else {}
    msg_ids = list(prior) if from_csv else gmail.list_ids("in:inbox", limit=limit)
    rows = []
    for i, msg_id in enumerate(msg_ids, 1):
        try:
            row = outcome_row(classify_message(gmail.get(msg_id), cfg, classifier))
        except (AuthError, RefreshError):
            raise
        except Exception:
            log.exception("eval failed on %s", msg_id)
            continue
        if msg_id in prior:
            row["true_type"] = prior[msg_id].get("true_type", "")
            row["true_priority"] = prior[msg_id].get("true_priority", "")
        rows.append(row)
        if i % 25 == 0:
            log.info("eval %d/%d", i, len(msg_ids))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole} = {part / whole:.1%}"


def score_rows(rows: list[dict]) -> str:
    labelled = [r for r in rows if r.get("true_type", "").strip()]
    if not labelled:
        return "No rows have true_type filled in."
    wrong = [r for r in labelled if r["true_type"].strip() != r["pred_type"]]
    lines = [f"Type accuracy: {_pct(len(labelled) - len(wrong), len(labelled))}"]

    confusions = Counter((r["true_type"].strip(), r["pred_type"]) for r in wrong)
    if confusions:
        lines.append("Confusions (true -> predicted):")
        lines += [f"  {true} -> {pred}: {n}" for (true, pred), n in confusions.most_common()]

    false_suspicious = [r for r in wrong if r["pred_type"] == "suspicious"]
    lines.append(f"False Suspicious: {len(false_suspicious)}")
    lines += [f"  {r['subject']}" for r in false_suspicious]

    act_now = [r for r in rows if r.get("pred_priority") == "act_now" and r.get("true_priority", "").strip()]
    if act_now:
        hits = sum(r["true_priority"].strip() == "act_now" for r in act_now)
        lines.append(f"Act Now precision: {_pct(hits, len(act_now))}")
    else:
        lines.append("Act Now precision: no labelled act_now rows")
    return "\n".join(lines)


def score_csv(path: Path) -> str:
    return score_rows(_read_rows(path))
