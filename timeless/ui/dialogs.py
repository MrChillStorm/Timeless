"""Settings and Help. Settings apply as you change them."""
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout,
)

from timeless.core import db
from timeless.core.calendar import DEFAULT_FULL_WEEK
from timeless.ui import theme
from timeless.ui.widgets import HoursSpin


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


class SettingsDialog(QDialog):
    changed = Signal()             # name or full week changed
    appearanceChanged = Signal()

    def __init__(self, parent, conn):
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)
        title = QLabel("Settings")
        title.setObjectName("title")
        layout.addWidget(title)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.name = QLineEdit(db.get_setting(conn, "your_name", "") or "")
        self.name.setPlaceholderText("Shown in CSV exports")
        self.name.editingFinished.connect(self._save_name)
        self.full_week = HoursSpin(maximum=80)
        self.full_week.setSuffix(" h")
        self.full_week.setFixedWidth(110)
        self.full_week.setValue(float(db.get_setting(conn, "full_week", str(DEFAULT_FULL_WEEK))))
        self.full_week.valueChanged.connect(self._save_full_week)
        self.appearance = QComboBox()
        for key, label in theme.APPEARANCES.items():
            self.appearance.addItem(label, key)
        current = db.get_setting(conn, "appearance", theme.DEFAULT_APPEARANCE)
        self.appearance.setCurrentIndex(max(0, self.appearance.findData(current)))
        self.appearance.currentIndexChanged.connect(self._save_appearance)
        form.addRow("Your name", self.name)
        week_row = QHBoxLayout()
        week_row.addWidget(self.full_week)
        week_row.addWidget(_muted("Your full-time week. Holidays are taken out automatically."), 1)
        form.addRow("Full week", week_row)
        form.addRow("Appearance", self.appearance)
        layout.addLayout(form)

        layout.addWidget(_muted(f"Your data is saved as you type, in\n{db.DB_PATH}\n"
                                f"with a daily backup of the last {db.BACKUPS_KEPT} days next to it."))
        buttons = QHBoxLayout()
        folder = QPushButton("Open Data Folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(db.DB_PATH.parent))))
        done = QPushButton("Done")
        done.setProperty("primary", True)
        done.setDefault(True)
        done.clicked.connect(self.accept)
        buttons.addWidget(folder)
        buttons.addStretch(1)
        buttons.addWidget(done)
        layout.addLayout(buttons)

    def _save_name(self) -> None:
        db.set_setting(self.conn, "your_name", self.name.text().strip())
        self.changed.emit()

    def _save_full_week(self, hours: float) -> None:
        db.set_setting(self.conn, "full_week", str(round(hours, 2)))
        self.changed.emit()

    def _save_appearance(self) -> None:
        db.set_setting(self.conn, "appearance", self.appearance.currentData())
        theme.set_appearance(self.appearance.currentData())
        self.appearanceChanged.emit()

    def accept(self) -> None:
        self._save_name()
        super().accept()


HELP = """
<h2>Timeless</h2>
<p>One week at a time. Everything you type is saved immediately. <b>⌘Z</b> undoes, and a daily backup
covers the rest.</p>

<h3>The week</h3>
<ul>
<li>Every active project is a row. Add projects in <b>Projects</b>; archive them when they're over.</li>
<li>Type hours straight into a day: <code>7,5</code>, <code>7.5</code>, <code>7:30</code> or <code>8h</code>,
    or select the day and scroll to step it by half an hour. Arrows and Tab move around; <b>Delete</b> clears a
    day.</li>
<li>Scroll over the week at the top to move week by week, or click it for the calendar.</li>
<li>Faint numbers are the plan. Press <b>=</b> on a day to take it, or <b>Fill from plan</b> for the whole week.</li>
<li>Press <b>Enter</b> on a day to write its note in the bar below, then Enter again to come back. Days with a
    note get a dot. Select a project's name instead to see or edit the project's own notes.</li>
<li>Right-click a project to add a row for on-call, travel or another kind of hours. Those count toward the
    project's plan, and a kind can change billing.</li>
<li>The bar at the top shows the week against your full week (holidays taken out) and against the plan.
    <b>Of plan</b> turns amber when a project goes over.</li>
<li>For all-hands, training and other internal time, make a project with no plan. Its hours count toward the
    week but have no plan to go over; write what it was in the day's note.</li>
<li><b>Mark week done</b> once it's reported or invoiced: the week becomes read-only until you reopen it.</li>
</ul>

<h3>Report</h3>
<p>Hours per project for any date range (scroll the dates a day at a time), with the billable split and each
project's share, and a CSV export. The <i>Excel, Finnish</i> format opens directly in Finnish Excel.</p>

<h3>Keys</h3>
<p>⌘1 / ⌘2 / ⌘3 Week, Projects, Report &nbsp;·&nbsp; ⌘[ / ⌘] previous / next week &nbsp;·&nbsp; ⌘T this week
&nbsp;·&nbsp; ⌘Z / ⇧⌘Z undo / redo &nbsp;·&nbsp; ⌘, settings</p>

<h3>Finland</h3>
<p>Sundays and public holidays are red in the calendar; every holiday under the Annual Holidays Act is shaded in
the week and gets no planned hours.</p>
"""


class HelpDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Timeless Help")
        self.resize(600, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        browser = QTextBrowser()
        browser.setHtml(HELP)
        browser.setStyleSheet("QTextBrowser { border: none; background: transparent; }")
        layout.addWidget(browser, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)
