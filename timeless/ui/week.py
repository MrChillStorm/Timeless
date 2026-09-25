"""The Week view. Every active project is a row, with indented rows under
it for special kinds of hours (on-call, travel...). Empty days show the
plan faintly; everything typed is saved at once and can be undone."""
import time
from dataclasses import dataclass
from datetime import date, timedelta

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton, QStackedLayout, QVBoxLayout, QWidget,
)

from timeless.core import store
from timeless.core.calendar import (
    DAY_NAMES, DAY_TITLES, DEFAULT_FULL_WEEK, MAX_HOURS_PER_DAY, fmt_date, fmt_day, fmt_hours, fmt_percent,
    fmt_signed, holiday_tooltip, is_day_off, is_public_holiday, week_capacity, week_days, week_label, week_start,
)
from timeless.core.db import get_setting
from timeless.ui import icons
from timeless.ui.cells import (
    ALIGN, DAY_OFF_ROLE, DISPLAY, EDIT, GHOST_ROLE, HEIGHT_ROLE, HOLIDAY_ROLE, KIND_ROLE, LOCKED_ROLE, NOTE_ROLE,
    PROGRESS_ROLE, RIGHT, SECONDARY_ROLE, SUBROW_ROLE, TODAY_ROLE, TOOLTIP, TOTAL_ROLE, WARN_ROLE, GridView, H, Kind,
)
from timeless.ui.theme import colors
from timeless.ui.widgets import CapacityBar, NoteBar, link_button


class C:
    NAME = 0
    D0 = 1  # MON..SUN = 1..7
    WEEK = 8
    PLAN = 9
    COUNT = 10


def day_of(col: int) -> int | None:
    return col - C.D0 if C.D0 <= col < C.D0 + 7 else None


@dataclass
class Row:
    project: store.Project
    kind: store.Kind | None = None

    @property
    def kind_id(self) -> int | None:
        return self.kind.id if self.kind else None


@dataclass
class Change:
    key: store.Key
    before: tuple[float, str]
    after: tuple[float, str]


class WeekModel(QAbstractTableModel):
    changed = Signal()  # totals, capacity or done-ness may have moved
    message = Signal(str)

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.start = week_start(date.today())
        self.days = week_days(self.start)
        self.rows: list[Row] = []
        self.cells: dict[store.Key, tuple[float, str]] = {}
        self.projects: dict[int, store.Project] = {}
        self.kinds: list[store.Kind] = []
        self.done = False
        self.full_week = DEFAULT_FULL_WEEK
        self.full_day = DEFAULT_FULL_WEEK / 5
        self.flex_on = False
        self.flex_before = 0.0  # the flex balance when the week starts
        self._extra: set[tuple[int, int]] = set()  # kind rows added this week, still empty
        self._undo: list[list[Change]] = []
        self._redo: list[list[Change]] = []
        self._nudging: tuple[store.Key, float] | None = None  # (cell, when) of the last wheel step

    # ---- loading ----------------------------------------------------------

    def load(self, start: date | None = None) -> None:
        if start is not None and start != self.start:
            self.start, self.days = start, week_days(start)
            self._extra.clear()
            self._undo.clear()
            self._redo.clear()
        self.beginResetModel()
        projects = store.list_projects(self.conn, include_archived=True)
        self.projects = {p.id: p for p in projects}
        self.kinds = store.list_kinds(self.conn)
        self.cells = store.week_cells(self.conn, self.days)
        self.done = store.is_done(self.conn, self.start)
        self.flex_on = store.flex_enabled(self.conn)
        if self.flex_on:
            earned, spent = store.flex_between(self.conn, None, self.start - timedelta(days=1))
            self.flex_before = round(store.flex_start(self.conn) + earned - spent, 2)
        used = {(pid, kid) for _day, pid, kid in self.cells}
        self.rows = []
        for p in projects:
            if p.archived and not any(pid == p.id for pid, _ in used):
                continue
            self.rows.append(Row(p))
            for k in self.kinds:
                if (p.id, k.id) in used or (p.id, k.id) in self._extra:
                    self.rows.append(Row(p, k))
        self.endResetModel()
        self.headerDataChanged.emit(H, 0, C.COUNT - 1)
        self.changed.emit()

    # ---- queries ----------------------------------------------------------

    def key(self, r: int, day: int) -> store.Key:
        row = self.rows[r]
        return self.days[day], row.project.id, row.kind_id

    def cell(self, r: int, day: int) -> tuple[float, str]:
        return self.cells.get(self.key(r, day), (0.0, ""))

    def row_total(self, r: int) -> float:
        return round(sum(self.cell(r, i)[0] for i in range(7)), 2)

    def project_hours(self, project_id: int) -> float:
        """All of a project's hours this week, special kinds included --
        on-call hours are still work done on the project."""
        return round(sum(h for (_d, pid, _k), (h, _n) in self.cells.items() if pid == project_id), 2)

    def project_plan(self, p: store.Project) -> float:
        return round(sum(p.plan_for(d) for d in self.days), 2)

    def day_total(self, day: int) -> float:
        return round(sum(h for (d, _p, _k), (h, _n) in self.cells.items() if d == self.days[day]), 2)

    def week_total(self) -> float:
        return round(sum(h for h, _n in self.cells.values()), 2)

    def planned_total(self) -> float:
        return round(sum(self.project_plan(p) for p in self.projects.values() if not p.archived), 2)

    def unplanned_hours(self) -> float:
        """Hours outside planned_total: projects with no plan this week, such
        as internal meetings, and archived ones."""
        return round(sum(h for (_d, pid, _k), (h, _n) in self.cells.items()
                         if self.projects[pid].archived or not self.project_plan(self.projects[pid])), 2)

    def capacity(self) -> float:
        return week_capacity(self.start, self.full_week)

    def is_billable(self, project_id: int, kind_id: int | None) -> bool:
        p = self.projects[project_id]
        kind = next((k for k in self.kinds if k.id == kind_id), None)
        return kind.billable_for(p) if kind else p.billable

    def billable_split(self, day: int | None = None) -> tuple[float, float]:
        billable = non_billable = 0.0
        for (d, pid, kid), (h, _n) in self.cells.items():
            if day is not None and d != self.days[day]:
                continue
            if self.is_billable(pid, kid):
                billable += h
            else:
                non_billable += h
        return round(billable, 2), round(non_billable, 2)

    def _flex_kinds(self) -> set[int]:
        return {k.id for k in self.kinds if k.flex}

    def flex_week(self) -> tuple[float, float]:
        """(earned, used) this week: hours on flex rows, and on flex time off."""
        flex_kinds = self._flex_kinds()
        earned = sum(h for (_d, _p, kid), (h, _n) in self.cells.items() if kid in flex_kinds)
        used = sum(h for (_d, pid, _k), (h, _n) in self.cells.items() if self.projects[pid].flex)
        return round(earned, 2), round(used, 2)

    def flex_balance(self) -> float:
        """The flex balance at the end of the week."""
        earned, used = self.flex_week()
        return round(self.flex_before + earned - used, 2)

    def day_length(self, day: int) -> float:
        """The normal day flex hours go on top of: none on a day off."""
        return 0.0 if is_day_off(self.days[day]) else self.full_day

    def work_hours(self, day: int) -> float:
        """A day's hours besides flex rows and flex time off."""
        flex_kinds = self._flex_kinds()
        return round(sum(h for (d, pid, kid), (h, _n) in self.cells.items()
                         if d == self.days[day] and kid not in flex_kinds and not self.projects[pid].flex), 2)

    def flex_short(self, day: int) -> bool:
        """The day doesn't have a full day of other work yet."""
        return self.work_hours(day) + 1e-9 < self.day_length(day)

    def _flex_refused(self, key: store.Key, hours: float, before: float) -> bool:
        """More flex hours on a day that isn't full yet are refused, with a
        message. Fewer are always fine."""
        day, _pid, kid = key
        if not self.flex_on or hours <= before or kid not in self._flex_kinds():
            return False
        i = self.days.index(day)
        if not self.flex_short(i):
            return False
        self.message.emit(f"Flex hours go on top of a full day: {DAY_TITLES[i]} has {fmt_hours(self.work_hours(i))} h "
                          f"of other work, and a day is {fmt_hours(self.day_length(i))} h.")
        return True

    def row_of(self, project_id: int, kind_id: int | None = None) -> int:
        return next((i for i, r in enumerate(self.rows) if r.project.id == project_id and r.kind_id == kind_id), -1)

    # ---- writing (saved at once, undoable) --------------------------------

    def write(self, changes: list[tuple[store.Key, float | None, str | None]]) -> int:
        """Applies (key, hours, note) changes -- None keeps that part -- as
        one undo step. Returns how many cells actually changed."""
        self._nudging = None
        if self.done:
            return 0
        group = []
        for key, hours, note in changes:
            before = self.cells.get(key, (0.0, ""))
            after = (before[0] if hours is None else round(hours, 2), before[1] if note is None else note.strip())
            if self._flex_refused(key, after[0], before[0]):
                continue
            if after != before:
                group.append(Change(key, before, after))
        if group:
            self._apply(group, forward=True)
            self._undo.append(group)
            self._redo.clear()
        return len(group)

    def _apply(self, group: list[Change], forward: bool) -> None:
        for change in group:
            hours, note = change.after if forward else change.before
            store.set_cell(self.conn, change.key, hours, note)
            if hours or note:
                self.cells[change.key] = (hours, note)
            else:
                self.cells.pop(change.key, None)
        self._refresh()

    def undo(self) -> bool:
        if not self._undo or self.done:
            return False
        group = self._undo.pop()
        self._apply(group, forward=False)
        self._redo.append(group)
        return True

    def redo(self) -> bool:
        if not self._redo or self.done:
            return False
        group = self._redo.pop()
        self._apply(group, forward=True)
        self._undo.append(group)
        return True

    def _refresh(self) -> None:
        self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows), C.COUNT - 1))
        self.changed.emit()

    def setData(self, index, value, role=EDIT) -> bool:
        """Hours typed into a day cell."""
        day = day_of(index.column())
        if role != EDIT or day is None or index.row() >= len(self.rows):
            return False
        return bool(self.write([(self.key(index.row(), day), float(value or 0), None)]))

    NUDGE_MERGE_SECONDS = 2.0

    def nudge(self, index: QModelIndex, hours: float) -> bool:
        """A wheel step on a day. Steps on the same day in quick succession
        stay one undo step, so one ⌘Z takes the whole scroll back."""
        day = day_of(index.column())
        if self.done or day is None or index.row() >= len(self.rows):
            return False
        key = self.key(index.row(), day)
        if self._flex_refused(key, round(hours, 2), self.cells.get(key, (0.0, ""))[0]):
            return False
        now = time.monotonic()
        last = self._nudging
        top = self._undo[-1] if self._undo else None
        if last and last[0] == key and now - last[1] < self.NUDGE_MERGE_SECONDS \
                and top and len(top) == 1 and top[0].key == key:
            current = self.cells.get(key, (0.0, ""))
            after = (round(hours, 2), current[1])
            if after == current:
                return False
            self._apply([Change(key, current, after)], forward=True)
            top[0].after = after
            if top[0].after == top[0].before:  # scrolled back to where it started
                self._undo.pop()
            self._redo.clear()
        elif not self.write([(key, hours, None)]):
            return False
        self._nudging = (key, now)
        return True

    def set_note(self, r: int, day: int, text: str) -> None:
        self.write([(self.key(r, day), None, text)])

    def clear(self, index: QModelIndex) -> None:
        day = day_of(index.column())
        if day is not None and index.row() < len(self.rows):
            self.write([(self.key(index.row(), day), 0.0, None)])

    def planned(self, r: int, day: int) -> float:
        row = self.rows[r]
        return 0.0 if row.kind else row.project.plan_for(self.days[day])

    def accept_plan(self, index: QModelIndex) -> bool:
        day = day_of(index.column())
        if day is None or index.row() >= len(self.rows) or not self.planned(index.row(), day):
            return False
        return bool(self.write([(self.key(index.row(), day), self.planned(index.row(), day), None)]))

    def fill_from_plan(self, rows: list[int] | None = None) -> int:
        """Puts planned hours into every empty day (never over what's typed)."""
        changes = []
        for r in rows if rows is not None else range(len(self.rows)):
            if self.rows[r].kind or self.rows[r].project.archived:
                continue
            for day in range(7):
                if not self.cell(r, day)[0] and self.planned(r, day):
                    changes.append((self.key(r, day), self.planned(r, day), None))
        return self.write(changes)

    def clear_row(self, r: int) -> int:
        return self.write([(self.key(r, day), 0.0, None) for day in range(7)])

    def add_kind_row(self, project_id: int, kind_id: int) -> int:
        self._extra.add((project_id, kind_id))
        self.load()
        return self.row_of(project_id, kind_id)

    def set_done(self, done: bool) -> None:
        store.set_done(self.conn, self.start, done)
        self.done = done
        self._refresh()

    # ---- Qt model interface -------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() or not self.rows else len(self.rows) + 1

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else C.COUNT

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation != H:
            return None
        day = day_of(section)
        if role == DISPLAY:
            if day is not None:
                return f"{DAY_NAMES[day]}\n{fmt_day(self.days[day])}"
            return {C.NAME: "PROJECT", C.WEEK: "WEEK", C.PLAN: "OF PLAN"}.get(section, "")
        if role == ALIGN and section != C.NAME:
            return RIGHT
        if role == TOOLTIP and section == C.PLAN:
            return "Hours entered as a share of each project's weekly plan (set in Projects)"
        if day is None:
            return None
        d = self.days[day]
        if role == DAY_OFF_ROLE:
            return is_day_off(d)
        if role == TODAY_ROLE:
            return d == date.today()
        if role == HOLIDAY_ROLE:
            return is_public_holiday(d)
        if role == TOOLTIP:
            holiday = holiday_tooltip(d)
            return f"{DAY_TITLES[day]} {fmt_date(d)}" + (f"\n{holiday}" if holiday else "")
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        if index.row() >= len(self.rows):
            return Qt.ItemFlag.ItemIsEnabled
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if day_of(index.column()) is not None and not self.done:
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        day = day_of(c)
        if role == KIND_ROLE:
            return (Kind.NAME if c == C.NAME else Kind.HOURS if day is not None
                    else Kind.SUM if c == C.WEEK else Kind.PLAN)
        if role == DAY_OFF_ROLE:
            return day is not None and is_day_off(self.days[day])
        if role == LOCKED_ROLE:
            return self.done
        if r >= len(self.rows):
            return self._total_data(c, day, role)

        row = self.rows[r]
        if role == HEIGHT_ROLE:
            return 34 if row.kind else (48 if row.project.subtitle() else 40)
        if c == C.NAME:
            if role == DISPLAY:
                return row.kind.name if row.kind else row.project.name
            if role == SUBROW_ROLE:
                return row.kind is not None
            if role == SECONDARY_ROLE and not row.kind:
                return "  ·  ".join(p for p in (row.project.subtitle(), "archived" if row.project.archived else "") if p)
            if role == TOOLTIP:
                if row.kind:
                    billing = store.BILLING[row.kind.billing].lower()
                    flex = "\nThey go into your flex balance." if row.kind.flex and self.flex_on else ""
                    return (f"{row.kind.name} hours on {row.project.name} ({billing}).\nThey count toward its plan."
                            + flex)
                return (row.project.details() + "\n\nSelect the name to edit the project's notes below; "
                        "double-click to edit the project, right-click for more.")
            return None
        if day is not None:
            hours, note = self.cell(r, day)
            if role == DISPLAY:
                return fmt_hours(hours, blank_zero=True)
            if role == EDIT:
                return hours
            if role == NOTE_ROLE:
                return bool(note)
            flex_row = self.flex_on and row.kind is not None and row.kind.flex
            if role == WARN_ROLE:
                return flex_row and bool(hours) and self.flex_short(day)
            planned = self.planned(r, day)
            if role == GHOST_ROLE and not hours and planned and not self.done:
                return fmt_hours(planned)
            if role == TOOLTIP:
                tip = note
                if not hours and planned and not self.done:
                    tip = (tip + "\n\n" if tip else "") + f"Planned {fmt_hours(planned)} h. Press = to use it."
                if flex_row and self.flex_short(day):
                    tip = (tip + "\n\n" if tip else "") + (
                        f"Flex hours go on top of a full day: this day has {fmt_hours(self.work_hours(day))} h of "
                        f"other work, and a day is {fmt_hours(self.day_length(day))} h.")
                return tip or None
            return None
        if c == C.WEEK and role == DISPLAY:
            return fmt_hours(self.row_total(r), blank_zero=True)
        if c == C.PLAN and not row.kind:
            plan = self.project_plan(row.project)
            hours = self.project_hours(row.project.id)
            if role == PROGRESS_ROLE:
                return hours / plan if plan else None
            if role == DISPLAY:
                return fmt_percent(hours, plan) if plan else ""
            if role == WARN_ROLE:
                return bool(plan) and hours > plan + 1e-9
            if role == TOOLTIP:
                if not plan:
                    return "No plan this week: these hours count toward the week only."
                over = f"\nOver plan by {fmt_hours(hours - plan)} h" if hours > plan + 1e-9 else ""
                return f"{fmt_hours(hours)} h of {fmt_hours(plan)} h planned{over}"
        return None

    def _total_data(self, c: int, day: int | None, role: int):
        if role == TOTAL_ROLE:
            return True
        if role == HEIGHT_ROLE:
            return 40
        if c == C.NAME and role == DISPLAY:
            return "Total"
        if day is not None:
            total = self.day_total(day)
            if role == DISPLAY:
                return fmt_hours(total, blank_zero=True)
            if role == WARN_ROLE:
                return total > MAX_HOURS_PER_DAY
            if role == TOOLTIP:
                return self._split_tip(f"{DAY_TITLES[day]} {fmt_date(self.days[day])}", day,
                                       holiday_tooltip(self.days[day]))
        if c == C.WEEK:
            if role == DISPLAY:
                return fmt_hours(self.week_total())
            if role == TOOLTIP:
                return self._split_tip("This week")
        if c == C.PLAN:
            plan = self.planned_total()
            if role == PROGRESS_ROLE:
                return self.week_total() / plan if plan else None
            if role == DISPLAY:
                return fmt_percent(self.week_total(), plan) if plan else ""
            if role == WARN_ROLE:
                return bool(plan) and self.week_total() > plan + 1e-9
            if role == TOOLTIP and plan:
                unplanned = self.unplanned_hours()
                extra = f"\nIncludes {fmt_hours(unplanned)} h on projects with no plan" if unplanned else ""
                return f"{fmt_hours(self.week_total())} h of {fmt_hours(plan)} h planned for the week{extra}"
        return None

    def _split_tip(self, heading: str, day: int | None = None, extra: str | None = None) -> str | None:
        billable, non_billable = self.billable_split(day)
        lines = [heading]
        if billable or non_billable:
            lines += [f"Billable {fmt_hours(billable)} h", f"Non-billable {fmt_hours(non_billable)} h"]
        if day is not None and self.day_total(day) > MAX_HOURS_PER_DAY:
            lines.append(f"More than {MAX_HOURS_PER_DAY} hours on this day.")
        if extra:
            lines.append(extra)
        return "\n".join(lines) if len(lines) > 1 else None


class WeekPage(QWidget):
    """Summary strip, the grid, and the note bar for the selected day."""
    message = Signal(str)
    projectRequested = Signal(int)   # open this project in the Projects view
    newProjectRequested = Signal()
    kindsRequested = Signal()
    projectsChanged = Signal()       # e.g. project notes edited here

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.model = WeekModel(conn, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_summary())
        layout.addWidget(self._build_grid(), 1)
        layout.addWidget(self._build_note_bar())
        self.model.changed.connect(self._refresh_summary)
        self.model.modelReset.connect(self._after_reset)
        self.model.message.connect(self.message.emit)

    # ---- layout -------------------------------------------------------------

    def _build_summary(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        row = QHBoxLayout(card)
        row.setContentsMargins(18, 12, 14, 12)
        row.setSpacing(16)
        numbers = QVBoxLayout()
        numbers.setSpacing(0)
        self.total_label = QLabel()
        self.total_label.setObjectName("big")
        self.capacity_label = QLabel()
        self.capacity_label.setObjectName("muted")
        numbers.addWidget(self.total_label)
        numbers.addWidget(self.capacity_label)
        row.addLayout(numbers)
        bar_box = QVBoxLayout()
        bar_box.setSpacing(4)
        self.bar = CapacityBar()
        self.split_label = QLabel()
        self.split_label.setObjectName("muted")
        bar_box.addStretch(1)
        bar_box.addWidget(self.bar)
        bar_box.addWidget(self.split_label)
        bar_box.addStretch(1)
        row.addLayout(bar_box, 1)
        self.flex_box = QWidget()
        flex = QVBoxLayout(self.flex_box)
        flex.setContentsMargins(4, 0, 4, 0)
        flex.setSpacing(0)
        self.flex_value = QLabel()
        self.flex_value.setObjectName("flex")
        caption = QLabel("flex balance")
        caption.setObjectName("muted")
        for label in (self.flex_value, caption):
            label.setAlignment(Qt.AlignmentFlag.AlignRight)
            flex.addWidget(label)
        row.addWidget(self.flex_box)
        self.fill_btn = QPushButton("Fill from plan")
        self.fill_btn.setToolTip("Put the planned hours into every empty day of this week. ⌘Z undoes it.")
        self.done_btn = QPushButton()
        self.done_btn.setCheckable(True)
        for b in (self.fill_btn, self.done_btn):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(b)
        self.fill_btn.clicked.connect(self.fill_from_plan)
        self.done_btn.clicked.connect(self._toggle_done)
        return card

    def _build_grid(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        self.stack = QStackedLayout()
        self.view = GridView()
        self.view.setModel(self.model)
        header = self.view.horizontalHeader()
        header.setSectionResizeMode(C.NAME, QHeaderView.ResizeMode.Stretch)
        for col, width in ((C.WEEK, 84), (C.PLAN, 150), *((C.D0 + i, 74) for i in range(7))):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(col, width)
        grid_page = QWidget()
        grid_layout = QVBoxLayout(grid_page)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(0)
        grid_layout.addWidget(self.view, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(8, 6, 12, 8)
        self.new_btn = link_button("＋  New project", "Add a project -- it becomes a row of every week")
        self.new_btn.clicked.connect(self.newProjectRequested.emit)
        self.hint = QLabel()
        self.hint.setObjectName("faint")
        footer.addWidget(self.new_btn)
        footer.addStretch(1)
        footer.addWidget(self.hint)
        grid_layout.addLayout(footer)

        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch(1)
        msg = QLabel("Nothing to track yet.\nAdd the projects you work on and they'll show up here, every week.")
        msg.setObjectName("empty")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        first = QPushButton("Create your first project")
        first.setProperty("primary", True)
        first.setCursor(Qt.CursorShape.PointingHandCursor)
        first.clicked.connect(self.newProjectRequested.emit)
        empty_layout.addWidget(msg)
        empty_layout.addWidget(first, 0, Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch(2)
        self.stack.addWidget(grid_page)
        self.stack.addWidget(empty)
        outer.addLayout(self.stack)

        self.view.returnPressed.connect(self._focus_note)
        self.view.delegate.editorReturned.connect(lambda r, c: self._focus_note(self.model.index(r, c)))
        self.view.clearRequested.connect(self.model.clear)
        self.view.acceptRequested.connect(self._accept_plan)
        self.view.message.connect(self.message.emit)
        self.view.doubleClicked.connect(self._double_clicked)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.selectionModel().currentChanged.connect(lambda *_: self._show_note())
        return card

    def _build_note_bar(self) -> NoteBar:
        self.notes = NoteBar()
        self.note_edit = self.notes.edit
        self.notes.committed.connect(self._note_committed)
        self.notes.returned.connect(self.view.setFocus)
        self._note_target: tuple | None = None  # ("day", row, day) or ("project", id)
        return self.notes

    # ---- behavior -------------------------------------------------------------

    def load(self, start: date | None = None) -> None:
        self.view.commit_editor()
        self._commit_note()
        self.model.full_week = float(get_setting(self.conn, "full_week", str(DEFAULT_FULL_WEEK)))
        self.model.full_day = store.full_day(self.conn)
        self.model.load(start)

    def _after_reset(self) -> None:
        self.stack.setCurrentIndex(0 if self.model.rows else 1)
        extras = "flex, on-call or travel" if self.model.flex_on else "on-call or travel"
        self.hint.setText(f"Type or scroll hours  ·  Enter for a note  ·  = takes the plan  ·  right-click for {extras}")
        self._show_note()

    def _refresh_summary(self) -> None:
        m = self.model
        c = colors()
        total, capacity, planned = m.week_total(), m.capacity(), m.planned_total()
        self.total_label.setText(f"{fmt_hours(total)} h")
        days_off = 5 - round(capacity / (m.full_week / 5)) if m.full_week else 0
        holidays = f" ({days_off} holiday{'s' if days_off > 1 else ''})" if days_off > 0 else ""
        share = f"   ·   {fmt_percent(total, capacity)}" if capacity and total else ""
        self.capacity_label.setText(f"of a {fmt_hours(capacity)} h week{holidays}{share}")
        self.bar.set_values(total, capacity, planned)
        billable, non_billable = m.billable_split()
        plan_text = f"   ·   plan {fmt_hours(planned)} h" if planned else ""
        self.split_label.setText(f"Billable {fmt_hours(billable)} h   ·   non-billable {fmt_hours(non_billable)} h"
                                 + plan_text)
        self.flex_box.setVisible(m.flex_on)
        if m.flex_on:
            balance = m.flex_balance()
            self.flex_value.setText(f"{fmt_signed(balance)} h")
            self.flex_value.setStyleSheet(f"color: {c['warning']};" if balance < 0 else "")
            self.flex_box.setToolTip(self._flex_tip(balance))
        self.fill_btn.setEnabled(not m.done and bool(m.rows))
        self.done_btn.setChecked(m.done)
        self.done_btn.setText("Done  ·  reopen" if m.done else "Mark week done")
        self.done_btn.setToolTip("This week is marked done and read-only. Click to reopen it." if m.done else
                                 "Mark the week done once it's reported or invoiced: it becomes read-only.")
        self.done_btn.setIcon(icons.icon("lock" if m.done else "check_circle", c["success" if m.done else "muted"], 16))
        self.done_btn.setProperty("done", m.done)
        self.done_btn.style().unpolish(self.done_btn)
        self.done_btn.style().polish(self.done_btn)
        self.notes.refresh_icon()
        self._show_note()

    def _flex_tip(self, balance: float) -> str:
        m = self.model
        earned, used = m.flex_week()
        week = week_label(m.start)
        kinds = " / ".join(k.name for k in m.kinds if k.flex) or "a flex"
        time_off = " / ".join(p.name for p in m.projects.values() if p.flex and not p.archived) or "flex time off"
        return (f"Flex balance at the end of {week[0].lower() + week[1:]}: {fmt_signed(balance)} h\n\n"
                f"Before this week: {fmt_signed(m.flex_before)} h\n"
                f"This week: {fmt_hours(earned)} h earned, {fmt_hours(used)} h taken off\n\n"
                f"Extra hours go on a {kinds} row under the project (right-click it).\n"
                f"Time taken off goes on {time_off}. The starting balance is in Settings.")

    def _toggle_done(self) -> None:
        self.view.commit_editor()
        self._commit_note()
        self.model.set_done(not self.model.done)
        self.message.emit("Week marked done -- it's read-only now." if self.model.done else "Week reopened.")

    def fill_from_plan(self) -> None:
        self.view.commit_editor()
        n = self.model.fill_from_plan()
        self.message.emit(f"Filled {n} day(s) from the plan. ⌘Z undoes it." if n else
                          "Nothing to fill: every planned day already has hours.")

    def _accept_plan(self, index: QModelIndex) -> None:
        if self.model.accept_plan(index):
            self.view.setCurrentIndex(index)

    def undo(self) -> None:
        self.view.commit_editor()
        self._commit_note()
        if not self.model.undo():
            self.message.emit("Nothing to undo." if not self.model.done else "This week is done -- reopen it first.")

    def redo(self) -> None:
        if not self.model.redo():
            self.message.emit("Nothing to redo.")

    # ---- the note bar ---------------------------------------------------------

    def _target_for(self, index: QModelIndex) -> tuple | None:
        """What the note bar is about: a day, or a project (its name selected)."""
        if not index.isValid() or index.row() >= len(self.model.rows):
            return None
        day = day_of(index.column())
        if day is not None:
            return "day", index.row(), day
        row = self.model.rows[index.row()]
        if index.column() == C.NAME and row.kind is None:
            return "project", row.project.id
        return None

    def _show_note(self) -> None:
        if self.notes.edit.hasFocus():
            return
        target = self._target_for(self.view.currentIndex())
        self._note_target = target
        if target is None:
            self.notes.set_content("Notes", placeholder="Select a day, or a project's name, to see or write its note")
        elif target[0] == "day":
            _kind, r, day = target
            row = self.model.rows[r]
            name = f"{row.project.name} · {row.kind.name}" if row.kind else row.project.name
            self.notes.set_content(
                f"{DAY_TITLES[day][:3]} {fmt_day(self.model.days[day])}  ·  {name}", self.model.cell(r, day)[1],
                "This week is done." if self.model.done else "What did you do? (saved as you go)",
                editable=not self.model.done)
        else:
            p = self.model.projects[target[1]]
            self.notes.set_content(f"{p.name}  ·  project notes", p.notes,
                                   "Notes about the project: contacts, order numbers, how to book it… "
                                   "(saved as you go)", editable=True)

    def _focus_note(self, index: QModelIndex) -> None:
        if self._target_for(index) is None:
            return
        self.view.setCurrentIndex(index)
        self._show_note()
        self.notes.focus()

    def _commit_note(self) -> None:
        self.notes.commit()

    def _note_committed(self, text: str) -> None:
        target = self._note_target
        if target is None:
            return
        if target[0] == "day":
            _kind, r, day = target
            if r < len(self.model.rows):
                self.model.set_note(r, day, text)
        else:
            p = self.model.projects.get(target[1])
            if p:
                p.notes = text
                store.save_project(self.conn, p)
                self.projectsChanged.emit()

    # ---- rows -------------------------------------------------------------------

    def _double_clicked(self, index: QModelIndex) -> None:
        if index.column() == C.NAME and index.row() < len(self.model.rows):
            self.projectRequested.emit(self.model.rows[index.row()].project.id)

    def _context_menu(self, pos) -> None:
        index = self.view.indexAt(pos)
        if not index.isValid() or index.row() >= len(self.model.rows):
            return
        r = index.row()
        row = self.model.rows[r]
        menu = QMenu(self)
        if not self.model.done:
            if row.kind is None and not row.project.flex:
                add = menu.addMenu(f"Add a row under {row.project.name}")
                present = {x.kind_id for x in self.model.rows if x.project.id == row.project.id}
                for k in self.model.kinds:
                    if k.id not in present:
                        add.addAction(k.name, lambda k=k: self._add_kind_row(row.project.id, k.id))
                if add.actions():
                    add.addSeparator()
                add.addAction("New kind…", self.kindsRequested.emit)
            if row.kind is None and self.model.project_plan(row.project):
                menu.addAction("Fill from plan", lambda: self._fill_row(r))
            if self.model.row_total(r):
                menu.addAction("Clear this week's hours", lambda: self._clear_row(r))
            menu.addSeparator()
        menu.addAction("Edit project…", lambda: self.projectRequested.emit(row.project.id))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _add_kind_row(self, project_id: int, kind_id: int) -> None:
        r = self.model.add_kind_row(project_id, kind_id)
        if r >= 0:
            first_workday = next((i for i, d in enumerate(self.model.days) if not is_day_off(d)), 0)
            self.view.setCurrentIndex(self.model.index(r, C.D0 + first_workday))
            self.view.setFocus()

    def _fill_row(self, r: int) -> None:
        n = self.model.fill_from_plan([r])
        self.message.emit(f"Filled {n} day(s) from the plan. ⌘Z undoes it.")

    def _clear_row(self, r: int) -> None:
        n = self.model.clear_row(r)
        self.message.emit(f"Cleared {n} day(s). Notes are kept. ⌘Z undoes it.")
