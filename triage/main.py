"""CLI: eval | score | once | run."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.exceptions import RefreshError

from triage.classifier import Classifier, load_router
from triage.config import load_config
from triage.coverage import format_coverage, month_coverage, range_query
from triage.evaluate import run_eval, score_csv
from triage.gmail_client import AuthError, GmailClient
from triage.pipeline import run_once
from triage.store import Store

APP_DIR = Path(__file__).resolve().parent.parent
log = logging.getLogger("triage")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="triage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("eval", help="read-only: classify inbox mail into data/eval-*.csv")
    ev.add_argument("--limit", type=int, default=500)
    ev.add_argument("--from", dest="from_csv", type=Path,
                    help="re-classify the emails in a labelled CSV, keeping true_* columns "
                         "(--limit is ignored with --from)")
    sc = sub.add_parser("score", help="accuracy report for a labelled eval CSV")
    sc.add_argument("csv", type=Path)
    once = sub.add_parser("once", help="label new mail once")
    once.add_argument("--latest", type=int, help="label the newest N inbox emails instead of mail since the last run")
    once.add_argument("--after", type=date.fromisoformat, help="label inbox mail received on/after this day (YYYY-MM-DD)")
    once.add_argument("--before", type=date.fromisoformat, help="...and before this day (exclusive, YYYY-MM-DD)")
    cov = sub.add_parser("coverage", help="read-only: per-day labelled vs still-to-do for a month")
    cov.add_argument("--month", required=True, help="YYYY-MM")
    sub.add_parser("run", help="label new mail every interval_minutes")
    return parser


AUTH_RETRY_SECONDS = 3600


def _connect_forever(token: Path) -> GmailClient:
    """run mode only: a bad token must never exit (Docker would restart-loop); wait and retry."""
    while True:
        try:
            return GmailClient.from_token(token, read_only=False)
        except (AuthError, RefreshError) as exc:
            log.critical("Gmail auth problem: %s; re-run auth.py on the host", exc)
        except Exception as exc:
            log.error("Gmail connect failed: %s", exc)
        time.sleep(AUTH_RETRY_SECONDS)


def _tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TZ") or "UTC")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.cmd == "score":
        print(score_csv(args.csv))
        return 0

    for noisy in ("googleapiclient", "httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    token = APP_DIR / "secrets" / "token.json"
    try:
        cfg = load_config(APP_DIR / "config")
        gmail = (_connect_forever(token) if args.cmd == "run"
                 else GmailClient.from_token(token, read_only=args.cmd in ("eval", "coverage")))
        if args.cmd == "coverage":
            with Store(APP_DIR / "data" / "state.db") as store:
                print(format_coverage(month_coverage(gmail, store, args.month, _tz())))
            return 0
        classifier = Classifier(cfg, load_router())
        if args.cmd == "eval":
            out = APP_DIR / "data" / f"eval-{time.strftime('%Y%m%d-%H%M%S')}.csv"
            n = run_eval(gmail, cfg, classifier, out, limit=args.limit, from_csv=args.from_csv)
            print(f"Wrote {n} rows to {out.relative_to(APP_DIR)}")
            return 0
        with Store(APP_DIR / "data" / "state.db") as store:
            if args.cmd == "once":
                query = None
                if args.after or args.before:
                    if not (args.after and args.before):
                        raise ValueError("--after and --before go together")
                    query = range_query(args.after, args.before, _tz())
                log.info("done: %s", run_once(gmail, store, cfg, classifier, latest=args.latest, query=query))
                return 0
            while True:
                if gmail is None:
                    gmail = _connect_forever(token)
                try:
                    log.info("done: %s", run_once(gmail, store, cfg, classifier))
                    time.sleep(cfg.interval_minutes * 60)
                except (AuthError, RefreshError) as exc:
                    log.critical("Gmail auth problem: %s; re-run auth.py on the host", exc)
                    time.sleep(AUTH_RETRY_SECONDS)
                    gmail = None
                except Exception:
                    log.exception("cycle failed; retrying next interval")
                    time.sleep(cfg.interval_minutes * 60)
    except KeyboardInterrupt:
        log.info("interrupted; exiting")
        return 0
    except (AuthError, RefreshError) as exc:
        log.error("Gmail auth problem: %s", exc)
        return 2
    except Exception:
        log.exception("fatal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
