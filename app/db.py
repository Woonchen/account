"""SQLite：連線、建表、預設分類。資料檔位置由 ACCOUNT_DB 決定（預設 data/account.db）。"""
import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    pass_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    icon TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    UNIQUE (kind, name)
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    amount INTEGER NOT NULL CHECK (amount > 0),
    date TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
"""

DEFAULT_CATEGORIES = [
    ("expense", "餐飲", "🍜"), ("expense", "交通", "🚌"), ("expense", "購物", "🛍️"),
    ("expense", "居家", "🏠"), ("expense", "娛樂", "🎬"), ("expense", "醫療", "💊"),
    ("expense", "其他", "📦"),
    ("income", "薪資", "💼"), ("income", "獎金", "🎁"), ("income", "投資", "📈"),
    ("income", "其他收入", "💰"),
]


def db_path():
    return Path(os.environ.get("ACCOUNT_DB", "data/account.db"))


def connect(path=None):
    path = Path(path or db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init(conn):
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO categories (kind, name, icon, sort_order) VALUES (?, ?, ?, ?)",
            [(k, n, i, idx) for idx, (k, n, i) in enumerate(DEFAULT_CATEGORIES)],
        )
    conn.commit()
