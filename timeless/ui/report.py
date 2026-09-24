"""The Report view: hours per project for any date range, with the
billable split and each project's share, and CSV export."""
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from timeless.core import report
from timeless.core.calendar import fmt_date, fmt_hours, fmt_percent, week_start
from timeless.core.db import get_setting, set_setting
from timeless.ui import icons
from timeless.ui.theme import colors
from timeless.ui.widgets import DayEdit, Segmented, from_qdate, to_qdate

PRESETS = ("This week", "Last week", "This month", "Last month", "This year")
COLUMNS = ("PROJECT", "CODE", "HOURS", "BILLABLE", "NON-BILLABLE", "SHARE")


def preset_range(name: str, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    monday = week_start(today)
    if name == "This week":
        return monday, monday + timedelta(days=6)
    if name == "Last week":
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if name == "This month":
        return report.month_range(today)
    if name == "Last month":
        return report.month_range(today.replace(day=1) - timedelta(days=1))
    return date(today.year, 1, 1), date(today.year, 12, 31)


def _date_edit() -> DayEdit:
    edit = DayEdit()
    edit.setFixedWidth(130)
    edit.setToolTip("Scroll or use the arrow keys to move a day at a time, or pick from the calendar")
    return edit


class ReportPage(QWidget):
    message = Signal(str)

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.entries: list[report.Entry] = []
        card = QFrame()
        card.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        controls = QHBoxLayout()
        controls.setContentsMargins(16, 12, 16, 12)
        controls.setSpacing(10)
        self.presets = Segmented(list(PRESETS))
        self.presets.changed.connect(lambda i: self.set_range(*preset_range(PRESETS[i])))
        self.start = _date_edit()
        self.end = _date_edit()
        self.start.dateChanged.connect(self._custom_range)
        self.end.dateChanged.connect(self._custom_range)
        controls.addWidget(self.presets)
        controls.addStretch(1)
        controls.addWidget(QLabel("From"))
        controls.addWidget(self.start)
        controls.addWidget(QLabel("to"))
        controls.addWidget(self.end)
        layout.addLayout(controls)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for col, width in ((1, 130), (2, 110), (3, 110), (4, 130), (5, 90)):
            self.table.setColumnWidth(col, width)
        self.table.horizontalHeaderItem(0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.horizontalHeaderItem(1).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.table, 1)

        bottom = QFrame()
        bottom.setObjectName("bar")
        foot = QHBoxLayout(bottom)
        foot.setContentsMargins(16, 10, 16, 10)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.fmt = QComboBox()
        for key, (label, *_rest) in report.CSV_FORMATS.items():
            self.fmt.addItem(label, key)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(get_setting(conn, "csv_format", "excel_fi"))))
        self.fmt.currentIndexChanged.connect(lambda _i: set_setting(conn, "csv_format", self.fmt.currentData()))
        self.export_btn = QPushButton("Export CSV…")
        self.export_btn.setProperty("primary", True)
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.clicked.connect(self.export)
        foot.addWidget(self.summary, 1)
        foot.addWidget(self.fmt)
        foot.addWidget(self.export_btn)
        layout.addWidget(bottom)
        self.presets.set_current(2)
        self.set_range(*preset_range("This month"))

    def refresh_icons(self) -> None:
        self.export_btn.setIcon(icons.icon("export", colors()["on_accent"], 16))

    def set_range(self, start: date, end: date) -> None:
        for edit, d in ((self.start, start), (self.end, end)):
            edit.blockSignals(True)
            edit.setDate(to_qdate(d))
            edit.blockSignals(False)
        self.load()

    def _custom_range(self) -> None:
        self.presets.group.setExclusive(False)
        for b in self.presets.group.buttons():
            b.setChecked(False)
        self.presets.group.setExclusive(True)
        self.load()

    def _range(self) -> tuple[date, date]:
        return from_qdate(self.start.date()), from_qdate(self.end.date())

    def load(self) -> None:
        start, end = self._range()
        self.entries = report.entries_between(self.conn, start, end) if start <= end else []
        totals = report.summarize(self.entries)
        grand = round(sum(t.total for t in totals), 2)
        rows: list[tuple[list[str], str]] = []  # (cells, style)
        for t in totals:
            rows.append(([t.project, t.code, fmt_hours(t.total), fmt_hours(t.billable, True),
                          fmt_hours(t.non_billable, True), fmt_percent(t.total, grand)], "project"))
            for kind, hours in sorted(t.kinds.items()):
                rows.append(([f"↳  {kind}", "", fmt_hours(hours), "", "", ""], "kind"))
        if totals:
            billable = round(sum(t.billable for t in totals), 2)
            rows.append((["Total", "", fmt_hours(grand), fmt_hours(billable), fmt_hours(round(grand - billable, 2)),
                          fmt_percent(grand, grand)], "total"))
        c = colors()
        self.table.setRowCount(len(rows))
        for r, (cells, style) in enumerate(rows):
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                align = Qt.AlignmentFlag.AlignLeft if col < 2 else Qt.AlignmentFlag.AlignRight
                item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                if style == "total":
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                if style == "kind" or col == 1:
                    item.setForeground(QColor(c["muted"]))
                self.table.setItem(r, col, item)
        days = len({e.day for e in self.entries if e.hours})
        self.summary.setText(f"{fmt_date(start)} – {fmt_date(end)}  ·  {fmt_hours(grand)} h on {days} day(s)"
                             if totals else "No hours in this range.")
        self.export_btn.setEnabled(bool(self.entries))
        self.refresh_icons()

    def export(self) -> None:
        start, end = self._range()
        person = get_setting(self.conn, "your_name", "") or ""
        suggested = Path.home() / "Documents" / f"timeless_{start.isoformat()}_{end.isoformat()}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", str(suggested), "CSV files (*.csv)")
        if not path:
            return
        try:
            report.export_csv(Path(path), self.entries, person, self.fmt.currentData())
        except OSError as exc:
            QMessageBox.warning(self, "Export", f"Couldn't write the file:\n\n{exc}")
            return
        self.message.emit(f"Exported {len(self.entries)} entries to {path}")
