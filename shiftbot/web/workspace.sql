CREATE TABLE IF NOT EXISTS employees (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    login TEXT NOT NULL,
    login_key TEXT NOT NULL UNIQUE,
    grants TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    password_hash TEXT NOT NULL,
    must_change INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    parent TEXT REFERENCES articles(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    edited_at TEXT NOT NULL,
    edited_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS articles_parent ON articles(parent);
CREATE TABLE IF NOT EXISTS documents (
    article TEXT PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    content BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    tag TEXT NOT NULL,
    tag_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    serial TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_events (
    id INTEGER PRIMARY KEY,
    asset TEXT NOT NULL REFERENCES assets(id),
    action TEXT NOT NULL CHECK(action IN ('receive','commission','assign','transfer','return')),
    state TEXT NOT NULL CHECK(state IN ('stock','in_use')),
    employee TEXT REFERENCES employees(id),
    employee_name TEXT NOT NULL,
    day TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    actor_name TEXT NOT NULL,
    note TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS asset_events_asset ON asset_events(asset,id);
