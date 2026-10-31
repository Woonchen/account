// 記帳本前端：純 JS，所有畫面文字用 textContent 放，不用 innerHTML 拼使用者輸入
"use strict";

const $ = (s) => document.querySelector(s);
const fmt = new Intl.NumberFormat("zh-TW");
const WEEK = "日一二三四五六";

const state = { me: null, month: thisMonth(), categories: [], txKind: "expense", txCat: null, editing: null, catKind: "expense" };

function thisMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
function today() {
  const d = new Date();
  return `${thisMonth()}-${String(d.getDate()).padStart(2, "0")}`;
}
function el(tag, props = {}, ...children) {
  const e = Object.assign(document.createElement(tag), props);
  for (const c of children) e.append(c);
  return e;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Error("請先登入"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(Array.isArray(d) ? "輸入的資料不正確" : d || `錯誤 ${res.status}`);
  }
  return data;
}

// ---------- 登入 ----------
function showLogin() {
  $("#main").hidden = true;
  $("#login").hidden = false;
  $("#login-form").username.focus();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  $("#login-error").textContent = "";
  f.querySelector("button").disabled = true;
  try {
    state.me = await api("/api/login", { method: "POST", body: { username: f.username.value.trim(), password: f.password.value } });
    f.password.value = "";
    await enter();
  } catch (err) {
    $("#login-error").textContent = err.message;
  } finally {
    f.querySelector("button").disabled = false;
  }
});

$("#logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  state.me = null;
  $("#menu").hidden = true;
  showLogin();
});

async function enter() {
  $("#login").hidden = true;
  $("#main").hidden = false;
  $("#me-label").textContent = `登入為 ${state.me.display_name}`;
  await loadCategories();
  await loadMonth();
}

// ---------- 月份與清單 ----------
function shiftMonth(delta) {
  const [y, m] = state.month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  state.month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  loadMonth();
}
$("#prev-month").addEventListener("click", () => shiftMonth(-1));
$("#next-month").addEventListener("click", () => shiftMonth(1));

async function loadCategories() {
  state.categories = await api("/api/categories");
}

async function loadMonth() {
  const [y, m] = state.month.split("-");
  $("#month-label").textContent = `${y} 年 ${Number(m)} 月`;
  const data = await api(`/api/transactions?month=${state.month}`);
  const s = data.summary;
  $("#sum-income").textContent = fmt.format(s.income);
  $("#sum-expense").textContent = fmt.format(s.expense);
  $("#sum-balance").textContent = fmt.format(s.balance);
  $("#sum-balance").className = s.balance < 0 ? "expense" : "";
  renderList(data.items);
}

function renderList(items) {
  const list = $("#list");
  list.replaceChildren();
  $("#empty").hidden = items.length > 0;
  const byDay = new Map();
  for (const t of items) {
    if (!byDay.has(t.date)) byDay.set(t.date, []);
    byDay.get(t.date).push(t);
  }
  for (const [date, txs] of byDay) {
    const d = new Date(date + "T00:00:00");
    const net = txs.reduce((a, t) => a + (t.kind === "income" ? t.amount : -t.amount), 0);
    const ul = el("ul");
    for (const t of txs) {
      const note = [t.note, t.created_by].filter(Boolean).join(" · ");
      const li = el("li", { className: "tx" },
        el("span", { className: "ic", textContent: t.category_icon || "•" }),
        el("div", { className: "body" },
          el("div", { className: "name", textContent: t.category_name }),
          el("div", { className: "note", textContent: note })),
        el("span", { className: `amt ${t.kind}`, textContent: (t.kind === "income" ? "+" : "−") + fmt.format(t.amount) }));
      li.addEventListener("click", () => openTx(t));
      ul.append(li);
    }
    list.append(el("section", { className: "day" },
      el("div", { className: "day-head" },
        el("span", { textContent: `${d.getMonth() + 1}/${d.getDate()}（${WEEK[d.getDay()]}）` }),
        el("span", { textContent: (net >= 0 ? "+" : "−") + fmt.format(Math.abs(net)) })),
      ul));
  }
}

// ---------- 新增／編輯 ----------
const txDialog = $("#tx-dialog");
const txForm = $("#tx-form");

function setTxKind(kind) {
  state.txKind = kind;
  for (const b of $("#tx-kind").children) b.classList.toggle("active", b.dataset.kind === kind);
  const cats = state.categories.filter((c) => c.kind === kind && (!c.archived || c.id === state.txCat));
  if (!cats.some((c) => c.id === state.txCat)) state.txCat = cats[0]?.id ?? null;
  const grid = $("#tx-cats");
  grid.replaceChildren();
  for (const c of cats) {
    const b = el("button", { type: "button", textContent: `${c.icon} ${c.name}`.trim() });
    b.classList.toggle("active", c.id === state.txCat);
    b.addEventListener("click", () => {
      state.txCat = c.id;
      for (const x of grid.children) x.classList.toggle("active", x === b);
    });
    grid.append(b);
  }
}
$("#tx-kind").addEventListener("click", (e) => { if (e.target.dataset.kind) setTxKind(e.target.dataset.kind); });

function openTx(t = null) {
  state.editing = t;
  $("#tx-error").textContent = "";
  txForm.amount.value = t ? t.amount : "";
  // 新增時，看的是本月就預設今天，看別的月份就預設那個月 1 號
  txForm.date.value = t ? t.date : (state.month === thisMonth() ? today() : `${state.month}-01`);
  txForm.note.value = t ? t.note : "";
  state.txCat = t ? t.category_id : null;
  setTxKind(t ? t.kind : state.txKind);
  $("#tx-delete").hidden = !t;
  $("#tx-meta").textContent = t ? `${t.created_by} 記的` + (t.updated_at !== t.created_at ? "（改過）" : "") : "";
  txDialog.showModal();
  if (!t) txForm.amount.focus();
}
$("#add-btn").addEventListener("click", () => openTx());
$("#tx-cancel").addEventListener("click", () => txDialog.close());

txForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const amount = Number(txForm.amount.value.replace(/[,\s]/g, ""));
  if (!Number.isInteger(amount) || amount <= 0) { $("#tx-error").textContent = "金額要是大於 0 的整數"; return; }
  if (!state.txCat) { $("#tx-error").textContent = "請選分類"; return; }
  const body = { kind: state.txKind, amount, date: txForm.date.value, category_id: state.txCat, note: txForm.note.value };
  try {
    if (state.editing) await api(`/api/transactions/${state.editing.id}`, { method: "PATCH", body });
    else await api("/api/transactions", { method: "POST", body });
    txDialog.close();
    if (body.date.slice(0, 7) !== state.month) state.month = body.date.slice(0, 7);
    await loadMonth();
  } catch (err) {
    $("#tx-error").textContent = err.message;
  }
});

$("#tx-delete").addEventListener("click", async () => {
  const t = state.editing;
  if (!t || !confirm(`刪除這筆 ${t.category_name} ${fmt.format(t.amount)}？`)) return;
  try {
    await api(`/api/transactions/${t.id}`, { method: "DELETE" });
    txDialog.close();
    await loadMonth();
  } catch (err) {
    $("#tx-error").textContent = err.message;
  }
});

// ---------- 選單 ----------
$("#menu-btn").addEventListener("click", (e) => { e.stopPropagation(); $("#menu").hidden = !$("#menu").hidden; });
document.addEventListener("click", (e) => { if (!$("#menu").contains(e.target)) $("#menu").hidden = true; });

// ---------- 分類管理 ----------
const catDialog = $("#cat-dialog");

function renderCats() {
  for (const b of $("#cat-kind").children) b.classList.toggle("active", b.dataset.kind === state.catKind);
  const cats = state.categories.filter((c) => c.kind === state.catKind);
  const ul = $("#cat-list");
  ul.replaceChildren();
  cats.forEach((c, i) => {
    const icon = el("input", { className: "icon-input", value: c.icon, maxLength: 8, ariaLabel: "圖示" });
    const name = el("input", { className: "cat-name", value: c.name, maxLength: 20, ariaLabel: "名稱" });
    const save = () => {
      if (icon.value === c.icon && name.value.trim() === c.name) return;
      if (!name.value.trim()) { name.value = c.name; return; }
      patchCat(c.id, { icon: icon.value, name: name.value.trim() });
    };
    icon.addEventListener("change", save);
    name.addEventListener("change", save);
    const up = el("button", { type: "button", textContent: "↑", disabled: i === 0, ariaLabel: "往上" });
    up.addEventListener("click", () => swapCat(cats, i, i - 1));
    const arch = el("button", { type: "button", textContent: c.archived ? "恢復" : "封存" });
    arch.addEventListener("click", () => patchCat(c.id, { archived: !c.archived }));
    const del = el("button", { type: "button", className: "danger", textContent: "刪除" });
    del.addEventListener("click", async () => {
      if (!confirm(`刪除分類「${c.name}」？`)) return;
      try { await api(`/api/categories/${c.id}`, { method: "DELETE" }); await refreshCats(); }
      catch (err) { $("#cat-error").textContent = err.message; }
    });
    ul.append(el("li", { className: c.archived ? "archived" : "" }, icon, name, up, arch, del));
  });
}

async function refreshCats() {
  $("#cat-error").textContent = "";
  await loadCategories();
  renderCats();
}
async function patchCat(id, body) {
  try { await api(`/api/categories/${id}`, { method: "PATCH", body }); await refreshCats(); }
  catch (err) { await refreshCats(); $("#cat-error").textContent = err.message; }
}
async function swapCat(cats, i, j) {
  // 先把這一類重新編號成 0..n-1，再交換兩個，順序才不會因為重複的 sort_order 亂掉
  const order = cats.map((c) => c.id);
  [order[i], order[j]] = [order[j], order[i]];
  try {
    await Promise.all(order.map((id, n) => api(`/api/categories/${id}`, { method: "PATCH", body: { sort_order: n } })));
  } catch (err) { $("#cat-error").textContent = err.message; }
  await refreshCats();
}

$("#cat-kind").addEventListener("click", (e) => { if (e.target.dataset.kind) { state.catKind = e.target.dataset.kind; renderCats(); } });
$("#open-categories").addEventListener("click", () => { $("#menu").hidden = true; $("#cat-error").textContent = ""; renderCats(); catDialog.showModal(); });
$("#cat-close").addEventListener("click", () => catDialog.close());
catDialog.addEventListener("close", () => loadMonth());

$("#cat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    await api("/api/categories", { method: "POST", body: { kind: state.catKind, name: f.name.value.trim(), icon: f.icon.value.trim() } });
    f.reset();
    await refreshCats();
  } catch (err) {
    $("#cat-error").textContent = err.message;
  }
});

// ---------- 啟動 ----------
(async () => {
  try {
    state.me = await api("/api/me");
    await enter();
  } catch {
    showLogin();
  }
})();
