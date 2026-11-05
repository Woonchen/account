"""記帳網頁：FastAPI 後端＋靜態單頁。

結構：帳本 → 成員（誰付款／收款）、分類 → 收支記錄。所有登入的帳號共用全部帳本。
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
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"  # 每次用 ETag 確認，更新後手機不會卡在舊的 app.js
    return response


def current_user(request: Request, conn=Depends(get_conn)):
    value = request.cookies.get(auth.COOKIE, "")
    name = auth.session_user(value)
    row = name and conn.execute("SELECT id, username, display_name, pass_hash FROM users WHERE username = ?", (name,)).fetchone()
    if not row or not auth.valid_session(value, row["pass_hash"]):
        raise HTTPException(401, "請先登入")
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"]}


def unique_or_409(conn, sql, params, msg):
    try:
        return conn.execute(sql, params)
    except sqlite3.IntegrityError:
        raise HTTPException(409, msg)


def patch_row(conn, table, rid, fields, msg):
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        unique_or_409(conn, f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), rid), msg)
        conn.commit()


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


# ---------- 帳本 ----------
Name = Field(min_length=1, max_length=20)


class LedgerIn(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    parties: list[str] = Field(default_factory=lambda: list(db.DEFAULT_PARTIES), min_length=1, max_length=20)
    copy_categories_from: int | None = None  # 每月開新帳本時沿用上一本的分類

    @field_validator("parties")
    @classmethod
    def clean_parties(cls, v):
        names = [n.strip() for n in v if n.strip()]
        if not names:
            raise ValueError("至少要有一位成員")
        if len(set(names)) != len(names):
            raise ValueError("成員名稱重複")
        if any(len(n) > 20 for n in names):
            raise ValueError("成員名稱太長")
        return names


class LedgerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=30)
    archived: bool | None = None


LEDGER_SELECT = "SELECT l.*, s.settled_at FROM ledgers l LEFT JOIN settlements s ON s.ledger_id = l.id"


def ledger_dict(r):
    return {"id": r["id"], "name": r["name"], "archived": bool(r["archived"]), "created_at": r["created_at"],
            "settled": r["settled_at"] is not None}


def ledger_row(conn, lid):
    return conn.execute(LEDGER_SELECT + " WHERE l.id = ?", (lid,)).fetchone()


def check_unsettled(conn, lid):
    """結算後記錄就鎖住，結算結果才會跟記錄對得上；要改請先取消結算。"""
    if conn.execute("SELECT 1 FROM settlements WHERE ledger_id = ?", (lid,)).fetchone():
        raise HTTPException(409, "這本帳本已經結算，要先取消結算才能修改記錄")


def get_ledger(lid: int, user=Depends(current_user), conn=Depends(get_conn)):
    """路由裡的 {lid} 必須是存在的帳本；回傳帳本 id。"""
    if not conn.execute("SELECT 1 FROM ledgers WHERE id = ?", (lid,)).fetchone():
        raise HTTPException(404, "找不到帳本")
    return lid


@app.get("/api/ledgers")
def list_ledgers(user=Depends(current_user), conn=Depends(get_conn)):
    rows = conn.execute(LEDGER_SELECT + " ORDER BY l.archived, l.id").fetchall()
    return [ledger_dict(r) for r in rows]


@app.post("/api/ledgers", status_code=201)
def create_ledger(body: LedgerIn, user=Depends(current_user), conn=Depends(get_conn)):
    if body.copy_categories_from is not None and not conn.execute(
            "SELECT 1 FROM ledgers WHERE id = ?", (body.copy_categories_from,)).fetchone():
        raise HTTPException(404, "找不到要沿用的帳本")
    lid = db.create_ledger(conn, body.name.strip(), body.parties, user["id"], body.copy_categories_from)
    conn.commit()
    log(f"新增帳本 #{lid} {body.name.strip()}（{user['username']}）")
    return ledger_detail(lid, user, conn)


@app.get("/api/ledgers/{lid}")
def ledger_detail(lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    """帳本＋成員＋分類，前端切換帳本時一次拿齊。"""
    led = ledger_row(conn, lid)
    parties = conn.execute("SELECT * FROM parties WHERE ledger_id = ? ORDER BY sort_order, id", (lid,)).fetchall()
    cats = conn.execute("SELECT * FROM categories WHERE ledger_id = ? ORDER BY kind DESC, sort_order, id", (lid,)).fetchall()
    return ledger_dict(led) | {"parties": [party_dict(p) for p in parties], "categories": [category_dict(c) for c in cats]}


@app.patch("/api/ledgers/{lid}")
def update_ledger(body: LedgerPatch, lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    fields = body.model_dump(exclude_none=True)
    if "name" in fields:
        fields["name"] = fields["name"].strip()
    patch_row(conn, "ledgers", lid, fields, "")
    return ledger_dict(ledger_row(conn, lid))


@app.delete("/api/ledgers/{lid}")
def delete_ledger(lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    if conn.execute("SELECT 1 FROM transactions WHERE ledger_id = ? LIMIT 1", (lid,)).fetchone():
        raise HTTPException(409, "這本帳本有記錄，只能封存")
    conn.execute("DELETE FROM ledgers WHERE id = ?", (lid,))
    conn.commit()
    log(f"刪除帳本 #{lid}（{user['username']}）")
    return {"ok": True}


# ---------- 成員（誰付款／收款） ----------
class PartyIn(BaseModel):
    name: str = Name


class PartyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=20)
    sort_order: int | None = None
    archived: bool | None = None


def party_dict(r):
    return {"id": r["id"], "name": r["name"], "sort_order": r["sort_order"], "archived": bool(r["archived"])}


def get_party(conn, lid, pid):
    row = conn.execute("SELECT * FROM parties WHERE id = ? AND ledger_id = ?", (pid, lid)).fetchone()
    if not row:
        raise HTTPException(404, "找不到成員")
    return row


@app.post("/api/ledgers/{lid}/parties", status_code=201)
def create_party(body: PartyIn, lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM parties WHERE ledger_id = ?", (lid,)).fetchone()[0]
    cur = unique_or_409(conn, "INSERT INTO parties (ledger_id, name, sort_order) VALUES (?, ?, ?)",
                        (lid, body.name.strip(), order), "已經有同名的成員")
    conn.commit()
    return party_dict(get_party(conn, lid, cur.lastrowid))


@app.patch("/api/ledgers/{lid}/parties/{pid}")
def update_party(pid: int, body: PartyPatch, lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    get_party(conn, lid, pid)
    fields = body.model_dump(exclude_none=True)
    if "name" in fields:
        fields["name"] = fields["name"].strip()
    patch_row(conn, "parties", pid, fields, "已經有同名的成員")
    return party_dict(get_party(conn, lid, pid))


@app.delete("/api/ledgers/{lid}/parties/{pid}")
def delete_party(pid: int, lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    get_party(conn, lid, pid)
    if conn.execute("SELECT 1 FROM transactions WHERE party_id = ? LIMIT 1", (pid,)).fetchone():
        raise HTTPException(409, "這位成員有記錄，只能封存")
    conn.execute("DELETE FROM parties WHERE id = ?", (pid,))
    conn.commit()
    return {"ok": True}


# ---------- 分類 ----------
Kind = Literal["income", "expense"]


class CategoryIn(BaseModel):
    name: str = Name
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


def get_category(conn, lid, cid):
    row = conn.execute("SELECT * FROM categories WHERE id = ? AND ledger_id = ?", (cid, lid)).fetchone()
    if not row:
        raise HTTPException(404, "找不到分類")
    return row


@app.post("/api/ledgers/{lid}/categories", status_code=201)
def create_category(body: CategoryIn, lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories WHERE ledger_id = ?", (lid,)).fetchone()[0]
    cur = unique_or_409(conn, "INSERT INTO categories (ledger_id, name, kind, icon, sort_order) VALUES (?, ?, ?, ?, ?)",
                        (lid, body.name.strip(), body.kind, body.icon, order), "已經有同名的分類")
    conn.commit()
    return category_dict(get_category(conn, lid, cur.lastrowid))


@app.patch("/api/ledgers/{lid}/categories/{cid}")
def update_category(cid: int, body: CategoryPatch, lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    get_category(conn, lid, cid)
    fields = body.model_dump(exclude_none=True)
    if "name" in fields:
        fields["name"] = fields["name"].strip()
    patch_row(conn, "categories", cid, fields, "已經有同名的分類")
    return category_dict(get_category(conn, lid, cid))


@app.delete("/api/ledgers/{lid}/categories/{cid}")
def delete_category(cid: int, lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    get_category(conn, lid, cid)
    if conn.execute("SELECT 1 FROM transactions WHERE category_id = ? LIMIT 1", (cid,)).fetchone():
        raise HTTPException(409, "這個分類有記錄，只能封存")
    conn.execute("DELETE FROM categories WHERE id = ?", (cid,))
    conn.commit()
    return {"ok": True}


# ---------- 收支記錄 ----------
def valid_date(v):
    if len(v) != 10:
        raise ValueError("日期格式要是 YYYY-MM-DD")
    dt.date.fromisoformat(v)
    return v


class TransactionIn(BaseModel):
    kind: Kind
    amount: int = Field(gt=0, le=MAX_AMOUNT)
    date: str
    party_id: int
    category_id: int
    note: str = Field(default="", max_length=200)

    @field_validator("date")
    @classmethod
    def check_date(cls, v):
        return valid_date(v)


TX_SELECT = """
SELECT t.id, t.ledger_id, t.kind, t.amount, t.date, t.note, t.category_id, t.party_id, t.created_at, t.updated_at,
       c.name AS category_name, c.icon AS category_icon, p.name AS party_name, u.display_name AS created_by
FROM transactions t
JOIN categories c ON c.id = t.category_id
JOIN users u ON u.id = t.created_by
LEFT JOIN parties p ON p.id = t.party_id
"""


def check_refs(conn, lid, body):
    cat = get_category(conn, lid, body.category_id)
    if cat["kind"] != body.kind:
        raise HTTPException(422, "分類跟收入／支出不符")
    get_party(conn, lid, body.party_id)


def get_tx(conn, lid, tid):
    row = conn.execute(TX_SELECT + " WHERE t.id = ? AND t.ledger_id = ?", (tid, lid)).fetchone()
    if not row:
        raise HTTPException(404, "找不到這筆記錄")
    return dict(row)


@app.get("/api/ledgers/{lid}/transactions")
def list_transactions(month: str | None = None, party_id: int | None = None,
                      lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    """不給 month 就是整本帳本的記錄（一個月一本帳本時的用法）。"""
    sql, params = TX_SELECT + " WHERE t.ledger_id = ?", [lid]
    if month is not None:
        if not MONTH.fullmatch(month):
            raise HTTPException(422, "月份格式要是 YYYY-MM")
        sql += " AND substr(t.date, 1, 7) = ?"
        params.append(month)
    if party_id is not None:
        sql += " AND t.party_id = ?"
        params.append(party_id)
    items = [dict(r) for r in conn.execute(sql + " ORDER BY t.date DESC, t.id DESC", params)]
    income = sum(r["amount"] for r in items if r["kind"] == "income")
    expense = sum(r["amount"] for r in items if r["kind"] == "expense")
    return {"month": month, "items": items, "summary": {"income": income, "expense": expense, "balance": income - expense}}


@app.post("/api/ledgers/{lid}/transactions", status_code=201)
def create_transaction(body: TransactionIn, lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    check_unsettled(conn, lid)
    check_refs(conn, lid, body)
    cur = conn.execute(
        """INSERT INTO transactions (ledger_id, kind, amount, date, party_id, category_id, note, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (lid, body.kind, body.amount, body.date, body.party_id, body.category_id, body.note.strip(), user["id"]),
    )
    conn.commit()
    return get_tx(conn, lid, cur.lastrowid)


@app.patch("/api/ledgers/{lid}/transactions/{tid}")
def update_transaction(tid: int, body: TransactionIn, lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    get_tx(conn, lid, tid)
    check_unsettled(conn, lid)
    check_refs(conn, lid, body)
    conn.execute(
        """UPDATE transactions SET kind = ?, amount = ?, date = ?, party_id = ?, category_id = ?, note = ?,
           updated_at = datetime('now') WHERE id = ?""",
        (body.kind, body.amount, body.date, body.party_id, body.category_id, body.note.strip(), tid),
    )
    conn.commit()
    return get_tx(conn, lid, tid)


@app.delete("/api/ledgers/{lid}/transactions/{tid}")
def delete_transaction(tid: int, lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    get_tx(conn, lid, tid)
    check_unsettled(conn, lid)
    conn.execute("DELETE FROM transactions WHERE id = ?", (tid,))
    conn.commit()
    log(f"刪除記錄 #{tid}（帳本 #{lid}，{user['username']}）")
    return {"ok": True}


# ---------- 儀表板 ----------
@app.get("/api/ledgers/{lid}/dashboard")
def dashboard(start: str | None = None, end: str | None = None,
              lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    """整本帳本的總計、各成員、各分類；start／end（含）可以只看其中一段。"""
    try:
        start = start and valid_date(start)
        end = end and valid_date(end)
    except ValueError:
        raise HTTPException(422, "日期格式要是 YYYY-MM-DD")
    where, params = "t.ledger_id = ?", [lid]
    if start:
        where += " AND t.date >= ?"
        params.append(start)
    if end:
        where += " AND t.date <= ?"
        params.append(end)

    tot = conn.execute(f"""SELECT COALESCE(SUM(CASE WHEN kind = 'income' THEN amount END), 0) AS income,
                                  COALESCE(SUM(CASE WHEN kind = 'expense' THEN amount END), 0) AS expense,
                                  COUNT(*) AS count, MIN(date) AS first, MAX(date) AS last
                           FROM transactions t WHERE {where}""", params).fetchone()

    by_party = conn.execute(f"""
        SELECT p.id, p.name, p.archived,
               COALESCE(SUM(CASE WHEN t.kind = 'income' THEN t.amount END), 0) AS income,
               COALESCE(SUM(CASE WHEN t.kind = 'expense' THEN t.amount END), 0) AS expense
        FROM parties p LEFT JOIN transactions t ON t.party_id = p.id AND {where}
        WHERE p.ledger_id = ? GROUP BY p.id ORDER BY p.sort_order, p.id""", (*params, lid)).fetchall()
    # 舊版搬過來、沒有成員的記錄
    no_party = conn.execute(f"""
        SELECT COALESCE(SUM(CASE WHEN kind = 'income' THEN amount END), 0) AS income,
               COALESCE(SUM(CASE WHEN kind = 'expense' THEN amount END), 0) AS expense
        FROM transactions t WHERE {where} AND t.party_id IS NULL""", params).fetchone()
    parties = [{"id": r["id"], "name": r["name"], "income": r["income"], "expense": r["expense"]}
               for r in by_party if not r["archived"] or r["income"] or r["expense"]]
    if no_party["income"] or no_party["expense"]:
        parties.append({"id": None, "name": "未指定", "income": no_party["income"], "expense": no_party["expense"]})

    by_cat = conn.execute(f"""
        SELECT c.id, c.name, c.icon, t.kind, SUM(t.amount) AS amount, COUNT(*) AS count
        FROM transactions t JOIN categories c ON c.id = t.category_id
        WHERE {where} GROUP BY c.id ORDER BY amount DESC""", params).fetchall()

    return {
        "totals": {"income": tot["income"], "expense": tot["expense"], "balance": tot["income"] - tot["expense"],
                   "count": tot["count"], "first_date": tot["first"], "last_date": tot["last"]},
        "by_party": parties,
        "by_category": [dict(r) for r in by_cat],
    }


# ---------- 全域預算 ----------
class BudgetItem(BaseModel):
    name: str
    amount: int = Field(ge=0, le=MAX_AMOUNT)


class BudgetsIn(BaseModel):
    items: list[BudgetItem]


def current_budgets(conn):
    rows = {r["name"]: r for r in conn.execute("SELECT * FROM budgets")}
    return [{"name": n, "amount": rows[n]["amount"], "updated_at": rows[n]["updated_at"]} for n in db.BUDGET_NAMES]


@app.get("/api/budgets")
def get_budgets(user=Depends(current_user), conn=Depends(get_conn)):
    return current_budgets(conn)


@app.put("/api/budgets")
def put_budgets(body: BudgetsIn, user=Depends(current_user), conn=Depends(get_conn)):
    for item in body.items:
        if item.name not in db.BUDGET_NAMES:
            raise HTTPException(422, f"只能設定{'、'.join(db.BUDGET_NAMES)}的預算")
    conn.executemany("UPDATE budgets SET amount = ?, updated_at = datetime('now') WHERE name = ?",
                     [(i.amount, i.name) for i in body.items])
    conn.commit()
    log(f"更新預算 {', '.join(f'{i.name}={i.amount}' for i in body.items)}（{user['username']}）")
    return current_budgets(conn)


# ---------- 結算 ----------
def spent_by_name(conn, lid):
    """這本帳本裡，名字是成、圓、寶貝基金的付款人各自付的支出合計（收款不算）。"""
    marks = ",".join("?" * len(db.BUDGET_NAMES))
    rows = conn.execute(f"""SELECT p.name, SUM(t.amount) AS spent FROM transactions t JOIN parties p ON p.id = t.party_id
                            WHERE t.ledger_id = ? AND t.kind = 'expense' AND p.name IN ({marks})
                            GROUP BY p.name""", (lid, *db.BUDGET_NAMES)).fetchall()
    return {r["name"]: r["spent"] for r in rows}


def settlement_result(lines, settled=None):
    """差額＝花費 − 預算：正數是超支，負數是還剩多少。"""
    out = [{"name": l["name"], "budget": l["budget"], "spent": l["spent"], "diff": l["spent"] - l["budget"]} for l in lines]
    total = {k: sum(l[k] for l in out) for k in ("budget", "spent", "diff")}
    return {"settled": settled is not None,
            "settled_at": settled["settled_at"] if settled else None,
            "settled_by": settled["display_name"] if settled else None,
            "lines": out, "total": total}


@app.get("/api/ledgers/{lid}/settlement")
def get_settlement(lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    """已結算就回傳當時存下的結果；還沒結算就用現在的全域預算試算。"""
    settled = conn.execute("""SELECT s.settled_at, u.display_name FROM settlements s LEFT JOIN users u ON u.id = s.settled_by
                              WHERE s.ledger_id = ?""", (lid,)).fetchone()
    if settled:
        lines = conn.execute("SELECT name, budget, spent FROM settlement_lines WHERE ledger_id = ?", (lid,)).fetchall()
        order = {n: i for i, n in enumerate(db.BUDGET_NAMES)}
        return settlement_result(sorted(lines, key=lambda l: order.get(l["name"], 99)), settled)
    spent = spent_by_name(conn, lid)
    return settlement_result([{"name": b["name"], "budget": b["amount"], "spent": spent.get(b["name"], 0)}
                              for b in current_budgets(conn)])


@app.post("/api/ledgers/{lid}/settlement", status_code=201)
def settle(lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    check_unsettled(conn, lid)
    preview = get_settlement(lid, user, conn)
    conn.execute("INSERT INTO settlements (ledger_id, settled_by) VALUES (?, ?)", (lid, user["id"]))
    conn.executemany("INSERT INTO settlement_lines (ledger_id, name, budget, spent) VALUES (?, ?, ?, ?)",
                     [(lid, l["name"], l["budget"], l["spent"]) for l in preview["lines"]])
    conn.commit()
    log(f"完成結算 帳本 #{lid}（{user['username']}）")
    return get_settlement(lid, user, conn)


@app.delete("/api/ledgers/{lid}/settlement")
def unsettle(lid: int = Depends(get_ledger), user=Depends(current_user), conn=Depends(get_conn)):
    if conn.execute("DELETE FROM settlements WHERE ledger_id = ?", (lid,)).rowcount == 0:
        raise HTTPException(404, "這本帳本還沒有結算")
    conn.commit()
    log(f"取消結算 帳本 #{lid}（{user['username']}）")
    return get_settlement(lid, user, conn)


# ---------- 頁面 ----------
@app.get("/health")
def health(conn=Depends(get_conn)):
    conn.execute("SELECT 1").fetchone()
    return {"ok": True}


@app.api_route("/", methods=["GET", "HEAD"])
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
