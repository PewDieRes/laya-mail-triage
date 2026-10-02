import importlib.util
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

spec = importlib.util.spec_from_file_location(
    "backfill_months", Path(__file__).resolve().parent.parent / "scripts" / "backfill_months.py")
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


class OldestGmail:
    def __init__(self):
        self.queries = []

    def list_ids(self, query, limit=None):
        self.queries.append(query)
        return ["new", "old"]

    def get(self, msg_id):
        return {"internalDate": "1664784148000"}  # 2022-10-03 13:32 IST


def test_oldest_inbox_month_has_no_negative_after_bound():
    gmail = OldestGmail()
    assert backfill.oldest_inbox_month(gmail, date(2026, 2, 1), ZoneInfo("Asia/Kolkata")) == (2022, 10)
    assert "after:" not in gmail.queries[0] and gmail.queries[0].startswith("in:inbox before:")


def test_previous_month_wraps_year():
    assert backfill.previous(2026, 1) == (2025, 12)
    assert backfill.previous(2026, 3) == (2026, 2)
