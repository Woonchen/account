"""SQLite：連線、建表、遷移、新帳本的預設內容。資料檔位置由 ACCOUNT_DB 決定（預設 data/account.db）。

結構：帳本 ledgers → 每本帳本各自的成員 parties（誰付款／收款）與分類 categories → 收支記錄 transactions。
所有登入的帳號共用全部帳本。schema 版本記在 PRAGMA user_version。
"""
import os
import sqlite3
from pathlib import Path

VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    pass_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ledgers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS parties (
    id INTEGER PRIMARY KEY,
    ledger_id INTEGER NOT NULL REFERENCES ledgers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    UNIQUE (ledger_id, name)
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    ledger_id INTEGER NOT NULL REFERENCES ledgers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    icon TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    UNIQUE (ledger_id, kind, name)
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    ledger_id INTEGER NOT NULL REFERENCES ledgers(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    amount INTEGER NOT NULL CHECK (amount > 0),
    date TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    party_id INTEGER REFERENCES parties(id),  -- 舊版沒有這欄的記錄是 NULL；新記錄由 API 要求必填
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tx_ledger_date ON transactions(ledger_id, date);
"""

DEFAULT_PARTIES = ["成", "圓", "寶貝基金"]
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


def columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def create_schema(conn):
    # 不用 executescript：它會先自己 commit，遷移就不是同一個交易了
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            conn.execute(stmt)


def init(conn):
    cols = columns(conn, "categories")
    if cols and "ledger_id" not in cols:
        migrate_v0(conn)
    create_schema(conn)
    conn.execute(f"PRAGMA user_version = {VERSION}")
    conn.commit()


def migrate_v0(conn):
    """第一版沒有帳本：分類是共用的、記錄沒有成員。有記錄的話全部搬進一本「我的帳本」，成員欄留空。"""
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")  # 交易裡改不了，要在開始前關
    try:
        with conn:
            conn.execute("BEGIN")  # sqlite3 模組不會在 ALTER/CREATE 前自動開交易，要自己開，失敗才能整個還原
            conn.execute("ALTER TABLE transactions RENAME TO transactions_v0")
            conn.execute("ALTER TABLE categories RENAME TO categories_v0")
            create_schema(conn)
            if conn.execute("SELECT COUNT(*) FROM transactions_v0").fetchone()[0]:
                owner = conn.execute("SELECT MIN(id) FROM users").fetchone()[0]
                lid = conn.execute("INSERT INTO ledgers (name, created_by) VALUES ('我的帳本', ?)", (owner,)).lastrowid
                conn.executemany("INSERT INTO parties (ledger_id, name, sort_order) VALUES (?, ?, ?)",
                                 [(lid, n, i) for i, n in enumerate(DEFAULT_PARTIES)])
                conn.execute("""INSERT INTO categories (id, ledger_id, name, kind, icon, sort_order, archived)
                                SELECT id, ?, name, kind, icon, sort_order, archived FROM categories_v0""", (lid,))
                conn.execute("""INSERT INTO transactions (id, ledger_id, kind, amount, date, category_id, note,
                                                          created_by, created_at, updated_at)
                                SELECT id, ?, kind, amount, date, category_id, note, created_by, created_at, updated_at
                                FROM transactions_v0""", (lid,))
            conn.execute("DROP TABLE transactions_v0")
            conn.execute("DROP TABLE categories_v0")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def create_ledger(conn, name, parties, user_id, copy_categories_from=None):
    """新帳本：成員照給的名單；分類沿用指定帳本（不含封存的），沒指定就用預設分類。"""
    lid = conn.execute("INSERT INTO ledgers (name, created_by) VALUES (?, ?)", (name, user_id)).lastrowid
    conn.executemany("INSERT INTO parties (ledger_id, name, sort_order) VALUES (?, ?, ?)",
                     [(lid, n, i) for i, n in enumerate(parties)])
    if copy_categories_from is not None:
        conn.execute("""INSERT INTO categories (ledger_id, kind, name, icon, sort_order)
                        SELECT ?, kind, name, icon, sort_order FROM categories
                        WHERE ledger_id = ? AND archived = 0 ORDER BY sort_order, id""", (lid, copy_categories_from))
    else:
        conn.executemany("INSERT INTO categories (ledger_id, kind, name, icon, sort_order) VALUES (?, ?, ?, ?, ?)",
                         [(lid, k, n, ic, i) for i, (k, n, ic) in enumerate(DEFAULT_CATEGORIES)])
    return lid
