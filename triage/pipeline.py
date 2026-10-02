"""One labelling pass: fetch new inbox mail, classify it, add labels."""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from triage.classifier import Classifier, LayaResult
from triage.config import Config
from triage.decide import Decision, decide
from triage.extract import Features, parse_message, to_state
from triage.gmail_client import AuthError
from triage.rules import SUSPICIOUS, RuleHits, apply_rules
from triage.store import Store

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
LEARN_WINDOW_SECONDS = 3 * 86400  # how far back to look for the owner's label corrections
OVERLAP_SECONDS = 3600
FIRST_RUN_LOOKBACK_SECONDS = 86400
BREAKER_MIN_ERRORS = 3
SKIP_SYSTEM_LABELS = {"SPAM", "TRASH", "SENT"}


@dataclass(frozen=True)
class Outcome:
    features: Features
    hits: RuleHits
    laya: LayaResult
    decision: Decision


def classify_message(raw: dict, cfg: Config, classifier: Classifier,
                     recall: Callable[[str], str | None] | None = None) -> Outcome:
    """Auth rules first, then the owner's remembered type for this sender, then Laya."""
    features = parse_message(raw)
    hits = apply_rules(features, cfg.vip)
    known = recall(features.from_email) if recall and hits.forced_type is None else None
    if known not in cfg.type_criteria:
        known = None
    # Laya cannot tell phishing from text (banks quote anti-scam warnings), so a
    # verified sender is never offered "suspicious"; the auth rules handle spoofing.
    exclude = frozenset({SUSPICIOUS}) if features.verified else frozenset()
    laya = classifier.classify(to_state(features, cfg.body_limit), skip_type=hits.forced_type is not None,
                                skip_action_types=cfg.no_priority_types, exclude_types=exclude,
                                known_type=known)
    return Outcome(features, hits, laya, decide(laya, hits, cfg))


def should_skip(raw: dict, laya_label_ids: set[str]) -> bool:
    label_ids = set(raw.get("labelIds", []))
    return bool(label_ids & SKIP_SYSTEM_LABELS or label_ids & laya_label_ids)


def build_query(last_run: int | None, now: int) -> str:
    start = last_run if last_run is not None else now - FIRST_RUN_LOOKBACK_SECONDS
    return f"in:inbox after:{start - OVERLAP_SECONDS}"


def _is_not_found(exc: Exception) -> bool:
    return isinstance(exc, HttpError) and getattr(exc.resp, "status", None) == 404


def _mark_failure(store: Store, msg_id: str, counts: dict) -> None:
    """Record one failed attempt; at the cap the message becomes terminal and stays unlabelled."""
    if store.attempts(msg_id) + 1 >= MAX_ATTEMPTS:
        store.mark(msg_id, "failed", [])
        counts["failed"] += 1
    else:
        store.mark(msg_id, "error", [])
        counts["errors"] += 1


def learn_corrections(gmail, store: Store, cfg: Config, label_map: dict[str, str], now: int) -> int:
    """Remember a sender's type when the owner swapped the Laya type label we applied."""
    type_by_label_id = {label_map[name]: t for t, name in cfg.type_labels.items() if name in label_map}
    type_by_label_name = {name: t for t, name in cfg.type_labels.items()}
    learned = 0
    for msg_id, sender, applied in store.recent_labelled(now - LEARN_WINDOW_SECONDS):
        applied_types = {type_by_label_name[n] for n in applied if n in type_by_label_name}
        try:
            current = gmail.get_label_ids(msg_id)
        except (AuthError, RefreshError):
            raise
        except Exception:
            log.debug("could not read labels of %s", msg_id, exc_info=True)
            continue
        added = {type_by_label_id[i] for i in current if i in type_by_label_id} - applied_types
        if len(added) == 1 and sender:
            new_type = added.pop()
            store.remember_sender(sender, new_type)
            store.set_labels(msg_id, [n for n in applied if n not in type_by_label_name]
                             + [cfg.type_labels[new_type]])
            log.info("learned: %s -> %s", sender, new_type)
            learned += 1
    return learned


def run_once(gmail, store: Store, cfg: Config, classifier: Classifier, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    run_id = uuid.uuid4().hex[:8]
    label_map = gmail.ensure_labels(cfg.all_label_names())
    laya_label_ids = set(label_map.values())
    try:
        learned = learn_corrections(gmail, store, cfg, label_map, now)
    except (AuthError, RefreshError):
        raise
    except Exception:
        log.exception("learning from label corrections failed; continuing")
        learned = 0
    groups: dict[tuple[str, ...], list[Outcome]] = defaultdict(list)
    counts = {"labelled": 0, "skipped": 0, "errors": 0, "failed": 0}
    failures: list[str] = []
    new_attempted = 0

    retry_ids = store.retry_ids()
    retry_set = set(retry_ids)
    candidates = dict.fromkeys(gmail.list_ids(build_query(store.get_last_run(), now)) + retry_ids)
    for msg_id in candidates:
        if store.is_done(msg_id):
            continue
        if msg_id not in retry_set:
            new_attempted += 1
        try:
            raw = gmail.get(msg_id)
            if should_skip(raw, laya_label_ids):
                store.mark(msg_id, "skipped", [])
                counts["skipped"] += 1
                continue
            outcome = classify_message(raw, cfg, classifier, store.recall_sender)
            names = outcome.decision.label_names(cfg)
            groups[tuple(names)].append(outcome)
            log.info("%s -> %s (type conf %.2f)", msg_id, ", ".join(names), outcome.laya.type_conf)
        except (AuthError, RefreshError):
            raise
        except Exception as exc:
            if _is_not_found(exc):
                log.warning("%s no longer exists, skipping", msg_id)
                store.mark(msg_id, "skipped", [])
                counts["skipped"] += 1
                continue
            log.exception("failed on %s", msg_id)
            failures.append(msg_id)

    # The breaker judges only new candidates; messages already in retry always get their bump.
    new_failures = [m for m in failures if m not in retry_set]
    aborted = len(new_failures) >= BREAKER_MIN_ERRORS and len(new_failures) * 2 > new_attempted
    if aborted:
        log.warning("circuit breaker: %d/%d failed, aborting pass", len(new_failures), new_attempted)
    for msg_id in failures:
        if aborted and msg_id not in retry_set:
            counts["errors"] += 1
        else:
            _mark_failure(store, msg_id, counts)

    failed_groups: list[tuple[tuple[str, ...], list[str]]] = []
    for names, outcomes in groups.items():
        msg_ids = [o.features.msg_id for o in outcomes]
        try:
            gmail.add_labels(msg_ids, [label_map[name] for name in names])
        except (AuthError, RefreshError):
            raise
        except Exception:
            log.exception("add_labels failed for %s", ", ".join(names))
            failed_groups.append((names, msg_ids))
            continue
        for o in outcomes:
            store.log_prediction(run_id, o.features, o.laya, o.hits, list(names))
            store.mark(o.features.msg_id, "ok", list(names))
        counts["labelled"] += len(msg_ids)

    if failed_groups and len(failed_groups) == len(groups):
        log.warning("add_labels failed for every group (%d); treating as a Gmail outage, "
                    "attempts not bumped", len(groups))
        counts["errors"] += sum(len(ids) for _, ids in failed_groups)
        aborted = True
    else:
        for _, msg_ids in failed_groups:
            for msg_id in msg_ids:
                _mark_failure(store, msg_id, counts)

    if not aborted:
        store.set_last_run(now)
    return {**counts, "aborted": aborted, "learned": learned}
