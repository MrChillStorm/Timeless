"""The window: a slim header with the week and the three views."""
from datetime import date

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QStatusBar, QVBoxLayout, QWidget,
)

from timeless.core import db
from timeless.core.calendar import week_label, week_start
from timeless.ui import icons, theme
from timeless.ui.dialogs import HelpDialog, SettingsDialog
from timeless.ui.projects import ProjectsPage
from timeless.ui.report import ReportPage
from timeless.ui.week import WeekPage
from timeless.ui.widgets import Segmented, WeekNavigator, refresh_tool_icons, tool_button

WEEK, PROJECTS, REPORT = range(3)


class MainWindow(QMainWindow):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.resize(1320, 800)
        self.setMinimumSize(980, 600)
        self._stale = False  # projects or kinds changed since the week was loaded

        central = QWidget()
        central.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())
        self.pages = QStackedWidget()
        self.week = WeekPage(conn)
        self.projects = ProjectsPage(conn)
        self.report = ReportPage(conn)
        for page in (self.week, self.projects, self.report):
            self.pages.addWidget(page)
            page.message.connect(self._status)
        body = QVBoxLayout()
        body.setContentsMargins(16, 14, 16, 4)
        body.addWidget(self.pages)
        outer.addLayout(body, 1)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

        self.navigator.weekChanged.connect(self.go_to_week)
        self.views.changed.connect(self.show_view)
        self.week.model.changed.connect(self._update_title)
        self.week.projectRequested.connect(self._open_project)
        self.week.newProjectRequested.connect(self._new_project)
        self.week.kindsRequested.connect(self._new_kind)
        self.week.projectsChanged.connect(self._projects_changed)
        self.projects.changed.connect(self._projects_changed)
        QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self.apply_theme())
        self._shortcuts()

        self.week.load(week_start(date.today()))
        self.apply_theme()
        geometry = db.get_setting(conn, "geometry")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
        central.setFocus()
        if not self.week.model.rows:
            self._status("Welcome! Start by creating the projects you work on.")

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("header")
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 10, 12, 10)
        row.setSpacing(10)
        self.logo = QLabel()
        self.logo.setPixmap(icons.pixmap("logo", "#000000", 26))
        name = QLabel("Timeless")
        name.setObjectName("title")
        self.navigator = WeekNavigator()
        self.views = Segmented(["Week", "Projects", "Report"], ["⌘1", "⌘2", "⌘3"])
        self.settings_btn = tool_button("gear", "Settings (⌘,)")
        self.help_btn = tool_button("help", "Help")
        self.settings_btn.clicked.connect(self.open_settings)
        self.help_btn.clicked.connect(lambda: HelpDialog(self).exec())
        row.addWidget(self.logo)
        row.addWidget(name)
        row.addSpacing(18)
        row.addWidget(self.navigator)
        row.addStretch(1)
        row.addWidget(self.views)
        row.addStretch(1)
        row.addWidget(self.settings_btn)
        row.addWidget(self.help_btn)
        return header

    def _shortcuts(self) -> None:
        for keys, slot in (
            # spelled out rather than StandardKey.Redo, whose keys differ between platforms
            ("Ctrl+Z", lambda: self.pages.currentIndex() == WEEK and self.week.undo()),
            ("Ctrl+Shift+Z", lambda: self.pages.currentIndex() == WEEK and self.week.redo()),
            ("Ctrl+[", self.navigator.prev_btn.click),
            ("Ctrl+]", self.navigator.next_btn.click),
            ("Ctrl+T", lambda: self.go_to_week(week_start(date.today()))),
            ("Ctrl+1", lambda: self.show_view(WEEK)),
            ("Ctrl+2", lambda: self.show_view(PROJECTS)),
            ("Ctrl+3", lambda: self.show_view(REPORT)),
            (QKeySequence.StandardKey.Preferences, self.open_settings),
            ("Ctrl+,", self.open_settings),
        ):
            QShortcut(QKeySequence(keys), self, slot)

    # ---- navigation ----------------------------------------------------------

    def show_view(self, index: int) -> None:
        self.week.view.commit_editor()
        self.week._commit_note()
        self.projects.notes.commit()
        self.views.set_current(index)
        self.navigator.setVisible(index == WEEK)
        if index == WEEK and self._stale:
            self._stale = False
            self.week.load()
        elif index == PROJECTS:
            self.projects.load()
        elif index == REPORT:
            self.report.load()
        self.pages.setCurrentIndex(index)
        self._update_title()

    def go_to_week(self, start: date) -> None:
        if self.pages.currentIndex() != WEEK:
            self.show_view(WEEK)
        self.week.load(start)
        self.navigator.set_week(start)

    def _open_project(self, project_id: int) -> None:
        self.show_view(PROJECTS)
        self.projects.show_project(project_id)

    def _new_project(self) -> None:
        self.show_view(PROJECTS)
        self.projects.new_project()

    def _new_kind(self) -> None:
        self.show_view(PROJECTS)
        self.projects.new_kind()

    def _projects_changed(self) -> None:
        self._stale = True

    # ---- chrome -------------------------------------------------------------------

    def _status(self, text: str) -> None:
        self.statusBar().showMessage(text, 8000)

    def _update_title(self) -> None:
        view = ("Week", "Projects", "Report")[self.pages.currentIndex()]
        suffix = week_label(self.week.model.start) if view == "Week" else view
        self.setWindowTitle(f"Timeless – {suffix}")

    def apply_theme(self) -> None:
        theme.apply(QApplication.instance())
        refresh_tool_icons(self.settings_btn, self.help_btn)
        self.navigator.refresh_icons()
        self.report.refresh_icons()
        self.week._refresh_summary()
        for view in (self.week.view, self.projects.project_view, self.projects.kind_view):
            view.viewport().update()
            view.horizontalHeader().viewport().update()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self, self.conn)
        dialog.appearanceChanged.connect(self.apply_theme)
        dialog.changed.connect(lambda: self.week.load())
        dialog.exec()

    def closeEvent(self, event) -> None:
        self.week.view.commit_editor()
        self.week._commit_note()
        db.set_setting(self.conn, "geometry", bytes(self.saveGeometry().toBase64()).decode())
        event.accept()
