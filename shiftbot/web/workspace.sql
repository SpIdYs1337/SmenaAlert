CREATE TABLE IF NOT EXISTS employees (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    login TEXT NOT NULL,
    login_key TEXT NOT NULL UNIQUE,
    grants TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    password_hash TEXT NOT NULL,
    must_change INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    login_enabled INTEGER NOT NULL DEFAULT 1 CHECK(login_enabled IN (0,1))
);
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    parent TEXT REFERENCES articles(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    edited_at TEXT NOT NULL,
    edited_by TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'article' CHECK(kind IN ('section','article')),
    html TEXT,
    archive_root TEXT REFERENCES articles(id)
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
    serial TEXT NOT NULL,
    service_months INTEGER CHECK(service_months BETWEEN 1 AND 600),
    service_revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS article_media (
    id TEXT PRIMARY KEY,
    article TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    seconds REAL,
    content BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS article_media_article ON article_media(article);
CREATE TABLE IF NOT EXISTS asset_photos (
    asset TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    content BLOB NOT NULL
);
