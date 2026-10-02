"""Which days of a month are labelled and which are still to do (read-only)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from triage.store import Store


@dataclass(frozen=True)
class DayCoverage:
    day: date
    inbox: int
    done: int

    @property
    def todo(self) -> int:
        return self.inbox - self.done


def day_window(day: date, tz: ZoneInfo) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


def range_query(after: date, before: date, tz: ZoneInfo) -> str:
    """Gmail query for inbox mail received on or after `after` and before `before` (local days)."""
    return f"in:inbox after:{day_window(after, tz)[0]} before:{day_window(before, tz)[0]}"


def month_coverage(gmail, store: Store, month: str, tz: ZoneInfo, today: date | None = None) -> list[DayCoverage]:
    year, mon = (int(x) for x in month.split("-"))
    day, last = date(year, mon, 1), (today or datetime.now(tz).date())
    rows = []
    while day.month == mon and day <= last:
        ids = gmail.list_ids(range_query(day, day + timedelta(days=1), tz))
        rows.append(DayCoverage(day, len(ids), sum(store.is_done(i) for i in ids)))
        day += timedelta(days=1)
    return rows


def format_coverage(rows: list[DayCoverage]) -> str:
    lines = ["date        inbox  done  to do"]
    for r in rows:
        mark = "" if r.todo == 0 else "  <-"
        lines.append(f"{r.day}  {r.inbox:>5} {r.done:>5} {r.todo:>6}{mark}")
    inbox, done = sum(r.inbox for r in rows), sum(r.done for r in rows)
    lines.append(f"total       {inbox:>5} {done:>5} {inbox - done:>6}")
    todo_days = [r.day for r in rows if r.todo]
    lines.append("still to do: " + (", ".join(str(d) for d in todo_days) if todo_days else "nothing"))
    return "\n".join(lines)
