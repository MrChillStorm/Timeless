"""Interface tests, run offscreen on a throwaway database:
python3 -m unittest discover tests"""
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QApplication

from timeless.core import db, store
from timeless.ui.cells import CHECKED_ROLE, TOOLTIP, WARN_ROLE
from timeless.ui.dialogs import SettingsDialog
from timeless.ui.projects import K, ProjectsPage
from timeless.ui.report import ReportPage
from timeless.ui.theme import colors
from timeless.ui.week import C, WeekPage

MON = date(2026, 9, 21)  # week 39
app = QApplication.instance() or QApplication([])


class UICase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        self.work = store.Project(name="Alpha", plan=[7.5] * 5 + [0, 0])
        store.save_project(self.conn, self.work)
        self.page = WeekPage(self.conn)

    def tearDown(self):
        self.page.deleteLater()
        self.conn.close()
        self.tmp.cleanup()

    def turn_flex_on(self, start: float = 0) -> tuple[store.Kind, store.Project]:
        store.set_flex(self.conn, True)
        db.set_setting(self.conn, "flex_start", str(start))
        return (next(k for k in store.list_kinds(self.conn) if k.flex),
                next(p for p in store.list_projects(self.conn) if p.flex))

    def index(self, project_id: int, kind_id: int | None, day: int) -> QModelIndex:
        m = self.page.model
        r = m.row_of(project_id, kind_id)
        if r < 0:
            r = m.add_kind_row(project_id, kind_id)
        return m.index(r, C.D0 + day)

    def type_hours(self, project_id: int, kind_id: int | None, day: int, hours: float) -> bool:
        return self.page.model.setData(self.index(project_id, kind_id, day), hours)


class WeekTest(UICase):
    def test_typed_hours_save_and_undo(self):
        self.page.load(MON)
        self.type_hours(self.work.id, None, 0, 6)
        self.assertEqual(store.week_cells(self.conn, self.page.model.days)[(MON, self.work.id, None)], (6, ""))
        self.page.model.undo()
        self.assertEqual(store.week_cells(self.conn, self.page.model.days), {})

    def test_project_without_plan(self):
        meetings = store.Project(name="Meetings", billable=False)
        store.save_project(self.conn, meetings)
        self.page.load(MON)
        self.type_hours(self.work.id, None, 0, 7.5)
        self.type_hours(meetings.id, None, 0, 1.5)
        m = self.page.model
        self.assertEqual(m.index(m.row_of(meetings.id), C.PLAN).data(TOOLTIP),
                         "No plan this week: these hours count toward the week only.")
        self.assertIn("Includes 1,50 h on projects with no plan", m.index(len(m.rows), C.PLAN).data(TOOLTIP))


class FlexTest(UICase):
    def test_no_tally_without_flex(self):
        self.page.load(MON)
        self.assertTrue(self.page.flex_box.isHidden())
        self.assertNotIn("flex", self.page.hint.text())

    def test_tally_follows_the_week(self):
        flex, time_off = self.turn_flex_on(start=6)
        store.set_cell(self.conn, (MON - timedelta(days=4), self.work.id, flex.id), 2, "")  # the week before
        self.page.load(MON)
        self.assertFalse(self.page.flex_box.isHidden())
        self.assertIn("flex", self.page.hint.text())
        self.assertEqual(self.page.flex_value.text(), "+8,00 h")
        self.type_hours(self.work.id, None, 1, 7.5)
        self.type_hours(self.work.id, flex.id, 1, 1.5)
        self.assertEqual(self.page.flex_value.text(), "+9,50 h")
        tip = self.page.flex_box.toolTip()
        self.assertIn("at the end of week 39: +9,50 h", tip)
        self.assertIn("Before this week: +8,00 h", tip)
        self.assertIn("This week: 1,50 h earned, 0,00 h taken off", tip)

        self.type_hours(time_off.id, None, 4, 10)  # more than there is: the balance goes negative
        self.assertEqual(self.page.flex_value.text(), "−0,50 h")
        self.assertIn(colors()["warning"], self.page.flex_value.styleSheet())
        self.page.model.undo()
        self.assertEqual(self.page.flex_value.text(), "+9,50 h")
        self.assertEqual(self.page.flex_value.styleSheet(), "")

        self.page.load(MON + timedelta(weeks=1))  # later weeks carry the balance on
        self.assertEqual(self.page.flex_value.text(), "+9,50 h")
        self.page.load(MON - timedelta(weeks=2))  # earlier ones show what it was then
        self.assertEqual(self.page.flex_value.text(), "+6,00 h")

    def test_flex_row_tooltip(self):
        flex, _ = self.turn_flex_on()
        self.page.load(MON)
        self.type_hours(self.work.id, None, 0, 7.5)
        self.type_hours(self.work.id, flex.id, 0, 1)
        m = self.page.model
        self.assertIn("flex balance", m.index(m.row_of(self.work.id, flex.id), C.NAME).data(TOOLTIP))

    def test_flex_goes_on_top_of_a_full_day(self):
        flex, time_off = self.turn_flex_on()
        meetings = store.Project(name="Meetings", billable=False)
        store.save_project(self.conn, meetings)
        self.page.load(MON)
        messages = []
        self.page.message.connect(messages.append)
        m = self.page.model

        self.type_hours(self.work.id, None, 0, 6)
        self.assertFalse(self.type_hours(self.work.id, flex.id, 0, 1))
        self.assertIn("Monday has 6,00 h of other work, and a day is 7,50 h", messages[-1])
        self.assertFalse(m.nudge(self.index(self.work.id, flex.id, 0), 0.5))  # scrolling too
        self.assertIn("a full day", m.index(m.row_of(self.work.id, flex.id), C.D0).data(TOOLTIP))

        self.type_hours(time_off.id, None, 0, 1.5)  # time off isn't work
        self.assertFalse(self.type_hours(self.work.id, flex.id, 0, 1))
        self.type_hours(meetings.id, None, 0, 1.5)  # any other project is
        self.assertTrue(self.type_hours(self.work.id, flex.id, 0, 1))
        self.assertTrue(m.nudge(self.index(self.work.id, flex.id, 0), 1.5))

        self.assertTrue(self.type_hours(self.work.id, flex.id, 5, 3))  # a Saturday is all extra
        self.assertTrue(self.type_hours(self.work.id, None, 0, 5))     # the day can still shrink...
        flex_cell = self.index(self.work.id, flex.id, 0)
        self.assertTrue(flex_cell.data(WARN_ROLE))                     # ...and the flex hours show it
        self.assertTrue(self.type_hours(self.work.id, flex.id, 0, 1))  # fewer are always fine
        self.assertFalse(self.type_hours(self.work.id, flex.id, 0, 2))

    def test_full_day_setting(self):
        dialog = SettingsDialog(None, self.conn)
        self.assertEqual(dialog.full_day.value(), 7.5)
        dialog.full_week.setValue(40)  # the day follows the week...
        self.assertEqual((dialog.full_day.value(), store.full_day(self.conn)), (8, 8))
        dialog.full_day.setValue(7.25)
        dialog.full_week.setValue(30)  # ...until it's set apart, e.g. four days a week
        self.assertEqual(store.full_day(self.conn), 7.25)
        flex, _ = self.turn_flex_on()
        self.page.load(MON)
        self.type_hours(self.work.id, None, 0, 7.25)
        self.assertTrue(self.type_hours(self.work.id, flex.id, 0, 1))
        dialog.deleteLater()

    def test_settings_turn_flex_on_and_off(self):
        dialog = SettingsDialog(None, self.conn)
        dialog.changed.connect(self.page.load)
        self.page.load(MON)
        self.assertFalse(dialog.flex_start.isEnabled())
        dialog.flex.setChecked(True)
        self.assertTrue(dialog.flex_start.isEnabled())
        self.assertFalse(self.page.flex_box.isHidden())
        self.assertIn("Flex time off", [r.project.name for r in self.page.model.rows])
        dialog.flex_start.setValue(-3)
        self.assertEqual(store.flex_start(self.conn), -3)
        self.assertEqual(self.page.flex_value.text(), "−3,00 h")
        dialog.flex.setChecked(False)
        self.assertTrue(self.page.flex_box.isHidden())
        self.assertNotIn("Flex time off", [r.project.name for r in self.page.model.rows])
        dialog.deleteLater()

    def test_kinds_flex_column(self):
        page = ProjectsPage(self.conn)
        page.load()
        self.assertTrue(page.kind_view.isColumnHidden(K.FLEX))
        self.turn_flex_on()
        store.save_kind(self.conn, store.Kind(name="Overtime"))
        page.load()
        self.assertFalse(page.kind_view.isColumnHidden(K.FLEX))
        r = next(i for i, k in enumerate(page.kinds.kinds) if k.name == "Overtime")
        self.assertTrue(page.kinds.setData(page.kinds.index(r, K.FLEX), True, CHECKED_ROLE))
        self.assertTrue(next(k for k in store.list_kinds(self.conn) if k.name == "Overtime").flex)
        page.deleteLater()

    def test_report_shows_the_flex_change(self):
        flex, time_off = self.turn_flex_on()
        store.set_cell(self.conn, (MON, self.work.id, flex.id), 2, "")
        store.set_cell(self.conn, (MON + timedelta(days=4), time_off.id, None), 0.5, "")
        page = ReportPage(self.conn)
        page.set_range(MON, MON + timedelta(days=6))
        self.assertIn("flex +1,50 h (2,00 earned, 0,50 taken off)", page.summary.text())
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
