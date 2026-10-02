"""Dev tool: save the raw Gmail messages listed in an eval CSV to data/cache/<id>.json
so tuning runs don't refetch them. Read-only. Run on the host:
    .venv/bin/python scripts/cache_messages.py data/eval-XXXX.csv
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from triage.gmail_client import GmailClient  # noqa: E402


def main(csv_path: str) -> None:
    cache = ROOT / "data" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    with open(csv_path, newline="") as fh:
        ids = [row["msg_id"] for row in csv.DictReader(fh)]
    gmail = GmailClient.from_token(ROOT / "secrets" / "token.json", read_only=True)
    fetched = 0
    for msg_id in ids:
        path = cache / f"{msg_id}.json"
        if path.exists():
            continue
        path.write_text(json.dumps(gmail.get(msg_id)))
        fetched += 1
    print(f"cached {fetched} new, {len(ids)} total in {cache}")


if __name__ == "__main__":
    main(sys.argv[1])
