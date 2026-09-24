"""Starts Timeless: python3 -m timeless"""
import getpass
import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from timeless.core import db
from timeless.ui import icons, theme
from timeless.ui.window import MainWindow


def _default_name() -> str:
    try:
        import pwd
        name = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
    except (ImportError, KeyError):
        name = ""
    return name or getpass.getuser()


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Timeless")
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(icons.pixmap("logo", "#000000", 256, dpr=1.0)))
    theme.apply(app)  # dark, until the saved appearance is known

    try:
        conn = db.connect()
    except Exception as exc:
        QMessageBox.critical(None, "Timeless", f"Couldn't open the database at\n{db.DB_PATH}\n\n{exc}")
        sys.exit(1)
    if db.get_setting(conn, "your_name") is None:
        db.set_setting(conn, "your_name", _default_name())
    theme.set_appearance(db.get_setting(conn, "appearance", theme.DEFAULT_APPEARANCE))
    theme.apply(app)
    try:
        db.daily_backup(conn)
    except Exception as exc:  # a failed backup must never stop anyone entering hours
        print(f"Daily backup failed: {exc}", file=sys.stderr)

    window = MainWindow(conn)
    window.show()
    sys.exit(app.exec())
