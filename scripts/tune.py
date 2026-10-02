"""Dev tool: score one or more config variants against hand labels, loading Laya once.

Inputs: cached raw messages in data/cache/<id>.json (scripts/cache_messages.py) and a
labels CSV with columns msg_id,true_type,true_priority. Run in the container:
    docker compose run --rm -v ./triage:/app/triage -v ./scripts:/app/scripts triage \
        python scripts/tune.py data/labels.csv config [data/variants/v2 ...]
"""
import csv
import json
import sys
import time
from dataclasses import replace
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from triage.classifier import Classifier, load_router  # noqa: E402
from triage.config import load_config  # noqa: E402
from triage.pipeline import classify_message  # noqa: E402


def load_labels(path: Path) -> list[dict]:
    with path.open(newline="") as fh:
        return [r for r in csv.DictReader(fh) if r["true_type"].strip()]


def evaluate(config_dir: Path, labels: list[dict], model, type_only: bool = False) -> str:
    cfg = load_config(config_dir)
    if type_only:  # skip the needs_action/urgency call to iterate on type criteria faster
        cfg = replace(cfg, no_priority_types=frozenset(cfg.type_criteria))
    classifier = Classifier(cfg, model)
    rows, started = [], time.perf_counter()
    for label in labels:
        raw = json.loads((ROOT / "data" / "cache" / f"{label['msg_id']}.json").read_text())
        out = classify_message(raw, cfg, classifier)
        rows.append((label, out))
    elapsed = time.perf_counter() - started

    n = len(rows)
    type_ok = sum(l["true_type"] == o.decision.type for l, o in rows)
    prio = lambda o: o.decision.priority or "none"  # noqa: E731
    prio_ok = sum((l["true_priority"] or "none") == prio(o) for l, o in rows)
    unsure = sum(o.decision.unsure for _, o in rows)
    lines = [
        f"=== {config_dir}  n={n}  {elapsed / n:.2f}s/email",
        f"type accuracy {type_ok}/{n} = {type_ok / n:.1%}   priority accuracy {prio_ok}/{n} = {prio_ok / n:.1%}"
        f"   unsure {unsure}/{n}",
    ]
    for p in ("act_now", "this_week"):
        tp = sum(prio(o) == p and l["true_priority"] == p for l, o in rows)
        pred = sum(prio(o) == p for _, o in rows)
        true = sum(l["true_priority"] == p for l, _ in rows)
        lines.append(f"{p}: precision {tp}/{pred}  recall {tp}/{true}")
    conf = Counter((l["true_type"], o.decision.type) for l, o in rows if l["true_type"] != o.decision.type)
    lines.append("confusions (true -> pred): " + ", ".join(f"{t}->{p}:{c}" for (t, p), c in conf.most_common()))
    lines.append("mistakes:")
    for l, o in rows:
        f = o.features
        bad_type = l["true_type"] != o.decision.type
        bad_prio = (l["true_priority"] or "none") != prio(o)
        if bad_type or bad_prio:
            na = "-" if o.laya.needs_action is None else f"{o.laya.needs_action:.2f}"
            ur = "-" if o.laya.urgency is None else f"{o.laya.urgency:.2f}"
            top2 = " ".join(f"{k}={v:.2f}" for k, v in o.laya.top2)
            lines.append(
                f"  {'T' if bad_type else ' '}{'P' if bad_prio else ' '} {l['true_type']}/{l['true_priority'] or 'none'}"
                f" -> {o.decision.type}/{prio(o)} [{top2}] act={na} urg={ur} | {f.from_email[:28]} | {f.subject[:60]}"
            )
    records = [{
        "msg_id": l["msg_id"], "from": o.features.from_email, "date": o.features.internal_date,
        "true_type": l["true_type"], "true_priority": l["true_priority"] or "none",
        "pred_type": o.decision.type, "pred_priority": prio(o), "type_conf": o.laya.type_conf,
        "forced": o.hits.forced_type is not None,
    } for l, o in rows]
    return "\n".join(lines), records


def main() -> None:
    args = sys.argv[1:]
    type_only = "--type-only" in args
    args = [a for a in args if a != "--type-only"]
    labels = load_labels(Path(args[0]))
    model = load_router()
    for config_dir in args[1:]:
        report, records = evaluate(Path(config_dir), labels, model, type_only)
        name = Path(config_dir).name or "config"
        (ROOT / "data" / f"tune-{name}.json").write_text(json.dumps(records))
        (ROOT / "data" / f"tune-{name}.txt").write_text(report + "\n")
        print(report.split("\nmistakes:")[0], flush=True)


if __name__ == "__main__":
    main()
