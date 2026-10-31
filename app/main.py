"""記帳網頁：FastAPI 後端＋靜態單頁。

所有 /api/* 都要登入（/api/login 除外）；/health 與靜態檔不用登入（頁面自己用 /api/me 判斷要不要顯示登入畫面）。
會寫入的請求帶了 Origin 就要跟 Host 一致，擋掉跨站送出（cookie 另外有 SameSite=Lax）。
"""
import datetime as dt
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import auth, db

STATIC = Path(__file__).parent / "static"
GUARD = auth.Guard()
MONTH = re.compile(r"\d{4}-\d{2}")
MAX_AMOUNT = 1_000_000_000_000
# 本機開發用 http，設 ACCOUNT_INSECURE_COOKIE=1 才不會加 Secure
SECURE_COOKIE = os.environ.get("ACCOUNT_INSECURE_COOKIE") != "1"


@asynccontextmanager
async def lifespan(_app):
    auth.secret()  # 沒設定就不要啟動
    conn = db.connect()
    db.init(conn)
    conn.close()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def log(msg):
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def client_ip(request: Request):
    # Caddy 會把 X-Forwarded-For 設成真正的連線位址（不採用使用者自己帶的值）；取最後一個最保險
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[-1].strip() or (request.client.host if request.client else "")


@app.middleware("http")
async def check_origin(request: Request, call_next):
    if request.method in ("POST", "PATCH", "PUT", "DELETE"):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "來源不符"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def current_user(request: Request, conn=Depends(get_conn)):
    value = request.cookies.get(auth.COOKIE, "")
    name = auth.session_user(value)
    row = name and conn.execute("SELECT id, username, display_name, pass_hash FROM users WHERE username = ?", (name,)).fetchone()
    if not row or not auth.valid_session(value, row["pass_hash"]):
        raise HTTPException(401, "請先登入")
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"]}


# ---------- 登入 ----------
class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response, conn=Depends(get_conn)):
    ip = client_ip(request)
    until = GUARD.is_blocked(ip)
    if until:
        raise HTTPException(429, f"輸錯太多次，{time.strftime('%m/%d %H:%M', time.localtime(until))} 後再試")
    row = conn.execute("SELECT username, display_name, pass_hash FROM users WHERE username = ?", (body.username,)).fetchone()
    if not auth.check_password(body.password, row["pass_hash"] if row else auth.DUMMY_HASH) or not row:
        time.sleep(1)
        blocked = GUARD.fail(ip)
        log(f"登入失敗 {body.username!r} {ip}" + ("（封鎖）" if blocked else ""))
        raise HTTPException(429 if blocked else 401, "輸錯太多次，24 小時後再試" if blocked else "帳號或密碼錯誤")
    GUARD.ok(ip)
    log(f"登入成功 {row['username']} {ip}")
    response.set_cookie(
        auth.COOKIE, auth.make_session(row["username"], row["pass_hash"]),
        max_age=auth.SESSION_DAYS * 86400, httponly=True, secure=SECURE_COOKIE, samesite="lax",
    )
    return {"username": row["username"], "display_name": row["display_name"]}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, httponly=True, secure=SECURE_COOKIE, samesite="lax")
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return user


# ---------- 分類 ----------
Kind = Literal["income", "expense"]


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    kind: Kind
    icon: str = Field(default="", max_length=8)


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=20)
    icon: str | None = Field(default=None, max_length=8)
    sort_order: int | None = None
    archived: bool | None = None


def category_dict(r):
    return {"id": r["id"], "name": r["name"], "kind": r["kind"], "icon": r["icon"],
            "sort_order": r["sort_order"], "archived": bool(r["archived"])}


@app.get("/api/categories")
def list_categories(user=Depends(current_user), conn=Depends(get_conn)):
    rows = conn.execute("SELECT * FROM categories ORDER BY kind DESC, sort_order, id").fetchall()
    return [category_dict(r) for r in rows]


@app.post("/api/categories", status_code=201)
def create_category(body: CategoryIn, user=Depends(current_user), conn=Depends(get_conn)):
    order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories").fetchone()[0]
    try:
        cur = conn.execute("INSERT INTO categories (name, kind, icon, sort_order) VALUES (?, ?, ?, ?)",
                           (body.name.strip(), body.kind, body.icon, order))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "已經有同名的分類")
    conn.commit()
    return category_dict(conn.execute("SELECT * FROM categories WHERE id = ?", (cur.lastrowid,)).fetchone())


@app.patch("/api/categories/{cid}")
def update_category(cid: int, body: CategoryPatch, user=Depends(current_user), conn=Depends(get_conn)):
    fields = body.model_dump(exclude_none=True)
    if "name" in fields:
        fields["name"] = fields["name"].strip()
    if not conn.execute("SELECT 1 FROM categories WHERE id = ?", (cid,)).fetchone():
        raise HTTPException(404, "找不到分類")
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        try:
            conn.execute(f"UPDATE categories SET {sets} WHERE id = ?", (*fields.values(), cid))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "已經有同名的分類")
        conn.commit()
    return category_dict(conn.execute("SELECT * FROM categories WHERE id = ?", (cid,)).fetchone())


@app.delete("/api/categories/{cid}")
def delete_category(cid: int, user=Depends(current_user), conn=Depends(get_conn)):
    if conn.execute("SELECT 1 FROM transactions WHERE category_id = ? LIMIT 1", (cid,)).fetchone():
        raise HTTPException(409, "這個分類有記錄，只能封存")
    if conn.execute("DELETE FROM categories WHERE id = ?", (cid,)).rowcount == 0:
        raise HTTPException(404, "找不到分類")
    conn.commit()
    return {"ok": True}


# ---------- 收支記錄 ----------
class TransactionIn(BaseModel):
    kind: Kind
    amount: int = Field(gt=0, le=MAX_AMOUNT)
    date: str
    category_id: int
    note: str = Field(default="", max_length=200)

    @field_validator("date")
    @classmethod
    def valid_date(cls, v):
        dt.date.fromisoformat(v)
        if len(v) != 10:
            raise ValueError("日期格式要是 YYYY-MM-DD")
        return v


TX_SELECT = """
SELECT t.id, t.kind, t.amount, t.date, t.note, t.category_id, t.created_at, t.updated_at,
       c.name AS category_name, c.icon AS category_icon, u.display_name AS created_by
FROM transactions t JOIN categories c ON c.id = t.category_id JOIN users u ON u.id = t.created_by
"""


def check_category(conn, body):
    cat = conn.execute("SELECT kind FROM categories WHERE id = ?", (body.category_id,)).fetchone()
    if not cat:
        raise HTTPException(422, "找不到分類")
    if cat["kind"] != body.kind:
        raise HTTPException(422, "分類跟收入／支出不符")


def get_tx(conn, tid):
    row = conn.execute(TX_SELECT + " WHERE t.id = ?", (tid,)).fetchone()
    if not row:
        raise HTTPException(404, "找不到這筆記錄")
    return dict(row)


@app.get("/api/transactions")
def list_transactions(month: str | None = None, user=Depends(current_user), conn=Depends(get_conn)):
    month = month or dt.date.today().strftime("%Y-%m")
    if not MONTH.fullmatch(month):
        raise HTTPException(422, "月份格式要是 YYYY-MM")
    rows = conn.execute(TX_SELECT + " WHERE substr(t.date, 1, 7) = ? ORDER BY t.date DESC, t.id DESC", (month,)).fetchall()
    items = [dict(r) for r in rows]
    income = sum(r["amount"] for r in items if r["kind"] == "income")
    expense = sum(r["amount"] for r in items if r["kind"] == "expense")
    return {"month": month, "items": items, "summary": {"income": income, "expense": expense, "balance": income - expense}}


@app.post("/api/transactions", status_code=201)
def create_transaction(body: TransactionIn, user=Depends(current_user), conn=Depends(get_conn)):
    check_category(conn, body)
    cur = conn.execute(
        "INSERT INTO transactions (kind, amount, date, category_id, note, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (body.kind, body.amount, body.date, body.category_id, body.note.strip(), user["id"]),
    )
    conn.commit()
    return get_tx(conn, cur.lastrowid)


@app.patch("/api/transactions/{tid}")
def update_transaction(tid: int, body: TransactionIn, user=Depends(current_user), conn=Depends(get_conn)):
    get_tx(conn, tid)
    check_category(conn, body)
    conn.execute(
        "UPDATE transactions SET kind = ?, amount = ?, date = ?, category_id = ?, note = ?, updated_at = datetime('now') WHERE id = ?",
        (body.kind, body.amount, body.date, body.category_id, body.note.strip(), tid),
    )
    conn.commit()
    return get_tx(conn, tid)


@app.delete("/api/transactions/{tid}")
def delete_transaction(tid: int, user=Depends(current_user), conn=Depends(get_conn)):
    if conn.execute("DELETE FROM transactions WHERE id = ?", (tid,)).rowcount == 0:
        raise HTTPException(404, "找不到這筆記錄")
    conn.commit()
    log(f"刪除記錄 #{tid}（{user['username']}）")
    return {"ok": True}


# ---------- 頁面 ----------
@app.get("/health")
def health(conn=Depends(get_conn)):
    conn.execute("SELECT 1").fetchone()
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
