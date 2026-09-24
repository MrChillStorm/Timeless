"""Hours for a date range: per project (and kind), and as CSV for
spreadsheets, invoicing or another time system."""
import csv
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from timeless.core.calendar import DAY_TITLES, fmt_date, fmt_hours, iso_week, week_start

# key: (label, delimiter, decimal comma, Finnish dates, encoding)
CSV_FORMATS = {
    "excel_fi": ("Excel, Finnish (semicolons, 7,50, 27.9.2026)", ";", True, True, "utf-8-sig"),
    "standard": ("Standard CSV (commas, 7.50, 2026-09-27)", ",", False, False, "utf-8"),
}
CSV_COLUMNS = ("Date", "Weekday", "Week", "Person", "Code", "Project", "Client", "Kind", "Billable", "Hours",
               "Note", "Week done")


@dataclass
class Entry:
    day: date
    project_id: int
    project: str
    code: str
    client: str
    kind: str
    billable: bool
    hours: float
    note: str
    done: bool


@dataclass
class ProjectTotal:
    project: str
    code: str
    billable: float = 0.0
    non_billable: float = 0.0
    kinds: dict[str, float] = field(default_factory=dict)  # hours on special kinds, by name

    @property
    def total(self) -> float:
        return round(self.billable + self.non_billable, 2)


def entries_between(conn: sqlite3.Connection, start: date, end: date) -> list[Entry]:
    rows = conn.execute(
        """SELECT e.day, e.hours, e.note, p.id AS pid, p.name, p.code, p.client, p.billable,
                  k.name AS kind, k.billing, d.week IS NOT NULL AS done
           FROM entries e
           JOIN projects p ON p.id = e.project_id
           LEFT JOIN kinds k ON k.id = e.kind_id
           -- the entry's Monday: back six days, then forward to a Monday
           LEFT JOIN done_weeks d ON d.week = date(e.day, '-6 days', 'weekday 1')
           WHERE e.day BETWEEN ? AND ?
           ORDER BY e.day, p.name COLLATE NOCASE, k.name""",
        (start.isoformat(), end.isoformat()),
    )
    out = []
    for r in rows:
        billing = r["billing"] or "project"
        billable = bool(r["billable"]) if billing == "project" else billing == "billable"
        out.append(Entry(date.fromisoformat(r["day"]), r["pid"], r["name"], r["code"], r["client"], r["kind"] or "",
                         billable, r["hours"], r["note"], bool(r["done"])))
    return out


def summarize(entries: list[Entry]) -> list[ProjectTotal]:
    totals: dict[int, ProjectTotal] = {}
    for e in entries:
        t = totals.setdefault(e.project_id, ProjectTotal(e.project, e.code))
        if e.billable:
            t.billable = round(t.billable + e.hours, 2)
        else:
            t.non_billable = round(t.non_billable + e.hours, 2)
        if e.kind:
            t.kinds[e.kind] = round(t.kinds.get(e.kind, 0.0) + e.hours, 2)
    return sorted(totals.values(), key=lambda t: t.project.lower())


def export_csv(path: Path, entries: list[Entry], person: str, fmt: str = "excel_fi") -> None:
    _label, delimiter, decimal_comma, finnish_dates, encoding = CSV_FORMATS[fmt]

    def day(d: date) -> str:
        return fmt_date(d) if finnish_dates else d.isoformat()

    with open(path, "w", newline="", encoding=encoding) as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(CSV_COLUMNS)
        for e in entries:
            writer.writerow([
                day(e.day), DAY_TITLES[e.day.weekday()], iso_week(week_start(e.day)), person, e.code, e.project,
                e.client, e.kind, "Yes" if e.billable else "No", fmt_hours(e.hours) if decimal_comma else f"{e.hours:.2f}",
                e.note, "Yes" if e.done else "No",
            ])


def month_range(d: date) -> tuple[date, date]:
    first = d.replace(day=1)
    return first, (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
