"""The Projects view: projects and their weekly plans in one editable
table, and the special kinds of hours below it. Every edit saves itself."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from timeless.core import store
from timeless.core.calendar import DAY_NAMES, fmt_hours
from timeless.ui.cells import (
    ALIGN, CHECKED_ROLE, DISPLAY, EDIT, HEIGHT_ROLE, KIND_ROLE, LOCKED_ROLE, OPTIONS_ROLE, PLACEHOLDER_ROLE, RIGHT,
    TOOLTIP, GridView, H, Kind,
)
from timeless.ui.widgets import NoteBar


class P:
    NAME, CODE, CLIENT, BILLABLE = range(4)
    D0 = 4  # MON..SUN plan = 4..10
    WEEK, BOOKED = 11, 12
    COUNT = 13


class K:
    NAME, BILLING, FLEX, HOURS = range(4)
    COUNT = 4


class ProjectsModel(QAbstractTableModel):
    message = Signal(str)
    changed = Signal()

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.show_archived = False
        self.projects: list[store.Project] = []
        self.booked: dict[int, float] = {}

    def load(self) -> None:
        self.beginResetModel()
        self.projects = store.list_projects(self.conn, include_archived=self.show_archived)
        self.booked = store.hours_by_project(self.conn)
        self.endResetModel()

    def row_of(self, project_id: int) -> int:
        return next((i for i, p in enumerate(self.projects) if p.id == project_id), -1)

    def _save(self, p: store.Project) -> bool:
        try:
            store.save_project(self.conn, p)
        except store.StoreError as exc:
            self.message.emit(str(exc))
            self.load()  # back to what's stored
            return False
        self.changed.emit()
        return True

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.projects)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else P.COUNT

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation != H:
            return None
        if role == DISPLAY:
            if P.D0 <= section < P.D0 + 7:
                return DAY_NAMES[section - P.D0]
            return {P.NAME: "PROJECT", P.CODE: "CODE", P.CLIENT: "CLIENT", P.BILLABLE: "BILLABLE",
                    P.WEEK: "WEEK PLAN", P.BOOKED: "HOURS"}.get(section, "")
        if role == ALIGN and (section >= P.D0 or section == P.BILLABLE):
            return RIGHT if section != P.BILLABLE else int(Qt.AlignmentFlag.AlignCenter)
        if role == TOOLTIP:
            if P.D0 <= section < P.D0 + 7:
                return "Planned hours on this weekday. Holidays are left out automatically."
            if section == P.BOOKED:
                return "All hours ever entered on the project"
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        c = index.column()
        if c in (P.NAME, P.CODE, P.CLIENT) or P.D0 <= c < P.D0 + 7:
            f |= Qt.ItemFlag.ItemIsEditable
        elif c == P.BILLABLE:
            f |= Qt.ItemFlag.ItemIsUserCheckable
        return f

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        p = self.projects[index.row()]
        c = index.column()
        if role == KIND_ROLE:
            if P.D0 <= c < P.D0 + 7:
                return Kind.HOURS
            return {P.NAME: Kind.TEXT, P.CODE: Kind.TEXT, P.CLIENT: Kind.TEXT, P.BILLABLE: Kind.CHECK,
                    P.WEEK: Kind.SUM, P.BOOKED: Kind.MUTED}[c]
        if role == HEIGHT_ROLE:
            return 40
        if role == LOCKED_ROLE:
            return p.archived
        if P.D0 <= c < P.D0 + 7:
            h = p.plan[c - P.D0]
            return fmt_hours(h, blank_zero=True) if role == DISPLAY else h if role == EDIT else None
        if c in (P.NAME, P.CODE, P.CLIENT):
            value = {P.NAME: p.name, P.CODE: p.code, P.CLIENT: p.client}[c]
            if role in (DISPLAY, EDIT):
                return value + ("  (archived)" if role == DISPLAY and c == P.NAME and p.archived else "")
            if role == PLACEHOLDER_ROLE:
                return {P.CODE: "code", P.CLIENT: "client"}.get(c)
            if role == TOOLTIP and c == P.NAME:
                return p.details() + "\n\nThe project's notes are in the bar below the table."
        if c == P.BILLABLE and role == CHECKED_ROLE:
            return p.billable
        if c == P.WEEK and role == DISPLAY:
            return fmt_hours(sum(p.plan), blank_zero=True)
        if c == P.BOOKED and role == DISPLAY:
            return fmt_hours(self.booked.get(p.id, 0.0), blank_zero=True)
        return None

    def setData(self, index, value, role=EDIT) -> bool:
        p = self.projects[index.row()]
        c = index.column()
        if P.D0 <= c < P.D0 + 7 and role == EDIT:
            if p.plan[c - P.D0] == value:
                return False
            p.plan[c - P.D0] = float(value)
        elif c == P.NAME and role == EDIT:
            if not value or value == p.name:
                if not value:
                    self.message.emit("A project needs a name.")
                return False
            p.name = value
        elif c == P.CODE and role == EDIT and value != p.code:
            p.code = value
        elif c == P.CLIENT and role == EDIT and value != p.client:
            p.client = value
        elif c == P.BILLABLE and role == CHECKED_ROLE:
            p.billable = bool(value)
        else:
            return False
        if self._save(p):
            self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), P.COUNT - 1))
            return True
        return False


class KindsModel(QAbstractTableModel):
    message = Signal(str)
    changed = Signal()

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.kinds: list[store.Kind] = []
        self.booked: dict[int, float] = {}

    def load(self) -> None:
        self.beginResetModel()
        self.kinds = store.list_kinds(self.conn)
        self.booked = store.hours_by_kind(self.conn)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.kinds)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else K.COUNT

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation != H:
            return None
        if role == DISPLAY:
            return ("KIND", "BILLING", "FLEX", "HOURS")[section]
        if role == ALIGN and section == K.HOURS:
            return RIGHT
        if role == ALIGN and section == K.FLEX:
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == TOOLTIP and section == K.FLEX:
            return "Extra hours: hours of this kind go into your flex balance"
        return None

    def flags(self, index):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == K.FLEX:
            return f | Qt.ItemFlag.ItemIsUserCheckable
        return f | Qt.ItemFlag.ItemIsEditable if index.column() in (K.NAME, K.BILLING) else f

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        k = self.kinds[index.row()]
        c = index.column()
        if role == KIND_ROLE:
            return (Kind.TEXT, Kind.CHOICE, Kind.CHECK, Kind.MUTED)[c]
        if role == HEIGHT_ROLE:
            return 40
        if c == K.NAME and role in (DISPLAY, EDIT):
            return k.name
        if c == K.FLEX and role == CHECKED_ROLE:
            return k.flex
        if c == K.BILLING:
            if role == DISPLAY:
                return store.BILLING[k.billing]
            if role == EDIT:
                return k.billing
            if role == OPTIONS_ROLE:
                return [(label, key) for key, label in store.BILLING.items()]
            if role == TOOLTIP:
                return "As project: billable when the project is. Or always billable / never billable."
        if c == K.HOURS and role == DISPLAY:
            return fmt_hours(self.booked.get(k.id, 0.0), blank_zero=True)
        return None

    def setData(self, index, value, role=EDIT) -> bool:
        k = self.kinds[index.row()]
        if index.column() == K.FLEX and role == CHECKED_ROLE:
            k.flex = bool(value)
        elif role != EDIT or not value:
            return False
        elif index.column() == K.NAME and value != k.name:
            k.name = value
        elif index.column() == K.BILLING and value != k.billing:
            k.billing = value
        else:
            return False
        try:
            store.save_kind(self.conn, k)
        except store.StoreError as exc:
            self.message.emit(str(exc))
            self.load()
            return False
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), K.COUNT - 1))
        self.changed.emit()
        return True


def _card(title: str, blurb: str) -> tuple[QFrame, QVBoxLayout, QHBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(1, 1, 1, 1)
    layout.setSpacing(0)
    head = QHBoxLayout()
    head.setContentsMargins(16, 12, 14, 10)
    text = QVBoxLayout()
    text.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("heading")
    b = QLabel(blurb)
    b.setObjectName("muted")
    b.setWordWrap(True)
    text.addWidget(t)
    text.addWidget(b)
    head.addLayout(text, 1)
    layout.addLayout(head)
    return card, layout, head


class ProjectsPage(QWidget):
    message = Signal(str)
    changed = Signal()  # projects or kinds changed: the Week view should reload

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.projects = ProjectsModel(conn, self)
        self.kinds = KindsModel(conn, self)
        row = QVBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        card, layout, head = _card("Projects", "Every active project is a row of every week. The plan is what you "
                                               "expect to spend on each weekday; archive a project when it's over.")
        self.show_archived = QCheckBox("Show archived")
        self.show_archived.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.show_archived.toggled.connect(self._toggle_archived)
        new = QPushButton("＋  New project")
        new.setProperty("primary", True)
        new.setCursor(Qt.CursorShape.PointingHandCursor)
        new.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        new.clicked.connect(self.new_project)
        head.addWidget(self.show_archived)
        head.addSpacing(10)
        head.addWidget(new)
        self.project_view = GridView(return_edits=True)
        self.project_view.setModel(self.projects)
        header = self.project_view.horizontalHeader()
        header.setSectionResizeMode(P.NAME, QHeaderView.ResizeMode.Stretch)
        for col, width in ((P.CODE, 120), (P.CLIENT, 170), (P.BILLABLE, 92), (P.WEEK, 104), (P.BOOKED, 90),
                           *((P.D0 + i, 62) for i in range(7))):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(col, width)
        layout.addWidget(self.project_view, 1)
        row.addWidget(card, 3)
        self.notes = NoteBar()
        self.notes.committed.connect(self._notes_committed)
        self.notes.returned.connect(self.project_view.setFocus)
        self._notes_project: int | None = None
        row.addWidget(self.notes)

        kcard, klayout, khead = _card("Kinds", "Special hours such as on-call or travel get their own row under a "
                                               "project (right-click it in the week). They count toward its plan.")
        knew = QPushButton("＋  New kind")
        knew.setCursor(Qt.CursorShape.PointingHandCursor)
        knew.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        knew.clicked.connect(self.new_kind)
        khead.addWidget(knew)
        self.kind_view = GridView(return_edits=True)
        self.kind_view.setModel(self.kinds)
        kheader = self.kind_view.horizontalHeader()
        kheader.setSectionResizeMode(K.NAME, QHeaderView.ResizeMode.Stretch)
        for col, width in ((K.BILLING, 170), (K.FLEX, 80), (K.HOURS, 90)):
            kheader.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            kheader.resizeSection(col, width)
        klayout.addWidget(self.kind_view, 1)
        row.addWidget(kcard, 1)

        for model in (self.projects, self.kinds):
            model.message.connect(self.message.emit)
            model.changed.connect(self.changed.emit)
        for view in (self.project_view, self.kind_view):
            view.message.connect(self.message.emit)
        self.project_view.customContextMenuRequested.connect(self._project_menu)
        self.project_view.selectionModel().currentChanged.connect(lambda *_: self._show_notes())
        self.projects.modelReset.connect(self._show_notes)
        self.kind_view.customContextMenuRequested.connect(self._kind_menu)

    def load(self) -> None:
        self.notes.commit()
        self.projects.load()
        self.kinds.load()
        self.kind_view.setColumnHidden(K.FLEX, not store.flex_enabled(self.conn))
        self._show_notes()

    def _show_notes(self) -> None:
        index = self.project_view.currentIndex()
        p = self.projects.projects[index.row()] if index.isValid() and index.row() < len(self.projects.projects) \
            else None
        self._notes_project = p.id if p else None
        self.notes.refresh_icon()
        if p is None:
            self.notes.set_content("Notes", placeholder="Select a project to see or write its notes")
        else:
            self.notes.set_content(f"{p.name}  ·  project notes", p.notes,
                                   "Contacts, order numbers, how to book it… (saved as you go)", editable=True)

    def _notes_committed(self, text: str) -> None:
        r = self.projects.row_of(self._notes_project) if self._notes_project else -1
        if r < 0:
            return
        p = self.projects.projects[r]
        p.notes = text
        if self.projects._save(p):
            self.message.emit(f"Notes saved for '{p.name}'.")

    def show_project(self, project_id: int) -> None:
        if self.projects.row_of(project_id) < 0 and not self.show_archived.isChecked():
            self.show_archived.setChecked(True)
        r = self.projects.row_of(project_id)
        if r >= 0:
            self.project_view.setCurrentIndex(self.projects.index(r, P.NAME))
            self.project_view.scrollTo(self.projects.index(r, P.NAME))
            self.project_view.setFocus()

    def _toggle_archived(self, on: bool) -> None:
        self.projects.show_archived = on
        self.projects.load()

    def new_project(self) -> None:
        p = store.Project(name=store.unique_name(self.conn, "projects", "New project"))
        store.save_project(self.conn, p)
        self.projects.load()
        self.changed.emit()
        self._edit_name(self.project_view, self.projects.index(self.projects.row_of(p.id), P.NAME))
        self.message.emit("New project added -- type its name. The rest is optional.")

    def new_kind(self) -> None:
        k = store.Kind(name=store.unique_name(self.conn, "kinds", "New kind"))
        store.save_kind(self.conn, k)
        self.kinds.load()
        self.changed.emit()
        r = next(i for i, x in enumerate(self.kinds.kinds) if x.id == k.id)
        self._edit_name(self.kind_view, self.kinds.index(r, 0))

    def _edit_name(self, view: GridView, index: QModelIndex) -> None:
        view.setFocus()
        view.setCurrentIndex(index)
        QTimer.singleShot(0, lambda: view.edit(index))

    def _project_menu(self, pos) -> None:
        index = self.project_view.indexAt(pos)
        if not index.isValid():
            return
        p = self.projects.projects[index.row()]
        menu = QMenu(self)
        menu.addAction("Restore" if p.archived else "Archive", lambda: self._set_archived(p, not p.archived))
        menu.addAction("Delete…", lambda: self._delete_project(p))
        menu.exec(self.project_view.viewport().mapToGlobal(pos))

    def _set_archived(self, p: store.Project, archived: bool) -> None:
        p.archived = archived
        store.save_project(self.conn, p)
        self.projects.load()
        self.changed.emit()
        self.message.emit(f"Archived '{p.name}': it leaves your weeks but keeps its history." if archived
                          else f"Restored '{p.name}'.")

    def _delete_project(self, p: store.Project) -> None:
        if QMessageBox.question(self, "Delete Project", f"Delete '{p.name}'?") != QMessageBox.StandardButton.Yes:
            return
        try:
            store.delete_project(self.conn, p.id)
        except store.StoreError as exc:
            QMessageBox.information(self, "Delete Project", str(exc))
            return
        self.projects.load()
        self.changed.emit()

    def _kind_menu(self, pos) -> None:
        index = self.kind_view.indexAt(pos)
        if not index.isValid():
            return
        k = self.kinds.kinds[index.row()]
        menu = QMenu(self)
        menu.addAction("Delete…", lambda: self._delete_kind(k))
        menu.exec(self.kind_view.viewport().mapToGlobal(pos))

    def _delete_kind(self, k: store.Kind) -> None:
        if QMessageBox.question(self, "Delete Kind", f"Delete '{k.name}'?") != QMessageBox.StandardButton.Yes:
            return
        try:
            store.delete_kind(self.conn, k.id)
        except store.StoreError as exc:
            QMessageBox.information(self, "Delete Kind", str(exc))
            return
        self.kinds.load()
        self.changed.emit()
