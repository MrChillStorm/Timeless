-- Timeless database (SQLite). Personal timesheet data: lives in the OS's
-- per-user data directory (see db.py), never inside the project folder.

-- What time gets booked to. There are no timecard "lines": every active
-- project is a row of every week, and archived ones only show up in the
-- weeks that have their hours.
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT NOT NULL DEFAULT '',          -- project / billing code, free text
    client TEXT NOT NULL DEFAULT '',        -- customer or area, free text
    billable INTEGER NOT NULL DEFAULT 1,
    -- the weekly plan: shown faintly in empty days, and what % of plan
    -- is measured against. Holidays never get planned hours.
    plan_mon REAL NOT NULL DEFAULT 0,
    plan_tue REAL NOT NULL DEFAULT 0,
    plan_wed REAL NOT NULL DEFAULT 0,
    plan_thu REAL NOT NULL DEFAULT 0,
    plan_fri REAL NOT NULL DEFAULT 0,
    plan_sat REAL NOT NULL DEFAULT 0,
    plan_sun REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Special kinds of hours (on-call, travel...), recorded on their own row
-- under a project. A kind can change billing: 'project' keeps the
-- project's setting, 'billable' / 'non_billable' override it.
CREATE TABLE IF NOT EXISTS kinds (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    billing TEXT NOT NULL DEFAULT 'project' CHECK (billing IN ('project', 'billable', 'non_billable')),
    created_at TEXT NOT NULL
);

-- One cell of the week grid: a project (and optional kind) on one day.
-- Saved the moment it's typed; an emptied cell's row is deleted.
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,                      -- ISO date
    project_id INTEGER NOT NULL REFERENCES projects(id),
    kind_id INTEGER REFERENCES kinds(id),   -- NULL = regular hours
    hours REAL NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS entries_cell ON entries(day, project_id, IFNULL(kind_id, 0));
CREATE INDEX IF NOT EXISTS entries_day ON entries(day);

-- Weeks marked done (reported, invoiced...): read-only until reopened.
CREATE TABLE IF NOT EXISTS done_weeks (
    week TEXT PRIMARY KEY,                  -- the Monday, ISO date
    done_at TEXT NOT NULL
);

-- Your name, full week, appearance, remembered choices.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
