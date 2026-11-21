CREATE TABLE IF NOT EXISTS asset_events (
    id INTEGER PRIMARY KEY,
    asset TEXT NOT NULL REFERENCES assets(id),
    action TEXT NOT NULL CHECK(action IN ('receive','commission','assign','transfer','return','relocate','write_off')),
    state TEXT NOT NULL CHECK(state IN ('stock','in_use','written_off')),
    employee TEXT REFERENCES employees(id),
    employee_name TEXT NOT NULL,
    day TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    actor_name TEXT NOT NULL,
    note TEXT NOT NULL,
    location TEXT NOT NULL
);
