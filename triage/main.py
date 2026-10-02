"""CLI: eval | score | once | run."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from google.auth.exceptions import RefreshError

from triage.classifier import Classifier, load_router
from triage.config import load_config
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
    sub.add_parser("once", help="label new mail once")
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
            time.sleep(AUTH_RETRY_SECONDS)


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
                 else GmailClient.from_token(token, read_only=args.cmd == "eval"))
        classifier = Classifier(cfg, load_router())
        if args.cmd == "eval":
            out = APP_DIR / "data" / f"eval-{time.strftime('%Y%m%d-%H%M%S')}.csv"
            n = run_eval(gmail, cfg, classifier, out, limit=args.limit, from_csv=args.from_csv)
            print(f"Wrote {n} rows to {out.relative_to(APP_DIR)}")
            return 0
        with Store(APP_DIR / "data" / "state.db") as store:
            if args.cmd == "once":
                log.info("done: %s", run_once(gmail, store, cfg, classifier))
                return 0
            while True:
                try:
                    log.info("done: %s", run_once(gmail, store, cfg, classifier))
                    time.sleep(cfg.interval_minutes * 60)
                except (AuthError, RefreshError) as exc:
                    log.critical("Gmail auth problem: %s; re-run auth.py on the host", exc)
                    time.sleep(AUTH_RETRY_SECONDS)
                    gmail = _connect_forever(token)
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
