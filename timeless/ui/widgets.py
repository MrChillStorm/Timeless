"""Small widgets: the week navigator and its Finnish calendar, the view
switcher, the capacity bar, an hours spin box."""
from datetime import date, timedelta

from PySide6.QtCore import QDate, QLocale, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractSpinBox, QButtonGroup, QCalendarWidget, QDateEdit, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QPushButton, QToolButton, QWidget, QWidgetAction,
)

from timeless.core.calendar import (
    fmt_hours, holiday_tooltip, holidays, is_public_holiday, week_days, week_label, week_range, week_start,
)
from timeless.ui import icons
from timeless.ui.theme import colors

FINNISH = QLocale(QLocale.Language.Finnish, QLocale.Country.Finland)


def to_qdate(d: date) -> QDate:
    return QDate(d.year, d.month, d.day)


def from_qdate(q: QDate) -> date:
    return date(q.year(), q.month(), q.day())


def link_button(text: str, tooltip: str = "") -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName("link")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    btn.setToolTip(tooltip)
    return btn


def tool_button(icon_name: str, tooltip: str, size: int = 18) -> QToolButton:
    btn = QToolButton()
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    btn.setIconSize(QSize(size, size))
    btn.setProperty("icon_name", icon_name)
    return btn


def refresh_tool_icons(*buttons: QToolButton, color_key: str = "muted") -> None:
    c = colors()
    for b in buttons:
        b.setIcon(icons.icon(b.property("icon_name"), c[color_key], b.iconSize().width()))


class WheelSteps:
    """Turns wheel movement into whole notches. A trackpad sends many small
    deltas; they add up until they make one 120-unit notch."""

    def __init__(self):
        self._rest = 0

    def take(self, event) -> int:
        self._rest += event.angleDelta().y() or event.angleDelta().x()
        steps = int(self._rest / 120)
        self._rest -= steps * 120
        return steps


class HoursSpin(QDoubleSpinBox):
    """Hours in Finnish format (7,50) that also accepts a typed '.'."""

    def __init__(self, maximum: float = 24):
        super().__init__()
        self.setLocale(FINNISH)
        self.setRange(0, maximum)
        self.setDecimals(2)
        self.setSingleStep(0.5)
        self.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)

    def keyPressEvent(self, event) -> None:
        if event.text() == ".":
            event = QKeyEvent(event.type(), Qt.Key.Key_Comma, event.modifiers(), ",")
        super().keyPressEvent(event)


class DayEdit(QDateEdit):
    """A date field (d.M.yyyy) with the Finnish calendar popup. The wheel
    and the arrow keys move it a whole day at a time, straight across
    month ends, whichever part of the date the cursor is on."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat("d.M.yyyy")
        self.setCalendarWidget(FinnishCalendar())

    def stepBy(self, steps: int) -> None:
        self.setDate(self.date().addDays(steps))

    def stepEnabled(self):
        return QAbstractSpinBox.StepEnabledFlag.StepUpEnabled | QAbstractSpinBox.StepEnabledFlag.StepDownEnabled


class FinnishCalendar(QCalendarWidget):
    """Marked the Finnish way: Sundays and public holidays red, Saturdays
    and the holiday eves ordinary, holiday names on hover. Date cells are
    painted by hand -- Qt's own painting makes every Saturday red and
    flattens the neighbouring month's days to one grey."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.setGridVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.ISOWeekNumbers)
        self._tooltip_years: set[int] = set()
        self._week: set[date] = set()
        self.currentPageChanged.connect(lambda year, _m: self._add_tooltips(year))
        self._add_tooltips(self.yearShown())
        self.refresh()

    def refresh(self) -> None:
        c = colors()
        for day, color in ((Qt.DayOfWeek.Saturday, c["ink"]), (Qt.DayOfWeek.Sunday, c["danger"])):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            self.setWeekdayTextFormat(day, fmt)
        self.updateCells()

    def _add_tooltips(self, year: int) -> None:
        for y in (year - 1, year, year + 1):
            if y not in self._tooltip_years:
                self._tooltip_years.add(y)
                for d in holidays(y):
                    fmt = QTextCharFormat()
                    fmt.setToolTip(holiday_tooltip(d))
                    self.setDateTextFormat(to_qdate(d), fmt)

    def highlight_week(self, days: list[date]) -> None:
        self._week = set(days)
        self.updateCells()

    def paintCell(self, painter: QPainter, rect: QRect, qdate: QDate) -> None:
        c = colors()
        d = from_qdate(qdate)
        selected = qdate == self.selectedDate()
        in_week = d in self._week
        if selected:
            bg, fg = c["accent_strong"], c["on_accent"]
        else:
            bg = c["tint"] if in_week else c["surface"]
            fg = c["danger"] if d.weekday() == 6 or is_public_holiday(d) else c["accent"] if in_week else c["ink"]
        painter.save()
        painter.fillRect(rect, QColor(bg))
        if qdate.month() != self.monthShown() and not selected:
            painter.setOpacity(0.4)
        font = painter.font()
        font.setBold(d == date.today())
        painter.setFont(font)
        painter.setPen(QColor(fg))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(qdate.day()))
        painter.restore()


class WeekNavigator(QWidget):
    """‹  Week 39 · 21.9.–27.9.2026  ›  This week. The label opens a
    calendar (any day picked there goes to its week), and the mouse wheel
    anywhere over the navigator moves a week per notch."""
    weekChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._start = week_start(date.today())
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.prev_btn = tool_button("chevron_left", "Previous week (⌘[)")
        self.next_btn = tool_button("chevron_right", "Next week (⌘])")
        self.label_btn = QPushButton()
        self.label_btn.setObjectName("weekButton")
        self.label_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.label_btn.setToolTip("Pick a week from the calendar, or scroll to move week by week")
        self._wheel = WheelSteps()
        self.this_week = link_button("This week", "Go to the current week (⌘T)")
        self.prev_btn.clicked.connect(lambda: self._go(self._start - timedelta(weeks=1)))
        self.next_btn.clicked.connect(lambda: self._go(self._start + timedelta(weeks=1)))
        self.this_week.clicked.connect(lambda: self._go(week_start(date.today())))

        self.calendar = FinnishCalendar()
        self.calendar.setMinimumSize(310, 230)
        self.calendar.clicked.connect(self._picked)
        self.calendar.activated.connect(self._picked)
        self.menu = QMenu(self)
        action = QWidgetAction(self.menu)
        action.setDefaultWidget(self.calendar)
        self.menu.addAction(action)
        self.label_btn.clicked.connect(self._open_calendar)

        for w in (self.prev_btn, self.label_btn, self.next_btn):
            layout.addWidget(w)
        layout.addSpacing(6)
        layout.addWidget(self.this_week)
        self.set_week(self._start)

    def wheelEvent(self, event) -> None:
        steps = self._wheel.take(event)
        if steps:
            self._go(self._start + timedelta(weeks=steps))
        event.accept()

    def set_week(self, start: date) -> None:
        """Updates the display only; doesn't emit weekChanged."""
        self._start = start
        self.label_btn.setText(f"{week_label(start)}   ·   {week_range(start)}")
        self.this_week.setVisible(start != week_start(date.today()))
        self.refresh_icons()

    def refresh_icons(self) -> None:
        refresh_tool_icons(self.prev_btn, self.next_btn, color_key="ink")

    def _open_calendar(self) -> None:
        self.calendar.setSelectedDate(to_qdate(self._start))
        self.calendar.refresh()
        self.calendar.highlight_week(week_days(self._start))
        self.menu.popup(self.label_btn.mapToGlobal(self.label_btn.rect().bottomLeft()))

    def _picked(self, qdate: QDate) -> None:
        self.menu.close()
        self._go(week_start(from_qdate(qdate)))

    def _go(self, start: date) -> None:
        if start != self._start:
            self.weekChanged.emit(start)


class NoteBar(QFrame):
    """The bar under a grid that shows and edits the note of whatever is
    selected -- a day, a project. Its owner says what's showing; the bar
    reports edits (on Enter, or when focus leaves)."""
    committed = Signal(str)
    returned = Signal()  # Enter: the owner usually hands focus back to its grid

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 6, 16, 6)
        row.setSpacing(10)
        self.icon = QLabel()
        self.where = QLabel()
        self.where.setObjectName("muted")
        self.where.setMinimumWidth(200)
        self.edit = QLineEdit()
        self.edit.setObjectName("note")
        self.edit.returnPressed.connect(self._return)
        self.edit.editingFinished.connect(self.commit)
        for w in (self.icon, self.where):
            row.addWidget(w)
        row.addWidget(self.edit, 1)
        self._shown: str | None = None  # the text as shown, when it's editable
        self.refresh_icon()

    def set_content(self, where: str, text: str = "", placeholder: str = "", editable: bool = False) -> None:
        if self.edit.hasFocus():
            return  # never pull the text out from under someone typing
        self.where.setText(where)
        self.edit.setText(text)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setReadOnly(not editable)
        self._shown = text if editable else None

    def focus(self) -> None:
        if not self.edit.isReadOnly():
            self.edit.setFocus()
            self.edit.end(False)

    def commit(self) -> None:
        text = self.edit.text().strip()
        if self._shown is not None and text != self._shown.strip():
            self._shown = text
            self.committed.emit(text)

    def _return(self) -> None:
        self.commit()
        self.returned.emit()

    def refresh_icon(self) -> None:
        self.icon.setPixmap(icons.pixmap("note", colors()["muted"], 16))


class Segmented(QFrame):
    """A row of mutually exclusive buttons: the view switcher."""
    changed = Signal(int)

    def __init__(self, labels: list[str], tooltips: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("segments")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self.group = QButtonGroup(self)
        for i, label in enumerate(labels):
            btn = QPushButton(label)
            btn.setObjectName("segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if tooltips:
                btn.setToolTip(tooltips[i])
            self.group.addButton(btn, i)
            layout.addWidget(btn)
        self.group.button(0).setChecked(True)
        self.group.idClicked.connect(self.changed.emit)

    def set_current(self, i: int) -> None:
        self.group.button(i).setChecked(True)


class CapacityBar(QWidget):
    """Hours entered against the week's capacity, with a tick where the
    plan would put you. Amber once past full."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(10)
        self.entered = self.capacity = self.planned = 0.0

    def set_values(self, entered: float, capacity: float, planned: float) -> None:
        self.entered, self.capacity, self.planned = entered, capacity, planned
        self.setToolTip(f"{fmt_hours(entered)} h entered · {fmt_hours(planned)} h planned · "
                        f"{fmt_hours(capacity)} h full week")
        self.update()

    def paintEvent(self, _event) -> None:
        c = colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(0, self.height() / 2 - 4, self.width(), 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["track"]))
        p.drawRoundedRect(bar, 4, 4)
        if self.capacity and self.entered:
            share = self.entered / self.capacity
            p.setBrush(QColor(c["warning"] if share > 1.005 else c["accent_strong"]))
            p.drawRoundedRect(QRectF(0, bar.top(), max(8.0, bar.width() * min(share, 1.0)), bar.height()), 4, 4)
        if self.capacity and 0 < self.planned < self.capacity * 1.5:
            x = bar.width() * min(self.planned / self.capacity, 1.0)
            p.setBrush(QColor(c["ink"]))
            p.drawRoundedRect(QRectF(min(x, bar.width() - 2) - 1, bar.top() - 3, 2, bar.height() + 6), 1, 1)
        p.end()
