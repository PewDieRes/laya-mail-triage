"""Label the inbox one month at a time, newest first, until the oldest inbox email.
Loads Laya once. Progress (one coverage line per month) goes to data/backfill-progress.txt.
    docker compose run --rm triage python scripts/backfill_months.py 2026-01 [--also 2026-10]
"""
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from triage.classifier import Classifier, load_router  # noqa: E402
from triage.config import load_config  # noqa: E402
from triage.coverage import day_window, month_coverage, range_query  # noqa: E402
from triage.gmail_client import GmailClient  # noqa: E402
from triage.pipeline import run_once  # noqa: E402
from triage.store import Store  # noqa: E402

log = logging.getLogger("backfill")


def month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def previous(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def next_start(year: int, month: int) -> date:
    return date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)


def oldest_inbox_month(gmail, before: date, tz: ZoneInfo) -> tuple[int, int] | None:
    # No `after:` bound: an epoch for 1970-01-01 in a timezone east of UTC is negative,
    # which Gmail silently treats as matching nothing.
    ids = gmail.list_ids(f"in:inbox before:{day_window(before, tz)[0]}")
    if not ids:
        return None
    oldest = datetime.fromtimestamp(int(gmail.get(ids[-1])["internalDate"]) / 1000, tz)
    return oldest.year, oldest.month


def label_month(gmail, store, cfg, classifier, year: int, month: int, tz: ZoneInfo, progress: Path) -> None:
    counts = run_once(gmail, store, cfg, classifier,
                      query=range_query(month_start(year, month), next_start(year, month), tz))
    rows = month_coverage(gmail, store, f"{year}-{month:02d}", tz)
    inbox, done = sum(r.inbox for r in rows), sum(r.done for r in rows)
    line = (f"{year}-{month:02d}  inbox {inbox:>4}  labelled {done:>4}  to do {inbox - done:>3}  "
            f"(this pass: {counts['labelled']} labelled, {counts['errors'] + counts['failed']} errors)")
    with progress.open("a") as fh:
        fh.write(line + "\n")
    log.info(line)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("googleapiclient", "httpx", "urllib3", "httpcore", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    tz = ZoneInfo(os.environ.get("TZ") or "UTC")
    start_year, start_month = (int(x) for x in sys.argv[1].split("-"))
    extra = [tuple(int(x) for x in a.split("-")) for a in sys.argv[3:]] if "--also" in sys.argv else []
    progress = ROOT / "data" / "backfill-progress.txt"
    cfg = load_config(ROOT / "config")
    gmail = GmailClient.from_token(ROOT / "secrets" / "token.json", read_only=False)
    classifier = Classifier(cfg, load_router())
    with Store(ROOT / "data" / "state.db") as store:
        for year, month in extra:
            label_month(gmail, store, cfg, classifier, year, month, tz, progress)
        stop = oldest_inbox_month(gmail, next_start(start_year, start_month), tz)
        if stop is None:
            log.info("no inbox mail up to the end of %s-%02d; nothing to backfill", start_year, start_month)
            return
        log.info("oldest inbox email is from %s-%02d", *stop)
        year, month = start_year, start_month
        while (year, month) >= stop:
            label_month(gmail, store, cfg, classifier, year, month, tz, progress)
            year, month = previous(year, month)
        with progress.open("a") as fh:
            fh.write(f"done: reached the oldest inbox month {stop[0]}-{stop[1]:02d}\n")


if __name__ == "__main__":
    main()
