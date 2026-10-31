import importlib

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


def cat_id(c, name):
    return next(x["id"] for x in c.get("/api/categories").json() if x["name"] == name)


def add(c, **kw):
    body = {"kind": "expense", "amount": 120, "date": "2026-11-01", "category_id": cat_id(c, "餐飲"), "note": "午餐"} | kw
    return c.post("/api/transactions", json=body, headers=ORIGIN)


def test_health_and_index_no_login(client):
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/").status_code == 200


def test_requires_login(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/transactions").status_code == 401
    assert client.post("/api/transactions", json={}, headers=ORIGIN).status_code in (401, 422)


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


def test_transaction_crud_and_summary(client):
    login(client)
    r = add(client)
    assert r.status_code == 201
    t = r.json()
    assert t["created_by"] == "Nick" and t["category_name"] == "餐飲"
    add(client, kind="income", amount=50000, category_id=cat_id(client, "薪資"), note="")
    add(client, date="2026-10-31")  # 別的月份不算

    data = client.get("/api/transactions?month=2026-11").json()
    assert len(data["items"]) == 2
    assert data["summary"] == {"income": 50000, "expense": 120, "balance": 49880}

    r = client.patch(f"/api/transactions/{t['id']}", json={**{k: t[k] for k in ("kind", "date", "category_id")}, "amount": 150, "note": "晚餐"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["amount"] == 150 and r.json()["note"] == "晚餐"

    assert client.delete(f"/api/transactions/{t['id']}", headers=ORIGIN).status_code == 200
    assert client.delete(f"/api/transactions/{t['id']}", headers=ORIGIN).status_code == 404
    assert client.get("/api/transactions?month=2026-11").json()["summary"]["expense"] == 0


def test_shared_book_between_users(client):
    login(client, "mei")
    add(client)
    client.post("/api/logout", headers=ORIGIN)
    login(client, "nick")
    items = client.get("/api/transactions?month=2026-11").json()["items"]
    assert [i["created_by"] for i in items] == ["小美"]


@pytest.mark.parametrize("kw", [{"amount": 0}, {"amount": -5}, {"amount": 1.5}, {"date": "2026-13-01"}, {"date": "20261101"}])
def test_invalid_transactions_rejected(client, kw):
    login(client)
    assert add(client, **kw).status_code == 422


def test_category_kind_must_match(client):
    login(client)
    assert add(client, kind="income").status_code == 422  # 餐飲是支出分類


def test_bad_month(client):
    login(client)
    assert client.get("/api/transactions?month=2026-1").status_code == 422


def test_cross_origin_write_blocked(client):
    login(client)
    assert add(client).status_code == 201
    body = {"kind": "expense", "amount": 1, "date": "2026-11-01", "category_id": cat_id(client, "餐飲")}
    assert client.post("/api/transactions", json=body, headers={"Origin": "https://evil.example"}).status_code == 403


def test_categories(client):
    login(client)
    r = client.post("/api/categories", json={"name": "寵物", "kind": "expense", "icon": "🐶"}, headers=ORIGIN)
    assert r.status_code == 201
    cid = r.json()["id"]
    assert client.post("/api/categories", json={"name": "寵物", "kind": "expense"}, headers=ORIGIN).status_code == 409
    assert client.patch(f"/api/categories/{cid}", json={"name": "毛小孩"}, headers=ORIGIN).json()["name"] == "毛小孩"

    add(client, category_id=cid)
    assert client.delete(f"/api/categories/{cid}", headers=ORIGIN).status_code == 409  # 有記錄只能封存
    assert client.patch(f"/api/categories/{cid}", json={"archived": True}, headers=ORIGIN).json()["archived"] is True
    # 封存後舊記錄照常顯示
    assert client.get("/api/transactions?month=2026-11").json()["items"][0]["category_name"] == "毛小孩"

    empty = client.post("/api/categories", json={"name": "暫時", "kind": "income"}, headers=ORIGIN).json()["id"]
    assert client.delete(f"/api/categories/{empty}", headers=ORIGIN).status_code == 200


def test_password_hash_roundtrip():
    from app import auth
    h = auth.make_hash("hello world!", 1000)
    assert auth.check_password("hello world!", h)
    assert not auth.check_password("hello world", h)
    assert not auth.check_password("x", "garbage")
