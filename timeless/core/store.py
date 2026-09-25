"""Projects, kinds, the cells of the week grid, and done weeks.

Everything here writes straight through: the app has no Save button."""
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from timeless.core.calendar import DEFAULT_FULL_WEEK, fmt_hours, is_day_off
from timeless.core.db import get_setting, set_setting

PLAN_COLUMNS = ("plan_mon", "plan_tue", "plan_wed", "plan_thu", "plan_fri", "plan_sat", "plan_sun")
BILLING = {"project": "As project", "billable": "Billable", "non_billable": "Non-billable"}

Key = tuple[date, int, int | None]  # (day, project id, kind id or None)


class StoreError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- projects --------------------------------------------------------------

@dataclass
class Project:
    id: int | None = None
    name: str = ""
    code: str = ""
    client: str = ""
    billable: bool = True
    plan: list[float] = field(default_factory=lambda: [0.0] * 7)
    notes: str = ""
    archived: bool = False
    flex: bool = False  # flex time off: its hours take from the flex balance

    def plan_for(self, d: date) -> float:
        """Planned hours on a day: the weekly pattern, never on a day off."""
        return 0.0 if is_day_off(d) else self.plan[d.weekday()]

    def subtitle(self) -> str:
        parts = (self.code, self.client, "" if self.billable else "non-billable", "takes from flex" if self.flex else "")
        return "  ·  ".join(p for p in parts if p)

    def details(self) -> str:
        lines = [self.name]
        if self.code:
            lines.append(f"Code: {self.code}")
        if self.client:
            lines.append(f"Client: {self.client}")
        lines.append("Billable" if self.billable else "Non-billable")
        if self.flex:
            lines.append("Flex time off: its hours take from your flex balance")
        if any(self.plan):
            lines.append("Plan: " + " / ".join(fmt_hours(h) for h in self.plan))
        if self.notes.strip():
            lines += ["", self.notes.strip()]
        return "\n".join(lines)


def _project(row: sqlite3.Row) -> Project:
    return Project(row["id"], row["name"], row["code"], row["client"], bool(row["billable"]),
                   [row[c] for c in PLAN_COLUMNS], row["notes"], bool(row["archived"]), bool(row["flex"]))


def list_projects(conn: sqlite3.Connection, include_archived: bool = False) -> list[Project]:
    where = "" if include_archived else "WHERE archived = 0"
    return [_project(r) for r in conn.execute(f"SELECT * FROM projects {where} ORDER BY name COLLATE NOCASE, id")]


def save_project(conn: sqlite3.Connection, p: Project) -> int:
    """Inserts or updates; names must be unique (ignoring case)."""
    p.name = p.name.strip()
    if not p.name:
        raise StoreError("A project needs a name.")
    if conn.execute("SELECT 1 FROM projects WHERE name = ? COLLATE NOCASE AND id IS NOT ?", (p.name, p.id)).fetchone():
        raise StoreError(f"There's already a project called '{p.name}'.")
    values = {"name": p.name, "code": p.code.strip(), "client": p.client.strip(), "billable": int(p.billable),
              **{c: float(h) for c, h in zip(PLAN_COLUMNS, p.plan)}, "notes": p.notes, "archived": int(p.archived),
              "flex": int(p.flex)}
    if p.id is None:
        values["created_at"] = _now()
        p.id = conn.execute(f"INSERT INTO projects ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                            list(values.values())).lastrowid
    else:
        conn.execute(f"UPDATE projects SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
                     [*values.values(), p.id])
    conn.commit()
    return p.id


def unique_name(conn: sqlite3.Connection, table: str, base: str) -> str:
    """base, or base 2, base 3... -- for freshly created rows."""
    name, n = base, 1
    while conn.execute(f"SELECT 1 FROM {table} WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
        n += 1
        name = f"{base} {n}"
    return name


def hours_by_project(conn: sqlite3.Connection) -> dict[int, float]:
    return {r[0]: r[1] for r in conn.execute("SELECT project_id, SUM(hours) FROM entries GROUP BY project_id")}


def delete_project(conn: sqlite3.Connection, project_id: int) -> None:
    """Only for a project nothing was ever booked to; archive the rest."""
    if conn.execute("SELECT 1 FROM entries WHERE project_id = ? LIMIT 1", (project_id,)).fetchone():
        raise StoreError("This project has hours, so it can't be deleted. Archive it instead: "
                         "it leaves your weeks but keeps its history.")
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()


# ---- kinds -----------------------------------------------------------------

@dataclass
class Kind:
    id: int | None = None
    name: str = ""
    billing: str = "project"
    flex: bool = False  # extra hours: they go into the flex balance

    def billable_for(self, project: Project) -> bool:
        return project.billable if self.billing == "project" else self.billing == "billable"


def list_kinds(conn: sqlite3.Connection) -> list[Kind]:
    return [Kind(r["id"], r["name"], r["billing"], bool(r["flex"]))
            for r in conn.execute("SELECT * FROM kinds ORDER BY name COLLATE NOCASE, id")]


def save_kind(conn: sqlite3.Connection, k: Kind) -> int:
    k.name = k.name.strip()
    if not k.name:
        raise StoreError("A kind needs a name.")
    if k.billing not in BILLING:
        raise StoreError(f"Unknown billing '{k.billing}'.")
    if conn.execute("SELECT 1 FROM kinds WHERE name = ? COLLATE NOCASE AND id IS NOT ?", (k.name, k.id)).fetchone():
        raise StoreError(f"There's already a kind called '{k.name}'.")
    if k.id is None:
        k.id = conn.execute("INSERT INTO kinds (name, billing, flex, created_at) VALUES (?, ?, ?, ?)",
                            (k.name, k.billing, int(k.flex), _now())).lastrowid
    else:
        conn.execute("UPDATE kinds SET name = ?, billing = ?, flex = ? WHERE id = ?",
                     (k.name, k.billing, int(k.flex), k.id))
    conn.commit()
    return k.id


def hours_by_kind(conn: sqlite3.Connection) -> dict[int, float]:
    return {r[0]: r[1] for r in
            conn.execute("SELECT kind_id, SUM(hours) FROM entries WHERE kind_id IS NOT NULL GROUP BY kind_id")}


def delete_kind(conn: sqlite3.Connection, kind_id: int) -> None:
    if conn.execute("SELECT 1 FROM entries WHERE kind_id = ? LIMIT 1", (kind_id,)).fetchone():
        raise StoreError("This kind has hours booked, so it can't be deleted. You can still rename it.")
    conn.execute("DELETE FROM kinds WHERE id = ?", (kind_id,))
    conn.commit()


# ---- cells -----------------------------------------------------------------

def week_cells(conn: sqlite3.Connection, days: list[date]) -> dict[Key, tuple[float, str]]:
    rows = conn.execute("SELECT day, project_id, kind_id, hours, note FROM entries WHERE day BETWEEN ? AND ?",
                        (days[0].isoformat(), days[-1].isoformat()))
    return {(date.fromisoformat(r["day"]), r["project_id"], r["kind_id"]): (r["hours"], r["note"]) for r in rows}


def set_cell(conn: sqlite3.Connection, key: Key, hours: float, note: str) -> None:
    """Writes one cell; a cell with no hours and no note is removed."""
    day, project_id, kind_id = key
    where = "day = ? AND project_id = ? AND IFNULL(kind_id, 0) = ?"
    args = (day.isoformat(), project_id, kind_id or 0)
    with conn:
        if not hours and not note.strip():
            conn.execute(f"DELETE FROM entries WHERE {where}", args)
        elif conn.execute(f"SELECT 1 FROM entries WHERE {where}", args).fetchone():
            conn.execute(f"UPDATE entries SET hours = ?, note = ?, updated_at = ? WHERE {where}",
                         (hours, note.strip(), _now(), *args))
        else:
            conn.execute("INSERT INTO entries (day, project_id, kind_id, hours, note, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (day.isoformat(), project_id, kind_id, hours, note.strip(), _now()))


# ---- flex hours ------------------------------------------------------------
# Extra hours go on a flex kind's row under the project they were worked
# on, once the day has a full day of other work; time taken off goes on
# the flex time-off project. The balance is the starting balance (from
# before Timeless) plus the one, minus the other.

FLEX_KIND, FLEX_PROJECT = "Flex", "Flex time off"


def flex_enabled(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, "flex") == "1"


def full_day(conn: sqlite3.Connection) -> float:
    """A normal working day, which flex hours go on top of. Until it's set
    on its own, a fifth of the full week."""
    week = float(get_setting(conn, "full_week", str(DEFAULT_FULL_WEEK)) or DEFAULT_FULL_WEEK)
    return float(get_setting(conn, "full_day", "") or round(week / 5, 2))


def flex_start(conn: sqlite3.Connection) -> float:
    return float(get_setting(conn, "flex_start", "0") or 0)


def flex_between(conn: sqlite3.Connection, first: date | None, last: date) -> tuple[float, float]:
    """(earned, used) from first (None: the very beginning) to last,
    both included."""
    span = ((first.isoformat() if first else ""), last.isoformat())
    earned = conn.execute("SELECT IFNULL(SUM(e.hours), 0) FROM entries e JOIN kinds k ON k.id = e.kind_id "
                          "WHERE k.flex = 1 AND e.day BETWEEN ? AND ?", span).fetchone()[0]
    used = conn.execute("SELECT IFNULL(SUM(e.hours), 0) FROM entries e JOIN projects p ON p.id = e.project_id "
                        "WHERE p.flex = 1 AND e.day BETWEEN ? AND ?", span).fetchone()[0]
    return round(earned, 2), round(used, 2)


def set_flex(conn: sqlite3.Connection, on: bool) -> None:
    """On: makes sure there's a flex kind and a flex time-off project,
    taking over ones already called that and restoring an archived one.
    Off: they're deleted if they were never used, and a used project is
    archived. No hours are touched either way."""
    set_setting(conn, "flex", "1" if on else "0")
    projects = list_projects(conn, include_archived=True)
    kinds = list_kinds(conn)
    if on:
        if not any(k.flex for k in kinds):
            k = next((k for k in kinds if k.name.lower() == FLEX_KIND.lower()), None) or Kind(name=FLEX_KIND)
            k.flex = True
            save_kind(conn, k)
        flex = [p for p in projects if p.flex] or [
            next((p for p in projects if p.name.lower() == FLEX_PROJECT.lower()), None)
            or Project(name=FLEX_PROJECT, billable=False)]
        for p in flex:
            p.flex, p.archived = True, False
            save_project(conn, p)
        return
    booked, kind_hours = hours_by_project(conn), hours_by_kind(conn)
    for p in projects:
        if p.flex and booked.get(p.id):
            p.archived = True
            save_project(conn, p)
        elif p.flex:
            delete_project(conn, p.id)
    for k in kinds:
        if k.flex and not kind_hours.get(k.id):
            delete_kind(conn, k.id)


# ---- done weeks ------------------------------------------------------------

def is_done(conn: sqlite3.Connection, start: date) -> bool:
    return conn.execute("SELECT 1 FROM done_weeks WHERE week = ?", (start.isoformat(),)).fetchone() is not None


def set_done(conn: sqlite3.Connection, start: date, done: bool) -> None:
    with conn:
        if done:
            conn.execute("INSERT OR IGNORE INTO done_weeks (week, done_at) VALUES (?, ?)", (start.isoformat(), _now()))
        else:
            conn.execute("DELETE FROM done_weeks WHERE week = ?", (start.isoformat(),))
