"""The grid kit shared by the Week and Projects views: data roles, a
painted header, one delegate that draws and edits every kind of cell, and
a table view with spreadsheet keys.

Models describe cells through KIND_ROLE and a few styling roles; the
delegate does all the drawing."""
import shiboken6
from PySide6.QtCore import (
    QEvent, QModelIndex, QPersistentModelIndex, QPointF, QRect, QRectF, QRegularExpression, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemDelegate, QAbstractItemView, QApplication, QComboBox, QHeaderView, QLineEdit, QStyle,
    QStyledItemDelegate, QTableView,
)

from timeless.core.calendar import fmt_hours, parse_hours, step_half_hours
from timeless.ui.theme import colors
from timeless.ui.widgets import WheelSteps

_BASE = int(Qt.ItemDataRole.UserRole)
KIND_ROLE = _BASE + 1
GHOST_ROLE = _BASE + 2        # hours cell: the planned value, shown faintly while empty
NOTE_ROLE = _BASE + 3         # hours cell has a note
DAY_OFF_ROLE = _BASE + 4      # weekend / holiday shading (cells and header)
TOTAL_ROLE = _BASE + 5        # the totals row
LOCKED_ROLE = _BASE + 6       # a done week: read-only look
SECONDARY_ROLE = _BASE + 7    # second line under a name
WARN_ROLE = _BASE + 8         # warning color
TODAY_ROLE = _BASE + 9        # header: today's column
HOLIDAY_ROLE = _BASE + 10     # header: a public holiday (red label)
CHECKED_ROLE = _BASE + 11
OPTIONS_ROLE = _BASE + 12     # choice cells: [(label, value)]
PROGRESS_ROLE = _BASE + 13    # plan cells: entered / planned, or None
SUBROW_ROLE = _BASE + 14      # an indented kind row under a project
PLACEHOLDER_ROLE = _BASE + 15
HEIGHT_ROLE = _BASE + 16      # row height

DISPLAY = Qt.ItemDataRole.DisplayRole
EDIT = Qt.ItemDataRole.EditRole
TOOLTIP = Qt.ItemDataRole.ToolTipRole
ALIGN = Qt.ItemDataRole.TextAlignmentRole
H = Qt.Orientation.Horizontal
RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
HEADER_HEIGHT = 46


class Kind:
    NAME = "name"        # project name with a muted second line, or an indented kind row
    TEXT = "text"        # editable text
    HOURS = "hours"      # editable hours (ghost plan, note dot)
    SUM = "sum"          # bold total
    MUTED = "muted"      # read-only muted number
    PLAN = "plan"        # progress bar against the plan
    CHECK = "check"
    CHOICE = "choice"    # pick from a short list


def _checkbox(painter: QPainter, rect: QRect, checked: bool, enabled: bool) -> None:
    c = colors()
    box = QRectF(rect.x() + rect.width() / 2 - 7.5, rect.y() + rect.height() / 2 - 7.5, 15, 15)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setOpacity(1.0 if enabled else 0.45)
    if checked:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(c["check_bg"]))
        painter.drawRoundedRect(box, 4, 4)
        pen = QPen(QColor(c["check_mark"]), 1.9)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QPainterPath()
        path.moveTo(box.left() + 3.6, box.center().y() + 0.3)
        path.lineTo(box.left() + 6.4, box.bottom() - 3.7)
        path.lineTo(box.right() - 3.3, box.top() + 3.8)
        painter.drawPath(path)
    else:
        painter.setPen(QPen(QColor(c["check_border"]), 1))
        painter.setBrush(QColor(c["surface"]))
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
    painter.restore()


class GridHeader(QHeaderView):
    """Muted small caps, two-line day headers, day-off shading, today in
    the accent color and public holidays in red."""

    def __init__(self, parent=None):
        super().__init__(H, parent)
        self.setHighlightSections(False)
        self.setSectionsClickable(False)
        self.setFixedHeight(HEADER_HEIGHT)

    def paintSection(self, painter: QPainter, rect: QRect, logical: int) -> None:
        if not rect.isValid():
            return
        c = colors()
        model = self.model()
        painter.save()
        bg = c["day_off"] if model.headerData(logical, H, DAY_OFF_ROLE) else c["panel"]
        painter.fillRect(rect, QColor(bg))
        painter.setPen(QColor(c["border"]))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        text = model.headerData(logical, H, DISPLAY) or ""
        today = bool(model.headerData(logical, H, TODAY_ROLE))
        holiday = bool(model.headerData(logical, H, HOLIDAY_ROLE))
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        painter.setFont(font)
        painter.setPen(QColor(c["accent"] if today else c["danger"] if holiday else c["muted"]))
        align = model.headerData(logical, H, ALIGN) or int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        inner = rect.adjusted(12, 0, -12, 0)
        fm = painter.fontMetrics()
        text = "\n".join(fm.elidedText(t, Qt.TextElideMode.ElideRight, inner.width()) for t in text.split("\n"))
        painter.drawText(inner, Qt.AlignmentFlag(align), text)
        painter.restore()


class HoursEditor(QLineEdit):
    """The in-cell hours box. Up/Down move to the same day on the next or
    previous row; Enter goes on to the note; the wheel steps half hours."""
    verticalMove = Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"^\d{0,2}([.,]\d{0,2}|:\d{0,2})?[hH]?$"), self))
        self._wheel = WheelSteps()

    def wheelEvent(self, event) -> None:
        steps = self._wheel.take(event)
        if steps:
            try:
                current = parse_hours(self.text())
            except ValueError:
                current = 0.0
            self.setText(fmt_hours(step_half_hours(current, steps), blank_zero=True))
            self.selectAll()
        event.accept()

    def keyPressEvent(self, event) -> None:
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and mods == Qt.KeyboardModifier.NoModifier:
            self.verticalMove.emit(-1 if event.key() == Qt.Key.Key_Up else 1)
            return
        super().keyPressEvent(event)


class CellDelegate(QStyledItemDelegate):
    navigateRequested = Signal(int, int, int)  # row, column, row delta
    editorReturned = Signal(int, int)          # Enter pressed in an hours editor

    def __init__(self, view: "GridView"):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, index.data(HEIGHT_ROLE) or 38)

    # ---- painting -------------------------------------------------------

    def paint(self, painter: QPainter, option, index) -> None:
        c = colors()
        kind = index.data(KIND_ROLE)
        rect = option.rect
        total = bool(index.data(TOTAL_ROLE))
        painter.save()
        bg = c["panel"] if total else c["day_off"] if index.data(DAY_OFF_ROLE) else c["surface"]
        painter.fillRect(rect, QColor(bg))
        selected = bool(option.state & QStyle.StateFlag.State_Selected) and not total
        if selected:
            painter.fillRect(rect, QColor(c["tint"]))
        painter.setPen(QColor(c["border_strong"] if total else c["border"]))
        line_y = rect.top() if total else rect.bottom()
        painter.drawLine(rect.left(), line_y, rect.right(), line_y)

        font = QFont(option.font)
        font.setPixelSize(13)
        painter.setFont(font)
        self._paint_content(painter, rect, index, kind, total, c)

        if option.state & QStyle.StateFlag.State_HasFocus and not total and kind not in (Kind.PLAN,):
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(c["accent_strong"]), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(rect).adjusted(1.5, 1.5, -1.5, -1.5), 4, 4)
        painter.restore()

    def _paint_content(self, painter, rect, index, kind, total, c) -> None:
        text = index.data(DISPLAY) or ""
        locked = bool(index.data(LOCKED_ROLE))
        ink = QColor(c["muted"] if locked else c["ink"])
        fm = painter.fontMetrics()
        left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        warn = bool(index.data(WARN_ROLE))

        if kind == Kind.NAME:
            if index.data(SUBROW_ROLE):
                painter.setPen(QColor(c["muted"]))
                painter.drawText(rect.adjusted(34, 0, -8, 0), left,
                                 fm.elidedText("↳  " + text, Qt.TextElideMode.ElideRight, rect.width() - 42))
                return
            secondary = index.data(SECONDARY_ROLE)
            f = painter.font()
            f.setPixelSize(13 if not total else 12)
            f.setWeight(QFont.Weight.DemiBold if total else QFont.Weight.Medium)
            painter.setFont(f)
            painter.setPen(QColor(c["muted"]) if total else ink)
            avail = rect.width() - 28
            if secondary:
                top = QRect(rect.left() + 14, rect.top() + 5, avail, rect.height() // 2 - 3)
                painter.drawText(top, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                                 painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, avail))
                f.setPixelSize(11)
                f.setWeight(QFont.Weight.Normal)
                painter.setFont(f)
                painter.setPen(QColor(c["faint"] if locked else c["muted"]))
                bottom = QRect(rect.left() + 14, rect.center().y() + 2, avail, rect.height() // 2 - 5)
                painter.drawText(bottom, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                                 painter.fontMetrics().elidedText(secondary, Qt.TextElideMode.ElideRight, avail))
            else:
                painter.drawText(rect.adjusted(14, 0, -14, 0), left,
                                 painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, avail))

        elif kind == Kind.TEXT:
            if text:
                painter.setPen(ink)
                painter.drawText(rect.adjusted(12, 0, -12, 0), left,
                                 fm.elidedText(text, Qt.TextElideMode.ElideRight, rect.width() - 24))
            elif index.data(PLACEHOLDER_ROLE):
                painter.setPen(QColor(c["faint"]))
                painter.drawText(rect.adjusted(12, 0, -12, 0), left, index.data(PLACEHOLDER_ROLE))

        elif kind in (Kind.HOURS, Kind.SUM, Kind.MUTED):
            if kind == Kind.SUM or total:
                f = painter.font()
                f.setWeight(QFont.Weight.DemiBold)
                painter.setFont(f)
            color = c["danger"] if warn else c["muted"] if (kind == Kind.MUTED or total or locked) else c["ink"]
            if text:
                painter.setPen(QColor(color))
                painter.drawText(rect.adjusted(6, 0, -12, 0), right, text)
            elif index.data(GHOST_ROLE):
                f = painter.font()
                f.setItalic(True)
                painter.setFont(f)
                painter.setPen(QColor(c["faint"]))
                painter.drawText(rect.adjusted(6, 0, -12, 0), right, index.data(GHOST_ROLE))
            if index.data(NOTE_ROLE):
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(c["accent"]))
                painter.drawEllipse(QRectF(rect.right() - 9.5, rect.top() + 5.5, 5, 5))

        elif kind == Kind.PLAN:
            progress = index.data(PROGRESS_ROLE)
            if progress is None:
                return
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            bar = QRectF(rect.left() + 12, rect.center().y() - 3, rect.width() - 72, 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["track"]))
            painter.drawRoundedRect(bar, 3, 3)
            over, full = progress > 1.005, progress >= 0.995
            fill_color = c["warning"] if over else c["success"] if full else c["accent_strong"]
            if progress > 0:
                fill = QRectF(bar.left(), bar.top(), max(6.0, bar.width() * min(progress, 1.0)), bar.height())
                painter.setBrush(QColor(fill_color))
                painter.drawRoundedRect(fill, 3, 3)
            f = painter.font()
            f.setPixelSize(12)
            painter.setFont(f)
            painter.setPen(QColor(c["warning"] if over else c["muted"]))
            painter.drawText(rect.adjusted(0, 0, -12, 0), right, text)

        elif kind == Kind.CHECK:
            _checkbox(painter, rect, bool(index.data(CHECKED_ROLE)),
                      bool(index.flags() & Qt.ItemFlag.ItemIsUserCheckable))

        elif kind == Kind.CHOICE:
            painter.setPen(ink)
            painter.drawText(rect.adjusted(12, 0, -28, 0), left, text)
            if index.flags() & Qt.ItemFlag.ItemIsEditable:
                painter.setPen(QPen(QColor(c["muted"]), 1.6))
                x, y = rect.right() - 17, rect.center().y()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.drawPolyline([QPointF(x, y - 2), QPointF(x + 4, y + 2), QPointF(x + 8, y - 2)])

    # ---- editing --------------------------------------------------------

    def createEditor(self, parent, option, index):
        kind = index.data(KIND_ROLE)
        persistent = QPersistentModelIndex(index)
        if kind == Kind.HOURS:
            editor = HoursEditor(parent)
            editor.verticalMove.connect(lambda delta: self._finish(editor, persistent, delta))
            editor.returnPressed.connect(
                lambda: persistent.isValid() and QTimer.singleShot(
                    0, lambda: self.editorReturned.emit(persistent.row(), persistent.column())))
            return editor
        if kind == Kind.TEXT:
            return QLineEdit(parent)
        if kind == Kind.CHOICE:
            combo = QComboBox(parent)
            for label, value in index.data(OPTIONS_ROLE) or []:
                combo.addItem(label, value)
            combo.activated.connect(lambda _i: self._finish(combo, persistent, 0))
            if self.view.opened_by_mouse():
                QTimer.singleShot(0, lambda: shiboken6.isValid(combo) and combo.isVisible() and combo.showPopup())
            return combo
        return None

    def _finish(self, editor, persistent: QPersistentModelIndex, delta: int) -> None:
        if not shiboken6.isValid(editor):
            return
        self.commitData.emit(editor)
        self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)
        if delta and persistent.isValid():
            self.navigateRequested.emit(persistent.row(), persistent.column(), delta)

    def setEditorData(self, editor, index) -> None:
        kind = index.data(KIND_ROLE)
        value = index.data(EDIT)
        if kind == Kind.HOURS:
            editor.setText(fmt_hours(value or 0, blank_zero=True))
            editor.selectAll()
        elif kind == Kind.TEXT:
            editor.setText(value or "")
            editor.selectAll()
        elif kind == Kind.CHOICE:
            i = editor.findData(value)
            editor.setCurrentIndex(max(0, i))

    def setModelData(self, editor, model, index) -> None:
        kind = index.data(KIND_ROLE)
        if kind == Kind.HOURS:
            try:
                model.setData(index, parse_hours(editor.text()), EDIT)
            except ValueError as exc:
                QApplication.beep()
                self.view.message.emit(str(exc))
        elif kind == Kind.TEXT:
            model.setData(index, editor.text().strip(), EDIT)
        elif kind == Kind.CHOICE:
            model.setData(index, editor.currentData(), EDIT)

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect.adjusted(1, 1, -1, -1))

    def editorEvent(self, event, model, option, index) -> bool:
        if index.data(KIND_ROLE) == Kind.CHECK and index.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            t = event.type()
            if t == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                model.setData(index, not index.data(CHECKED_ROLE), CHECKED_ROLE)
                return True
            if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
                return True
            if t == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space:
                model.setData(index, not index.data(CHECKED_ROLE), CHECKED_ROLE)
                return True
        return super().editorEvent(event, model, option, index)


class GridView(QTableView):
    """Spreadsheet keys: arrows move, typing replaces, Tab skips read-only
    cells, Delete clears. Enter either edits (return_edits) or is handed to
    the owner (the Week view uses it to jump to the note)."""
    message = Signal(str)
    returnPressed = Signal(QModelIndex)
    clearRequested = Signal(QModelIndex)
    acceptRequested = Signal(QModelIndex)  # "=": take the planned value

    def __init__(self, return_edits: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("grid")  # the stylesheet's grid rules must not reach other tables
        self.return_edits = return_edits
        self._last_trigger = None
        self.setHorizontalHeader(GridHeader(self))
        self.horizontalHeader().setMinimumSectionSize(20)
        vh = self.verticalHeader()
        vh.hide()
        vh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.setShowGrid(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        T = QAbstractItemView.EditTrigger
        self.setEditTriggers(T.AnyKeyPressed | T.DoubleClicked)
        self.setWordWrap(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setMouseTracking(True)
        self.delegate = CellDelegate(self)
        self.setItemDelegate(self.delegate)
        self.delegate.navigateRequested.connect(self._navigate)
        self._wheel = WheelSteps()

    def edit(self, index, trigger=None, event=None):
        if trigger is None:
            return super().edit(index)
        self._last_trigger = trigger
        return super().edit(index, trigger, event)

    def opened_by_mouse(self) -> bool:
        return self._last_trigger == QAbstractItemView.EditTrigger.DoubleClicked or \
            bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)

    def commit_editor(self) -> None:
        index = self.currentIndex()
        if self.state() == QAbstractItemView.State.EditingState and index.isValid():
            editor = self.indexWidget(index)
            if editor is not None:
                self.commitData(editor)
                self.closeEditor(editor, QAbstractItemDelegate.EndEditHint.NoHint)

    def keyPressEvent(self, event) -> None:
        index = self.currentIndex()
        editing = self.state() == QAbstractItemView.State.EditingState
        key = event.key()
        if index.isValid() and not editing:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self.return_edits and index.flags() & Qt.ItemFlag.ItemIsEditable:
                    self.edit(index)
                else:
                    self.returnPressed.emit(index)
                return
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.clearRequested.emit(index)
                return
            if event.text() == "=":
                self.acceptRequested.emit(index)
                return
        super().keyPressEvent(event)

    def moveCursor(self, action, modifiers):
        A = QAbstractItemView.CursorAction
        if action not in (A.MoveNext, A.MovePrevious):
            return super().moveCursor(action, modifiers)
        model = self.model()
        rows, cols = model.rowCount(), model.columnCount()
        current = self.currentIndex()
        pos = current.row() * cols + current.column() if current.isValid() else -1
        step = 1 if action == A.MoveNext else -1
        for _ in range(rows * cols):
            pos = (pos + step) % (rows * cols)
            idx = model.index(*divmod(pos, cols))
            if idx.flags() & Qt.ItemFlag.ItemIsEditable:
                return idx
        return current

    def _navigate(self, row: int, col: int, delta: int) -> None:
        model = self.model()
        r = row + delta
        while 0 <= r < model.rowCount() and not model.index(r, col).flags() & Qt.ItemFlag.ItemIsSelectable:
            r += delta
        if 0 <= r < model.rowCount():
            QTimer.singleShot(0, lambda: self.setCurrentIndex(model.index(r, col)))

    def wheelEvent(self, event) -> None:
        """Over the selected hours cell the wheel steps it by half hours
        (the model may merge a burst into one undo step); anywhere else it
        scrolls the grid as usual."""
        index = self.indexAt(event.position().toPoint())
        if (index.isValid() and index == self.currentIndex() and index.data(KIND_ROLE) == Kind.HOURS
                and index.flags() & Qt.ItemFlag.ItemIsEditable
                and self.state() != QAbstractItemView.State.EditingState):
            steps = self._wheel.take(event)
            if steps:
                hours = step_half_hours(index.data(EDIT) or 0.0, steps)
                nudge = getattr(self.model(), "nudge", None)
                (nudge(index, hours) if nudge else self.model().setData(index, hours, EDIT))
            event.accept()
            return
        super().wheelEvent(event)

    def mouseMoveEvent(self, event) -> None:
        index = self.indexAt(event.position().toPoint())
        clickable = index.isValid() and index.data(KIND_ROLE) == Kind.CHECK and \
            bool(index.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)
