"""Core tests: python3 -m unittest discover tests"""
import csv
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timeless.core import db, report, store
from timeless.core.calendar import (
    fmt_hours, fmt_percent, fmt_signed, holiday_name, is_day_off, is_public_holiday, iso_week, parse_hours, step_half_hours,
    week_capacity, week_days, week_label, week_range, week_start,
)

MON = date(2026, 9, 21)  # week 39


class CalendarTest(unittest.TestCase):
    def test_weeks(self):
        self.assertEqual(week_start(date(2026, 9, 27)), MON)
        self.assertEqual(week_start(MON), MON)
        self.assertEqual(week_label(MON, today=date(2026, 9, 24)), "Week 39")
        self.assertEqual(week_label(date(2027, 1, 4), today=date(2026, 9, 24)), "Week 1, 2027")
        self.assertEqual(week_label(date(2026, 12, 28), today=date(2026, 9, 24)), "Week 53")
        self.assertEqual(iso_week(date(2026, 12, 28)), "2026-W53")
        self.assertEqual(week_range(MON), "21.9.–27.9.2026")

    def test_hours_and_percent(self):
        for text, hours in (("7,5", 7.5), ("7.25", 7.25), ("7:30", 7.5), ("8h", 8.0), (",5", 0.5), ("", 0.0)):
            self.assertEqual(parse_hours(text), hours, text)
        for bad in ("abc", "25", "1:75", ",", "1.234"):
            with self.assertRaises(ValueError, msg=bad):
                parse_hours(bad)
        self.assertEqual(fmt_hours(1.5), "1,50")
        self.assertEqual(fmt_percent(20.5, 39.5), "52 %")
        self.assertEqual(fmt_percent(0.1, 40), "<1 %")
        self.assertEqual(fmt_percent(3, 0), "")
        self.assertEqual((fmt_signed(3.5), fmt_signed(-2), fmt_signed(0.001)), ("+3,50", "−2,00", "0,00"))

    def test_half_hour_steps(self):
        self.assertEqual(step_half_hours(7.5, 1), 8.0)
        self.assertEqual(step_half_hours(7.5, -3), 6.0)
        self.assertEqual(step_half_hours(7.25, 1), 7.5)   # odd values snap in the scroll direction
        self.assertEqual(step_half_hours(7.25, -1), 7.0)
        self.assertEqual(step_half_hours(0, -1), 0.0)     # never below zero
        self.assertEqual(step_half_hours(23.5, 5), 24.0)  # or over 24

    def test_holidays_and_capacity(self):
        self.assertTrue(is_public_holiday(date(2026, 12, 25)))
        self.assertTrue(is_day_off(date(2026, 12, 24)) and not is_public_holiday(date(2026, 12, 24)))
        self.assertIsNotNone(holiday_name(date(2026, 4, 4)))  # Easter Saturday
        self.assertEqual(week_capacity(MON), 37.5)
        self.assertEqual(week_capacity(date(2026, 12, 21)), 22.5)
        self.assertEqual(week_capacity(MON, 40), 40)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def project(self, name="Alpha", **kw) -> store.Project:
        p = store.Project(name=name, **kw)
        store.save_project(self.conn, p)
        return p


class StoreTest(StoreCase):
    def test_project_rules(self):
        p = self.project(plan=[4, 4, 4, 4, 4, 0, 0])
        with self.assertRaises(store.StoreError):
            self.project("alpha")
        self.assertEqual(p.plan_for(date(2026, 12, 24)), 0.0)  # holiday
        self.assertEqual(p.plan_for(MON), 4.0)
        store.set_cell(self.conn, (MON, p.id, None), 2, "")
        with self.assertRaises(store.StoreError):
            store.delete_project(self.conn, p.id)
        p.archived = True
        store.save_project(self.conn, p)
        self.assertEqual(store.list_projects(self.conn), [])
        self.assertEqual(len(store.list_projects(self.conn, include_archived=True)), 1)

    def test_cells_write_through(self):
        p = self.project()
        oncall = store.Kind(name="On-call")
        store.save_kind(self.conn, oncall)
        days = week_days(MON)
        store.set_cell(self.conn, (MON, p.id, None), 7.5, "Workshop")
        store.set_cell(self.conn, (MON, p.id, oncall.id), 2, "")
        store.set_cell(self.conn, (MON, p.id, None), 8, "Workshop")  # update in place
        cells = store.week_cells(self.conn, days)
        self.assertEqual(cells[(MON, p.id, None)], (8, "Workshop"))
        self.assertEqual(cells[(MON, p.id, oncall.id)], (2, ""))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 2)
        store.set_cell(self.conn, (MON, p.id, None), 0, "")  # emptied -> gone
        self.assertNotIn((MON, p.id, None), store.week_cells(self.conn, days))
        with self.assertRaises(store.StoreError):
            store.delete_kind(self.conn, oncall.id)

    def test_kind_billing(self):
        p = self.project(billable=True)
        self.assertTrue(store.Kind(billing="project").billable_for(p))
        self.assertFalse(store.Kind(billing="non_billable").billable_for(p))
        with self.assertRaises(store.StoreError):
            store.save_kind(self.conn, store.Kind(name="X", billing="sometimes"))

    def test_done_weeks(self):
        self.assertFalse(store.is_done(self.conn, MON))
        store.set_done(self.conn, MON, True)
        self.assertTrue(store.is_done(self.conn, MON))
        store.set_done(self.conn, MON, False)
        self.assertFalse(store.is_done(self.conn, MON))


class FlexTest(StoreCase):
    def test_turning_flex_on_and_off(self):
        self.assertFalse(store.flex_enabled(self.conn))
        store.set_flex(self.conn, True)
        self.assertTrue(store.flex_enabled(self.conn))
        [kind] = [k for k in store.list_kinds(self.conn) if k.flex]
        [off] = [p for p in store.list_projects(self.conn) if p.flex]
        self.assertEqual((kind.name, kind.billing, off.name, off.billable), ("Flex", "project", "Flex time off", False))
        store.set_flex(self.conn, True)  # twice changes nothing
        self.assertEqual(len(store.list_kinds(self.conn)), 1)
        store.set_flex(self.conn, False)  # never used: both go
        self.assertEqual((store.list_kinds(self.conn), store.list_projects(self.conn, include_archived=True)), ([], []))

    def test_used_flex_is_kept(self):
        store.set_flex(self.conn, True)
        off = next(p for p in store.list_projects(self.conn) if p.flex)
        store.set_cell(self.conn, (MON, off.id, None), 2, "")
        store.set_flex(self.conn, False)
        [kept] = store.list_projects(self.conn, include_archived=True)
        self.assertTrue(kept.archived and kept.flex)
        store.set_flex(self.conn, True)  # back again, with its history
        self.assertEqual([p.id for p in store.list_projects(self.conn)], [off.id])

    def test_takes_over_existing_names(self):
        own = self.project("flex time off")
        store.save_kind(self.conn, store.Kind(name="FLEX", billing="billable"))
        store.set_flex(self.conn, True)
        self.assertEqual([p.id for p in store.list_projects(self.conn) if p.flex], [own.id])
        self.assertEqual([(k.name, k.billing, k.flex) for k in store.list_kinds(self.conn)], [("FLEX", "billable", True)])

    def test_balance(self):
        store.set_flex(self.conn, True)
        db.set_setting(self.conn, "flex_start", "-1.5")
        work = self.project("Alpha")
        flex = next(k for k in store.list_kinds(self.conn) if k.flex)
        off = next(p for p in store.list_projects(self.conn) if p.flex)
        oncall = store.Kind(name="On-call")
        store.save_kind(self.conn, oncall)
        store.set_cell(self.conn, (date(2026, 9, 14), work.id, flex.id), 2, "")
        store.set_cell(self.conn, (MON, work.id, flex.id), 1.5, "")
        store.set_cell(self.conn, (MON, work.id, oncall.id), 3, "")    # other kinds don't count
        store.set_cell(self.conn, (MON, work.id, None), 7.5, "")
        store.set_cell(self.conn, (date(2026, 9, 25), off.id, None), 4, "")
        self.assertEqual(store.flex_between(self.conn, None, MON - date.resolution), (2, 0))
        self.assertEqual(store.flex_between(self.conn, MON, date(2026, 9, 27)), (1.5, 4))
        earned, used = store.flex_between(self.conn, None, date(2026, 9, 27))
        self.assertEqual(store.flex_start(self.conn) + earned - used, -2.0)

    def test_older_database_gets_the_flex_columns(self):
        path = Path(self.tmp.name) / "old.db"
        old = sqlite3.connect(path)
        old.executescript("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT NOT NULL DEFAULT '', "
                          "client TEXT NOT NULL DEFAULT '', billable INTEGER NOT NULL DEFAULT 1, "
                          + "".join(f"{c} REAL NOT NULL DEFAULT 0, " for c in store.PLAN_COLUMNS)
                          + "notes TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0, "
                          "created_at TEXT NOT NULL);"
                          "CREATE TABLE kinds (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                          "billing TEXT NOT NULL DEFAULT 'project', created_at TEXT NOT NULL);"
                          "INSERT INTO projects (name, created_at) VALUES ('Alpha', 'x');"
                          "INSERT INTO kinds (name, created_at) VALUES ('On-call', 'x');")
        old.close()
        conn = db.connect(path)
        self.assertFalse(store.list_projects(conn)[0].flex)
        self.assertFalse(store.list_kinds(conn)[0].flex)
        conn.close()


class ReportTest(StoreCase):
    def test_summary_and_csv(self):
        a = self.project("Alpha", code="TMP-001")
        internal = self.project("Internal", billable=False)
        travel = store.Kind(name="Travel", billing="non_billable")
        store.save_kind(self.conn, travel)
        store.set_cell(self.conn, (MON, a.id, None), 6, "Kickoff; prep")
        store.set_cell(self.conn, (MON, a.id, travel.id), 1.5, "")
        store.set_cell(self.conn, (date(2026, 9, 27), internal.id, None), 2, "")
        store.set_done(self.conn, MON, True)
        entries = report.entries_between(self.conn, MON, date(2026, 9, 27))
        totals = {t.project: t for t in report.summarize(entries)}
        self.assertEqual((totals["Alpha"].billable, totals["Alpha"].non_billable), (6, 1.5))
        self.assertEqual(totals["Alpha"].kinds, {"Travel": 1.5})
        self.assertEqual(totals["Internal"].non_billable, 2)
        self.assertTrue(all(e.done for e in entries))  # Sunday belongs to the same done week

        out = Path(self.tmp.name) / "out.csv"
        report.export_csv(out, entries, "Test User")
        with open(out, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f, delimiter=";"))
        self.assertEqual(rows[0][:4], ["Date", "Weekday", "Week", "Person"])
        self.assertEqual(rows[1][:6], ["21.9.2026", "Monday", "2026-W39", "Test User", "TMP-001", "Alpha"])
        self.assertEqual(rows[1][9:12], ["6,00", "Kickoff; prep", "Yes"])

    def test_daily_backup(self):
        backups = Path(self.tmp.name) / "backups"
        self.assertIsNone(db.daily_backup(self.conn, MON, backups))  # nothing yet
        self.project()
        self.assertTrue(db.daily_backup(self.conn, MON, backups).exists())
        self.assertIsNone(db.daily_backup(self.conn, MON, backups))


if __name__ == "__main__":
    unittest.main()
