"""Read-only evaluation: classify inbox mail into a CSV, then score hand labels."""
from __future__ import annotations

import csv
import logging
from collections import Counter
from datetime import datetime, timezone
from statistics import median
from pathlib import Path

from google.auth.exceptions import RefreshError

from triage.classifier import Classifier
from triage.config import Config
from triage.gmail_client import AuthError
from triage.pipeline import Outcome, classify_message

log = logging.getLogger(__name__)

CSV_FIELDS = [
    "msg_id", "date", "from", "subject", "pred_type", "type_conf", "top2", "needs_action",
    "urgency", "pred_priority", "unsure", "rules", "true_type", "true_priority",
]


def _safe(value: str) -> str:
    """Neutralise spreadsheet formula injection."""
    return "'" + value if value and value[0] in "=+-@\t\r" else value


def _num(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def outcome_row(out: Outcome) -> dict[str, str]:
    f, laya = out.features, out.laya
    return {
        "msg_id": f.msg_id,
        "date": datetime.fromtimestamp(f.internal_date / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "from": _safe(f.from_email),
        "subject": _safe(f.subject),
        "pred_type": out.decision.type,
        "type_conf": f"{laya.type_conf:.2f}",
        "top2": _safe("; ".join(f"{label}={p:.2f}" for label, p in laya.top2)),
        "needs_action": _num(laya.needs_action),
        "urgency": _num(laya.urgency),
        "pred_priority": out.decision.priority or "",
        "unsure": "yes" if out.decision.unsure else "",
        "rules": ",".join(out.hits.names),
        "true_type": "",
        "true_priority": "",
    }


def _read_rows(path: Path) -> list[dict]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def _fallback_row(prior_row: dict) -> dict[str, str]:
    row = {k: prior_row.get(k, "") or "" for k in CSV_FIELDS}
    row["pred_type"] = "ERROR"
    return row


def run_eval(gmail, cfg: Config, classifier: Classifier, out_path: Path, limit: int = 500,
             from_csv: Path | None = None) -> int:
    """Stream rows to out_path. With from_csv, limit is ignored."""
    prior: dict[str, dict] = {}
    if from_csv:
        with from_csv.open(newline="") as src:
            reader = csv.DictReader(src)
            prior_rows = list(reader)
            header = set(reader.fieldnames or [])
        missing = {"msg_id", "true_type", "true_priority"} - header
        if missing:
            raise ValueError(f"{from_csv} is missing column(s): {', '.join(sorted(missing))}")
        prior = {r["msg_id"]: r for r in prior_rows}
    msg_ids = list(prior) if from_csv else gmail.list_ids("in:inbox", limit=limit)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = failures = 0
    fh = out_path.open("w", newline="")
    try:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        fh.flush()
        for i, msg_id in enumerate(msg_ids, 1):
            try:
                row = outcome_row(classify_message(gmail.get(msg_id), cfg, classifier))
            except (AuthError, RefreshError):
                raise
            except Exception:
                log.exception("eval failed on %s", msg_id)
                failures += 1
                if msg_id not in prior:
                    continue
                row = _fallback_row(prior[msg_id])
            if msg_id in prior:
                row["true_type"] = prior[msg_id].get("true_type", "")
                row["true_priority"] = prior[msg_id].get("true_priority", "")
            writer.writerow(row)
            fh.flush()
            written += 1
            if i % 25 == 0:
                log.info("eval %d/%d", i, len(msg_ids))
    finally:
        fh.close()
    if failures:
        log.warning("eval: %d failure(s) out of %d messages", failures, len(msg_ids))
    return written


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole} = {part / whole:.1%}"


def _f(value) -> float | None:
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _dist(values: list[float]) -> str:
    return f"min {min(values):.2f} median {median(values):.2f} max {max(values):.2f}"


PRIORITIES = ("act_now", "this_week", "none")


def _tuning_lines(rows: list[dict], labelled: list[dict]) -> list[str]:
    lines = [""]
    for col in ("needs_action", "urgency"):
        lines.append(f"{col} by true_priority:")
        for prio in PRIORITIES:
            vals = [v for r in labelled if r.get("true_priority", "").strip() == prio
                    if (v := _f(r.get(col, ""))) is not None]
            lines.append(f"  {prio}: {_dist(vals)} (n={len(vals)})" if vals else f"  {prio}: no values")

    pts = [(na, ur, r["true_priority"].strip() == "act_now") for r in rows
           if r.get("true_priority", "").strip()
           and (na := _f(r.get("needs_action", ""))) is not None
           and (ur := _f(r.get("urgency", ""))) is not None]
    lines.append("act_now precision/recall sweep:")
    total_pos = sum(p for _, _, p in pts)
    best: tuple[float, float, float] | None = None
    for na_t in (0.3, 0.4, 0.5, 0.6, 0.7):
        for ur_t in (1.5, 2.0, 2.5, 3.0):
            pred = [p for na, ur, p in pts if na >= na_t and ur >= ur_t]
            tp = sum(pred)
            prec = tp / len(pred) if pred else 0.0
            rec = tp / total_pos if total_pos else 0.0
            f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
            if best is None or f1 > best[0]:
                best = (f1, na_t, ur_t)
            lines.append(f"  needs_action>={na_t} urgency>={ur_t}: precision "
                         f"{_pct(tp, len(pred)) if pred else 'n/a'} recall "
                         f"{_pct(tp, total_pos) if total_pos else 'n/a'}")
    if best and pts:
        lines.append(f"Best F1: needs_action>={best[1]} urgency>={best[2]} (F1 {best[0]:.2f})")
    else:
        lines.append("Best F1: n/a (no rows with needs_action, urgency and true_priority)")

    confs = [v for r in labelled if (v := _f(r.get("type_conf", ""))) is not None]
    if confs:
        lines.append(f"type_conf: {_dist(confs)}")
        unsure = sum(r.get("unsure", "").strip() == "yes" for r in labelled)
        lines.append(f"Unsure rate: {_pct(unsure, len(labelled))}")
        for t in (0.3, 0.4, 0.5, 0.55):
            lines.append(f"  type_conf<{t}: {_pct(sum(c < t for c in confs), len(confs))}")
    return lines


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
    lines += _tuning_lines(rows, labelled)
    return "\n".join(lines)


def score_csv(path: Path) -> str:
    return score_rows(_read_rows(path))
