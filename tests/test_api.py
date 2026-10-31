import datetime as dt
import importlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("ACCOUNT_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ACCOUNT_INSECURE_COOKIE", "1")
    from app import auth, db, main
    importlib.reload(main)  # 每個測試重新讀環境變數、清空封鎖記錄
    monkeypatch.setattr(main.time, "sleep", lambda _s: None)
    conn = db.connect()
    db.init(conn)
    for u, name in (("nick", "Nick"), ("mei", "小美")):
        conn.execute("INSERT INTO users (username, display_name, pass_hash) VALUES (?, ?, ?)",
                     (u, name, auth.make_hash("password123", 1000)))
    conn.commit()
    conn.close()
    with TestClient(main.app) as c:
        yield c


def login(c, user="nick", pw="password123"):
    return c.post("/api/login", json={"username": user, "password": pw}, headers=ORIGIN)


def post(c, url, body):
    return c.post(url, json=body, headers=ORIGIN)


def patch(c, url, body):
    return c.patch(url, json=body, headers=ORIGIN)


def delete(c, url):
    return c.delete(url, headers=ORIGIN)


def new_ledger(c, name="家用", **kw):
    r = post(c, "/api/ledgers", {"name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def pick(led, kind="expense", cat="餐飲", party="成"):
    return {"kind": kind, "category_id": next(x["id"] for x in led["categories"] if x["name"] == cat),
            "party_id": next(x["id"] for x in led["parties"] if x["name"] == party)}


def add(c, led, cat="餐飲", party="成", kind="expense", **kw):
    body = {"amount": 120, "date": "2026-11-01", "note": "午餐"} | pick(led, kind, cat, party) | kw
    return post(c, f"/api/ledgers/{led['id']}/transactions", body)


# ---------- 登入 ----------
def test_health_and_index_no_login(client):
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/").status_code == 200
    assert client.head("/").status_code == 200


def test_requires_login(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/ledgers").status_code == 401
    assert client.get("/api/ledgers/1/dashboard").status_code == 401


def test_login_and_logout(client):
    assert login(client, pw="wrong").status_code == 401
    r = login(client)
    assert r.status_code == 200 and r.json()["display_name"] == "Nick"
    assert "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/me").json()["username"] == "nick"
    client.post("/api/logout", headers=ORIGIN)
    assert client.get("/api/me").status_code == 401


def test_block_after_three_failures(client):
    assert login(client, pw="x").status_code == 401
    assert login(client, user="nobody", pw="x").status_code == 401
    assert login(client, pw="x").status_code == 429
    assert login(client).status_code == 429  # 封鎖期間密碼對也不行


def test_tampered_cookie_rejected(client):
    login(client)
    v = client.cookies.get("account_session")
    client.cookies.set("account_session", v[:-1] + ("0" if v[-1] != "0" else "1"))
    assert client.get("/api/me").status_code == 401


def test_password_change_invalidates_session(client):
    from app import auth, db
    login(client)
    conn = db.connect()
    conn.execute("UPDATE users SET pass_hash = ? WHERE username = 'nick'", (auth.make_hash("another-pass", 1000),))
    conn.commit()
    assert client.get("/api/me").status_code == 401


def test_cross_origin_write_blocked(client):
    login(client)
    assert client.post("/api/ledgers", json={"name": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403


# ---------- 帳本 ----------
def test_ledger_create_with_default_parties(client):
    login(client)
    assert client.get("/api/ledgers").json() == []
    led = new_ledger(client)
    assert [p["name"] for p in led["parties"]] == ["成", "圓", "寶貝基金"]
    assert {c["kind"] for c in led["categories"]} == {"income", "expense"}
    assert [x["name"] for x in client.get("/api/ledgers").json()] == ["家用"]


def test_ledger_custom_parties_validated(client):
    login(client)
    assert new_ledger(client, "旅行", parties=[" 成 ", "圓", ""])["parties"][0]["name"] == "成"
    assert post(client, "/api/ledgers", {"name": "x", "parties": ["成", "成"]}).status_code == 422
    assert post(client, "/api/ledgers", {"name": "x", "parties": ["  "]}).status_code == 422
    assert post(client, "/api/ledgers", {"name": ""}).status_code == 422


def test_new_ledger_copies_categories(client):
    login(client)
    oct_ = new_ledger(client, "10 月")
    url = f"/api/ledgers/{oct_['id']}/categories"
    post(client, url, {"name": "尿布", "kind": "expense", "icon": "🧷"})
    food = next(c for c in oct_["categories"] if c["name"] == "餐飲")
    patch(client, f"{url}/{food['id']}", {"archived": True})
    nov = new_ledger(client, "11 月", parties=["成", "圓"], copy_categories_from=oct_["id"])
    names = [c["name"] for c in nov["categories"] if c["kind"] == "expense"]
    assert "尿布" in names and "餐飲" not in names  # 封存的不沿用
    assert [p["name"] for p in nov["parties"]] == ["成", "圓"]
    assert all(c["id"] not in {x["id"] for x in oct_["categories"]} for c in nov["categories"])  # 是複製，不是共用
    assert post(client, "/api/ledgers", {"name": "x", "copy_categories_from": 999}).status_code == 404


def test_ledger_rename_archive_delete(client):
    login(client)
    led = new_ledger(client)
    assert patch(client, f"/api/ledgers/{led['id']}", {"name": "家庭"}).json()["name"] == "家庭"
    add(client, led)
    assert delete(client, f"/api/ledgers/{led['id']}").status_code == 409  # 有記錄只能封存
    assert patch(client, f"/api/ledgers/{led['id']}", {"archived": True}).json()["archived"] is True
    empty = new_ledger(client, "空的")
    assert delete(client, f"/api/ledgers/{empty['id']}").status_code == 200
    assert client.get(f"/api/ledgers/{empty['id']}").status_code == 404


def test_ledgers_are_separate(client):
    login(client)
    a, b = new_ledger(client, "A"), new_ledger(client, "B")
    add(client, a)
    assert len(client.get(f"/api/ledgers/{a['id']}/transactions?month=2026-11").json()["items"]) == 1
    assert client.get(f"/api/ledgers/{b['id']}/transactions?month=2026-11").json()["items"] == []
    # 不能用 A 的分類、成員記到 B
    assert post(client, f"/api/ledgers/{b['id']}/transactions",
                {"amount": 1, "date": "2026-11-01"} | pick(a)).status_code == 404
    # 也不能用 B 的網址改 A 的記錄
    tid = client.get(f"/api/ledgers/{a['id']}/transactions?month=2026-11").json()["items"][0]["id"]
    assert delete(client, f"/api/ledgers/{b['id']}/transactions/{tid}").status_code == 404


# ---------- 成員 ----------
def test_parties(client):
    login(client)
    led = new_ledger(client)
    url = f"/api/ledgers/{led['id']}/parties"
    p = post(client, url, {"name": "阿嬤"}).json()
    assert post(client, url, {"name": "阿嬤"}).status_code == 409
    assert patch(client, f"{url}/{p['id']}", {"name": "外婆"}).json()["name"] == "外婆"
    assert delete(client, f"{url}/{p['id']}").status_code == 200
    cheng = led["parties"][0]["id"]
    add(client, led)
    assert delete(client, f"{url}/{cheng}").status_code == 409
    assert patch(client, f"{url}/{cheng}", {"archived": True}).json()["archived"] is True


# ---------- 收支記錄 ----------
def test_transaction_crud_and_summary(client):
    login(client)
    led = new_ledger(client)
    base = f"/api/ledgers/{led['id']}/transactions"
    r = add(client, led)
    assert r.status_code == 201
    t = r.json()
    assert (t["created_by"], t["category_name"], t["party_name"]) == ("Nick", "餐飲", "成")
    add(client, led, "薪資", "圓", "income", amount=50000, note="")
    add(client, led, date="2026-10-31")  # 給月份時別的月份不算

    data = client.get(f"{base}?month=2026-11").json()
    assert len(data["items"]) == 2
    assert data["summary"] == {"income": 50000, "expense": 120, "balance": 49880}
    # 不給月份就是整本帳本（一個月一本帳本的用法）
    data = client.get(base).json()
    assert len(data["items"]) == 3 and data["summary"]["expense"] == 240
    assert [i["date"] for i in data["items"]] == ["2026-11-01", "2026-11-01", "2026-10-31"]
    yuan = led["parties"][1]["id"]
    assert [i["party_name"] for i in client.get(f"{base}?month=2026-11&party_id={yuan}").json()["items"]] == ["圓"]

    body = {"amount": 150, "date": "2026-11-02", "note": "晚餐"} | pick(led, party="寶貝基金")
    r = patch(client, f"{base}/{t['id']}", body)
    assert r.status_code == 200 and r.json()["amount"] == 150 and r.json()["party_name"] == "寶貝基金"

    assert delete(client, f"{base}/{t['id']}").status_code == 200
    assert delete(client, f"{base}/{t['id']}").status_code == 404
    assert client.get(f"{base}?month=2026-11").json()["summary"]["expense"] == 0


def test_shared_between_users(client):
    login(client, "mei")
    led = new_ledger(client)
    add(client, led)
    client.post("/api/logout", headers=ORIGIN)
    login(client, "nick")
    items = client.get(f"/api/ledgers/{led['id']}/transactions?month=2026-11").json()["items"]
    assert [i["created_by"] for i in items] == ["小美"]


@pytest.mark.parametrize("kw", [{"amount": 0}, {"amount": -5}, {"amount": 1.5}, {"date": "2026-13-01"}, {"date": "20261101"}])
def test_invalid_transactions_rejected(client, kw):
    login(client)
    assert add(client, new_ledger(client), **kw).status_code == 422


def test_party_required_and_kind_must_match(client):
    login(client)
    led = new_ledger(client)
    body = {"kind": "expense", "amount": 1, "date": "2026-11-01", "category_id": led["categories"][0]["id"]}
    assert post(client, f"/api/ledgers/{led['id']}/transactions", body).status_code == 422
    assert add(client, led, kind="income").status_code == 422  # 餐飲是支出分類


def test_bad_month(client):
    login(client)
    led = new_ledger(client)
    assert client.get(f"/api/ledgers/{led['id']}/transactions?month=2026-1").status_code == 422


# ---------- 分類 ----------
def test_categories(client):
    login(client)
    led = new_ledger(client)
    url = f"/api/ledgers/{led['id']}/categories"
    r = post(client, url, {"name": "寵物", "kind": "expense", "icon": "🐶"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert post(client, url, {"name": "寵物", "kind": "expense"}).status_code == 409
    assert patch(client, f"{url}/{cid}", {"name": "毛小孩"}).json()["name"] == "毛小孩"
    add(client, led, category_id=cid)
    assert delete(client, f"{url}/{cid}").status_code == 409  # 有記錄只能封存
    assert patch(client, f"{url}/{cid}", {"archived": True}).json()["archived"] is True
    items = client.get(f"/api/ledgers/{led['id']}/transactions?month=2026-11").json()["items"]
    assert items[0]["category_name"] == "毛小孩"  # 封存後舊記錄照常顯示
    # 另一本帳本可以有同名分類
    other = new_ledger(client, "另一本")
    assert post(client, f"/api/ledgers/{other['id']}/categories", {"name": "寵物", "kind": "expense"}).status_code == 201


# ---------- 儀表板 ----------
def test_dashboard(client):
    login(client)
    led = new_ledger(client)
    month = dt.date.today().strftime("%Y-%m")
    add(client, led, amount=300, date=f"{month}-01")
    add(client, led, "交通", "圓", amount=100, date=f"{month}-01")
    add(client, led, "薪資", "成", "income", amount=5000, date=f"{month}-01")
    add(client, led, amount=50, date="2020-01-15")
    url = f"/api/ledgers/{led['id']}/dashboard"

    d = client.get(url).json()
    assert d["totals"] | {} == {"income": 5000, "expense": 450, "balance": 4550, "count": 4,
                                "first_date": "2020-01-15", "last_date": f"{month}-01"}
    parties = {p["name"]: p for p in d["by_party"]}
    assert (parties["成"]["income"], parties["成"]["expense"]) == (5000, 350)
    assert parties["圓"]["expense"] == 100 and parties["寶貝基金"]["expense"] == 0
    assert [c["name"] for c in d["by_category"] if c["kind"] == "expense"] == ["餐飲", "交通"]
    assert "monthly" not in d

    d = client.get(f"{url}?start={month}-01").json()
    assert d["totals"]["expense"] == 400
    assert client.get(f"{url}?start=bad").status_code == 422


# ---------- 遷移 ----------
def test_migrate_v0_keeps_records(tmp_path):
    from app import db
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
                            pass_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, icon TEXT NOT NULL DEFAULT '',
                                 sort_order INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, UNIQUE (kind, name));
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, amount INTEGER NOT NULL, date TEXT NOT NULL,
                                   category_id INTEGER NOT NULL REFERENCES categories(id), note TEXT NOT NULL DEFAULT '',
                                   created_by INTEGER NOT NULL REFERENCES users(id),
                                   created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE INDEX idx_tx_date ON transactions(date);
        INSERT INTO users (username, display_name, pass_hash) VALUES ('z', 'Z', 'h');
        INSERT INTO categories (id, name, kind) VALUES (5, '餐飲', 'expense');
        INSERT INTO transactions (kind, amount, date, category_id, note, created_by) VALUES ('expense', 88, '2026-10-01', 5, '早餐', 1);
    """)
    old.close()
    conn = db.connect(path)
    db.init(conn)
    led = conn.execute("SELECT * FROM ledgers").fetchone()
    assert led["name"] == "我的帳本"
    t = conn.execute("SELECT * FROM transactions").fetchone()
    assert (t["ledger_id"], t["amount"], t["category_id"], t["party_id"], t["note"]) == (led["id"], 88, 5, None, "早餐")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.VERSION
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    db.init(conn)  # 再跑一次不會出事
    assert conn.execute("SELECT COUNT(*) FROM ledgers").fetchone()[0] == 1


def test_migrate_v0_without_records(tmp_path):
    from app import db
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, pass_hash TEXT, created_at TEXT);
        CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT, kind TEXT, icon TEXT, sort_order INTEGER, archived INTEGER);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, kind TEXT, amount INTEGER, date TEXT, category_id INTEGER,
                                   note TEXT, created_by INTEGER, created_at TEXT, updated_at TEXT);
        INSERT INTO categories (name, kind) VALUES ('餐飲', 'expense');
    """)
    old.close()
    conn = db.connect(path)
    db.init(conn)
    assert conn.execute("SELECT COUNT(*) FROM ledgers").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0
    assert "ledger_id" in db.columns(conn, "categories")


def test_password_hash_roundtrip():
    from app import auth
    h = auth.make_hash("hello world!", 1000)
    assert auth.check_password("hello world!", h)
    assert not auth.check_password("hello world", h)
    assert not auth.check_password("x", "garbage")
