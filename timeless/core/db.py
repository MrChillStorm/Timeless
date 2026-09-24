"""Where the database lives, opening it, daily backups and settings."""
import os
import sqlite3
from datetime import date
from pathlib import Path

from platformdirs import user_data_dir

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# The OS's per-user data directory (on macOS ~/Library/Application
# Support/Timeless) -- deliberately outside the project folder, so
# personal data never ends up in a git commit. TIMELESS_DB points the app
# at another file, e.g. to try things out without touching the real data.
DATA_DIR = Path(user_data_dir("Timeless", appauthor=False))
DB_PATH = Path(os.environ["TIMELESS_DB"]) if os.environ.get("TIMELESS_DB") else DATA_DIR / "timeless.db"
BACKUP_DIR = DB_PATH.parent / "backups"
BACKUPS_KEPT = 30


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path) if path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    return conn


def daily_backup(conn: sqlite3.Connection, today: date | None = None, backup_dir: Path | None = None) -> Path | None:
    """Snapshots the database on the first launch of each day, keeping
    the newest BACKUPS_KEPT. Everything saves the moment it's typed, so
    this is what makes a bad day recoverable. Returns the backup's path,
    or None if today's exists or there's nothing to back up yet."""
    today = today or date.today()
    backup_dir = backup_dir or BACKUP_DIR
    target = backup_dir / f"timeless-{today.isoformat()}.db"
    if target.exists() or not conn.execute("SELECT 1 FROM projects LIMIT 1").fetchone():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = sqlite3.connect(target)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    for old in sorted(backup_dir.glob("timeless-*.db"), reverse=True)[BACKUPS_KEPT:]:
        old.unlink()
    return target


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
