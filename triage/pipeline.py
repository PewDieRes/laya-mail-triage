"""One labelling pass: fetch new inbox mail, classify it, add labels."""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass

from google.auth.exceptions import RefreshError

from triage.classifier import Classifier, LayaResult
from triage.config import Config
from triage.decide import Decision, decide
from triage.extract import Features, parse_message, to_state
from triage.gmail_client import AuthError
from triage.rules import RuleHits, apply_rules
from triage.store import Store

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
OVERLAP_SECONDS = 3600
FIRST_RUN_LOOKBACK_SECONDS = 86400
SKIP_SYSTEM_LABELS = {"SPAM", "TRASH", "SENT"}


@dataclass(frozen=True)
class Outcome:
    features: Features
    hits: RuleHits
    laya: LayaResult
    decision: Decision


def classify_message(raw: dict, cfg: Config, classifier: Classifier) -> Outcome:
    features = parse_message(raw)
    hits = apply_rules(features, cfg.vip)
    laya = classifier.classify(to_state(features), skip_type=hits.forced_type is not None)
    return Outcome(features, hits, laya, decide(laya, hits, cfg))


def should_skip(raw: dict, laya_label_ids: set[str]) -> bool:
    label_ids = set(raw.get("labelIds", []))
    return bool(label_ids & SKIP_SYSTEM_LABELS or label_ids & laya_label_ids)


def build_query(last_run: int | None, now: int) -> str:
    start = last_run if last_run is not None else now - FIRST_RUN_LOOKBACK_SECONDS
    return f"in:inbox after:{start - OVERLAP_SECONDS}"


def run_once(gmail, store: Store, cfg: Config, classifier: Classifier, now: int | None = None) -> dict[str, int]:
    now = int(time.time()) if now is None else now
    run_id = uuid.uuid4().hex[:8]
    label_map = gmail.ensure_labels(cfg.all_label_names())
    laya_label_ids = set(label_map.values())
    unsure = cfg.priority_labels["unsure"]
    groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    counts = {"labelled": 0, "skipped": 0, "errors": 0}

    candidates = dict.fromkeys(gmail.list_ids(build_query(store.get_last_run(), now)) + store.retry_ids())
    for msg_id in candidates:
        if store.is_done(msg_id):
            continue
        try:
            raw = gmail.get(msg_id)
            if should_skip(raw, laya_label_ids):
                store.mark(msg_id, "skipped", [])
                counts["skipped"] += 1
                continue
            outcome = classify_message(raw, cfg, classifier)
            names = outcome.decision.label_names(cfg)
            store.log_prediction(run_id, outcome.features, outcome.laya, outcome.hits, names)
            groups[tuple(names)].append(msg_id)
            log.info("%s -> %s (type conf %.2f)", msg_id, ", ".join(names), outcome.laya.type_conf)
        except (AuthError, RefreshError):
            raise
        except Exception:
            log.exception("failed on %s", msg_id)
            store.mark(msg_id, "error", [])
            counts["errors"] += 1
            if store.attempts(msg_id) >= MAX_ATTEMPTS:
                groups[(unsure,)].append(msg_id)

    for names, msg_ids in groups.items():
        gmail.add_labels(msg_ids, [label_map[name] for name in names])
        for msg_id in msg_ids:
            store.mark(msg_id, "ok", list(names))
        counts["labelled"] += len(msg_ids)

    store.set_last_run(now)
    return counts
