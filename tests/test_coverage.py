from datetime import date
from zoneinfo import ZoneInfo

from triage.coverage import day_window, format_coverage, month_coverage, range_query
from triage.store import Store

IST = ZoneInfo("Asia/Kolkata")


class QueryGmail:
    def __init__(self, by_query):
        self.by_query, self.queries = by_query, []

    def list_ids(self, query, limit=None):
        self.queries.append(query)
        return self.by_query.get(query, [])


def test_day_window_uses_local_midnight():
    start, end = day_window(date(2026, 10, 1), IST)
    assert start == 1790793000  # 2026-10-01 00:00 IST = 2026-09-30 18:30 UTC
    assert end - start == 86400


def test_range_query():
    assert range_query(date(2026, 10, 1), date(2026, 10, 3), IST) == \
        f"in:inbox after:{day_window(date(2026, 10, 1), IST)[0]} before:{day_window(date(2026, 10, 3), IST)[0]}"


def test_month_coverage_counts_done_and_todo(tmp_path):
    q1 = range_query(date(2026, 10, 1), date(2026, 10, 2), IST)
    q2 = range_query(date(2026, 10, 2), date(2026, 10, 3), IST)
    gmail = QueryGmail({q1: ["a", "b"], q2: ["c"]})
    with Store(tmp_path / "s.db") as store:
        store.mark("a", "ok", ["Laya/Finance"])
        store.mark("b", "skipped", [])
        rows = month_coverage(gmail, store, "2026-10", IST, today=date(2026, 10, 2))
    assert [(r.day.day, r.inbox, r.done, r.todo) for r in rows] == [(1, 2, 2, 0), (2, 1, 0, 1)]
    report = format_coverage(rows)
    assert "still to do: 2026-10-02" in report
    assert "total           3     2      1" in report
