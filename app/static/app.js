// 記帳本前端：純 JS。所有使用者輸入都用 textContent 放進畫面，不用 innerHTML 拼字串
"use strict";

const $ = (s) => document.querySelector(s);
const fmt = new Intl.NumberFormat("zh-TW");
const WEEK = "日一二三四五六";
const KIND = { income: "收入", expense: "支出" };
const PARTY_VERB = { income: "誰收款", expense: "誰付款" };

const state = {
  me: null,
  ledgers: [],
  ledger: null,          // 目前帳本（含 parties、categories）
  view: "dashboard",
  partyFilter: null,
  catKind: "expense",    // 儀表板分類卡
  dash: null,
  tx: { kind: "expense", party: null, cat: null, editing: null },
  setCatKind: "expense", // 設定裡的分類
};

// ---------- 小工具 ----------
function pad(n) { return String(n).padStart(2, "0"); }
function thisMonth() { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`; }
function today() { const d = new Date(); return `${thisMonth()}-${pad(d.getDate())}`; }
function el(tag, props = {}, ...children) {
  const e = Object.assign(document.createElement(tag), props);
  for (const c of children) if (c != null) e.append(c);
  return e;
}
function money(n) { return (n < 0 ? "−" : "") + fmt.format(Math.abs(n)); }
function signed(kind, n) { return (kind === "income" ? "+" : "−") + fmt.format(n); }
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 無痕模式之類，不記也沒關係 */ } },
};

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
    throw new Error(Array.isArray(d) ? (d[0]?.msg?.replace(/^Value error, /, "") || "輸入的資料不正確") : d || `錯誤 ${res.status}`);
  }
  return data;
}
const L = (path = "") => `/api/ledgers/${state.ledger.id}${path}`;

// ---------- 登入 ----------
function showLogin() {
  $("#shell").hidden = true;
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

async function logout() {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  state.me = null;
  closePopovers();
  showLogin();
}
$("#logout").addEventListener("click", logout);
$("#logout-foot").addEventListener("click", logout);

async function enter() {
  $("#login").hidden = true;
  $("#shell").hidden = false;
  $("#me-label").textContent = `登入為 ${state.me.display_name}`;
  $("#me-foot").textContent = state.me.display_name;
  $("#user-initial").textContent = [...state.me.display_name][0] || "?";
  await loadLedgers();
  const saved = Number(store.get("account.ledger"));
  const active = state.ledgers.filter((l) => !l.archived);
  const pick = state.ledgers.find((l) => l.id === saved) || active[0];
  if (pick) await switchLedger(pick.id);
  else showView();
}

// ---------- 帳本 ----------
async function loadLedgers() {
  state.ledgers = await api("/api/ledgers");
  renderLedgerMenu();
}

async function switchLedger(id) {
  state.ledger = await api(`/api/ledgers/${id}`);
  store.set("account.ledger", String(id));
  state.partyFilter = null;
  $("#ledger-name").textContent = state.ledger.name;
  document.title = `${state.ledger.name} · 記帳本`;
  renderLedgerMenu();
  renderPartyFilter();
  closePopovers();
  showView();
}

async function reloadLedger() {
  state.ledger = await api(L());
  $("#ledger-name").textContent = state.ledger.name;
  renderPartyFilter();
}

function renderLedgerMenu() {
  const box = $("#ledger-list");
  box.replaceChildren();
  const active = state.ledgers.filter((l) => !l.archived);
  const archived = state.ledgers.filter((l) => l.archived);
  const item = (l) => {
    const b = el("button", { type: "button", className: l.id === state.ledger?.id ? "current" : "" },
      el("span", {}, l.name, l.settled ? el("span", { className: "tag", textContent: "已結算" }) : null));
    b.addEventListener("click", () => switchLedger(l.id));
    return b;
  };
  if (!state.ledgers.length) box.append(el("p", { className: "muted small", textContent: "還沒有帳本" }));
  active.forEach((l) => box.append(item(l)));
  if (archived.length) {
    box.append(el("div", { className: "group-label", textContent: "已封存" }));
    archived.forEach((l) => box.append(item(l)));
  }
  $("#ledger-settings").hidden = !state.ledger;
  if (!state.ledger) $("#ledger-name").textContent = "記帳本";
}

// ---------- 彈出選單 ----------
function closePopovers() {
  $("#ledger-menu").hidden = true;
  $("#user-menu").hidden = true;
  $("#ledger-btn").setAttribute("aria-expanded", "false");
}
function togglePopover(menu, btn) {
  const open = menu.hidden;
  closePopovers();
  menu.hidden = !open;
  btn?.setAttribute("aria-expanded", String(open));
}
$("#ledger-btn").addEventListener("click", (e) => { e.stopPropagation(); togglePopover($("#ledger-menu"), $("#ledger-btn")); });
$("#user-btn").addEventListener("click", (e) => { e.stopPropagation(); togglePopover($("#user-menu")); });
document.addEventListener("click", (e) => { if (!e.target.closest(".popover")) closePopovers(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePopovers(); });

// ---------- 頁面切換（網址 #dashboard／#records，上一頁可用） ----------
function showView() {
  const v = location.hash.slice(1);
  state.view = ["records", "settle"].includes(v) ? v : "dashboard";
  const has = !!state.ledger;
  const locked = has && state.ledger.settled;
  $("#view-empty").hidden = has;
  for (const name of ["dashboard", "records", "settle"]) $(`#view-${name}`).hidden = !has || state.view !== name;
  $("#add-btn").hidden = !has || locked || state.view === "settle";
  $("#add-inline").hidden = locked;
  $("#settled-notice").hidden = !locked;
  $(".tabs").hidden = !has;
  for (const a of document.querySelectorAll(".tabs a")) a.classList.toggle("active", a.dataset.view === state.view);
  if (!has) return;
  if (state.view === "dashboard") loadDashboard();
  else if (state.view === "records") loadRecords();
  else loadSettlement();
}
window.addEventListener("hashchange", showView);

// ---------- 儀表板 ----------
$("#cat-period-kind").addEventListener("click", (e) => {
  const k = e.target.dataset.kind;
  if (!k) return;
  state.catKind = k;
  for (const b of $("#cat-period-kind").children) b.classList.toggle("active", b.dataset.kind === k);
  renderCatBars();
});

async function loadDashboard() {
  const d = await api(L("/dashboard"));
  state.dash = d;
  const t = d.totals;
  $("#t-income").textContent = fmt.format(t.income);
  $("#t-expense").textContent = fmt.format(t.expense);
  $("#t-balance").textContent = money(t.balance);
  $("#t-count").textContent = fmt.format(t.count);
  $("#period-note").textContent = t.count
    ? (t.first_date === t.last_date ? t.first_date : `${t.first_date} 到 ${t.last_date}`) : "";
  $("#dash-empty").hidden = !!t.count;
  $("#dash-body").hidden = !t.count;
  renderPartyTable();
  renderCatBars();
}

function renderPartyTable() {
  const tbl = $("#party-table");
  tbl.replaceChildren();
  const rows = state.dash.by_party;
  tbl.append(el("thead", {}, el("tr", {}, ...["", "收款（收入）", "付款（支出）", "淨額"].map((h) => el("th", { textContent: h })))));
  const body = el("tbody");
  for (const p of rows) {
    body.append(el("tr", {},
      el("td", { textContent: p.name }),
      el("td", { textContent: fmt.format(p.income) }),
      el("td", { textContent: fmt.format(p.expense) }),
      el("td", { textContent: money(p.income - p.expense) })));
  }
  const sum = (k) => rows.reduce((a, p) => a + p[k], 0);
  tbl.append(body, el("tfoot", {}, el("tr", {},
    el("td", { textContent: "合計" }),
    el("td", { textContent: fmt.format(sum("income")) }),
    el("td", { textContent: fmt.format(sum("expense")) }),
    el("td", { textContent: money(sum("income") - sum("expense")) }))));
}

function renderCatBars() {
  const ul = $("#cat-bars");
  ul.replaceChildren();
  const cats = state.dash.by_category.filter((c) => c.kind === state.catKind);
  const total = cats.reduce((a, c) => a + c.amount, 0);
  if (!cats.length) { ul.append(el("li", { className: "muted small", textContent: `這本帳本沒有${KIND[state.catKind]}` })); return; }
  const max = cats[0].amount;
  for (const c of cats) {
    const fill = el("div", { className: "bar-fill" });
    fill.style.width = `${(c.amount / max) * 100}%`;
    fill.style.background = `var(--${state.catKind})`;
    ul.append(el("li", { title: `${c.name}：${fmt.format(c.amount)}（${c.count} 筆）` },
      el("span", { textContent: `${c.icon} ${c.name}`.trim() }),
      el("span", {}, el("span", { className: "v", textContent: fmt.format(c.amount) }),
        el("span", { className: "pct", textContent: `${Math.round((c.amount / total) * 100)}%` })),
      el("div", { className: "bar-track" }, fill)));
  }
}

// ---------- 記帳 ----------
function renderPartyFilter() {
  const box = $("#party-filter");
  box.replaceChildren();
  const parties = state.ledger.parties.filter((p) => !p.archived || p.id === state.partyFilter);
  const chip = (id, name) => {
    const b = el("button", { type: "button", textContent: name, className: state.partyFilter === id ? "active" : "" });
    b.setAttribute("aria-pressed", String(state.partyFilter === id));
    b.addEventListener("click", () => { state.partyFilter = id; renderPartyFilter(); loadRecords(); });
    return b;
  };
  box.append(chip(null, "全部"), ...parties.map((p) => chip(p.id, p.name)));
}

async function loadRecords() {
  const q = new URLSearchParams();
  if (state.partyFilter) q.set("party_id", state.partyFilter);
  const data = await api(L(`/transactions?${q}`));
  const s = data.summary;
  $("#m-income").textContent = fmt.format(s.income);
  $("#m-expense").textContent = fmt.format(s.expense);
  $("#m-balance").textContent = money(s.balance);
  renderList(data.items);
}

function renderList(items) {
  const list = $("#list");
  list.replaceChildren();
  $("#empty").hidden = items.length > 0;
  $("#empty").textContent = state.partyFilter ? "這本帳本沒有這個人的記錄" : "這本帳本還沒有記錄，按「＋」記一筆";
  $("#records").hidden = !items.length;
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
      const party = t.party_name || "未指定";
      const sub = [`${party} ${t.kind === "income" ? "收款" : "付款"}`, t.note, t.created_by && `${t.created_by} 記`].filter(Boolean).join(" · ");
      const cat = el("div", { className: "cat", textContent: t.category_name });
      cat.dataset.icon = t.category_icon || "";
      const li = el("li", { className: "tx", tabIndex: 0 },
        el("span", { className: "ic", textContent: t.category_icon || "•" }),
        el("div", { className: "main" }, cat, el("div", { className: "sub", textContent: sub })),
        el("span", { className: "party" }, el("span", { className: "badge", textContent: party })),
        el("span", { className: "note", textContent: t.note || "" }),
        el("span", { className: "who", textContent: t.created_by }),
        el("span", { className: "amt" }, el("i", { className: `key key-${t.kind}`, title: KIND[t.kind] }), signed(t.kind, t.amount)));
      li.addEventListener("click", () => openTx(t));
      li.addEventListener("keydown", (e) => { if (e.key === "Enter") openTx(t); });
      ul.append(li);
    }
    list.append(el("section", { className: "day" },
      el("div", { className: "day-head" },
        el("span", { textContent: `${d.getMonth() + 1}/${d.getDate()}（${WEEK[d.getDay()]}）` }),
        el("span", { textContent: (net > 0 ? "+" : "") + money(net) })),
      ul));
  }
}

// ---------- 新增／編輯一筆 ----------
const txDialog = $("#tx-dialog");
const txForm = $("#tx-form");

function renderTxChoices() {
  const { kind } = state.tx;
  for (const b of $("#tx-kind").children) b.classList.toggle("active", b.dataset.kind === kind);
  $("#party-label").textContent = PARTY_VERB[kind];

  const parties = state.ledger.parties.filter((p) => !p.archived || p.id === state.tx.party);
  const pbox = $("#tx-parties");
  pbox.replaceChildren();
  for (const p of parties) {
    const b = el("button", { type: "button", textContent: p.name, className: p.id === state.tx.party ? "active" : "" });
    b.addEventListener("click", () => { state.tx.party = p.id; renderTxChoices(); });
    pbox.append(b);
  }

  const cats = state.ledger.categories.filter((c) => c.kind === kind && (!c.archived || c.id === state.tx.cat));
  if (!cats.some((c) => c.id === state.tx.cat)) state.tx.cat = cats[0]?.id ?? null;
  const grid = $("#tx-cats");
  grid.replaceChildren();
  for (const c of cats) {
    const b = el("button", { type: "button", textContent: `${c.icon} ${c.name}`.trim(), className: c.id === state.tx.cat ? "active" : "" });
    b.addEventListener("click", () => { state.tx.cat = c.id; renderTxChoices(); });
    grid.append(b);
  }
  if (!cats.length) grid.append(el("p", { className: "muted small", textContent: `沒有${KIND[kind]}分類，請到帳本設定新增` }));
}
$("#tx-kind").addEventListener("click", (e) => { if (e.target.dataset.kind) { state.tx.kind = e.target.dataset.kind; renderTxChoices(); } });

function openTx(t = null) {
  const last = Number(store.get(`account.party.${state.ledger.id}`));
  const active = state.ledger.parties.filter((p) => !p.archived);
  state.tx = {
    kind: t ? t.kind : state.tx.kind,
    party: t ? t.party_id : (state.partyFilter || (active.some((p) => p.id === last) ? last : active[0]?.id ?? null)),
    cat: t ? t.category_id : null,
    editing: t,
  };
  $("#tx-title").textContent = t ? "編輯記錄" : `記一筆 · ${state.ledger.name}`;
  $("#tx-error").textContent = "";
  txForm.amount.value = t ? t.amount : "";
  txForm.date.value = t ? t.date : today();
  txForm.note.value = t ? t.note : "";
  const locked = state.ledger.settled;
  $("#tx-delete").hidden = !t || locked;
  txForm.querySelector("button[type=submit]").hidden = locked;
  $("#tx-cancel").textContent = locked ? "關閉" : "取消";
  $("#tx-meta").textContent = (t ? `${t.created_by} 記的` + (t.updated_at !== t.created_at ? "（改過）" : "") : "")
    + (locked ? "　🔒 已結算，不能修改" : "");
  renderTxChoices();
  for (const x of txForm.querySelectorAll("input, #tx-kind button, #tx-parties button, #tx-cats button")) x.disabled = locked;
  txDialog.showModal();
  if (!t && matchMedia("(min-width: 900px)").matches) txForm.amount.focus();
}
$("#add-btn").addEventListener("click", () => openTx());
$("#add-inline").addEventListener("click", () => openTx());
$("#tx-cancel").addEventListener("click", () => txDialog.close());

txForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const amount = Number(txForm.amount.value.replace(/[,\s]/g, ""));
  if (!Number.isInteger(amount) || amount <= 0) { $("#tx-error").textContent = "金額要是大於 0 的整數"; return; }
  if (!state.tx.party) { $("#tx-error").textContent = `請選${PARTY_VERB[state.tx.kind]}`; return; }
  if (!state.tx.cat) { $("#tx-error").textContent = "請選分類"; return; }
  const body = { kind: state.tx.kind, amount, date: txForm.date.value, party_id: state.tx.party, category_id: state.tx.cat, note: txForm.note.value };
  try {
    if (state.tx.editing) await api(L(`/transactions/${state.tx.editing.id}`), { method: "PATCH", body });
    else await api(L("/transactions"), { method: "POST", body });
    store.set(`account.party.${state.ledger.id}`, String(state.tx.party));
    txDialog.close();
    if (state.view === "records") await loadRecords();
    else await loadDashboard();
  } catch (err) {
    $("#tx-error").textContent = err.message;
  }
});

$("#tx-delete").addEventListener("click", async () => {
  const t = state.tx.editing;
  if (!t || !confirm(`刪除這筆 ${t.category_name} ${fmt.format(t.amount)}？`)) return;
  try {
    await api(L(`/transactions/${t.id}`), { method: "DELETE" });
    txDialog.close();
    await loadRecords();
  } catch (err) {
    $("#tx-error").textContent = err.message;
  }
});

// ---------- 新增帳本 ----------
const ledgerDialog = $("#ledger-dialog");
const DEFAULT_PARTIES = ["成", "圓", "寶貝基金"];

function partyInputRow(value = "") {
  const input = el("input", { value, maxLength: 20, placeholder: "名字", ariaLabel: "付款／收款人" });
  const rm = el("button", { type: "button", textContent: "✕", ariaLabel: "移除" });
  const li = el("li", {}, input, rm);
  rm.addEventListener("click", () => li.remove());
  return li;
}
// 一個月一本帳本：名稱預設「YYYY 年 M 月」（這個月的已經有了就給下個月）；付款／收款人與分類沿用目前帳本
function suggestLedgerName() {
  const d = new Date();
  for (let i = 0; i < 24; i++) {
    const x = new Date(d.getFullYear(), d.getMonth() + i, 1);
    const name = `${x.getFullYear()} 年 ${x.getMonth() + 1} 月`;
    if (!state.ledgers.some((l) => l.name === name)) return name;
  }
  return "";
}
function openNewLedger() {
  closePopovers();
  const f = $("#ledger-form");
  f.reset();
  $("#ledger-error").textContent = "";
  f.name.value = suggestLedgerName();
  const cur = state.ledger;
  const names = cur ? cur.parties.filter((p) => !p.archived).map((p) => p.name) : [];
  $("#ledger-parties").replaceChildren(...(names.length ? names : DEFAULT_PARTIES).map((n) => partyInputRow(n)));
  $("#copy-cats-row").hidden = !cur;
  $("#copy-cats").checked = !!cur;
  if (cur) $("#copy-cats-label").textContent = `沿用「${cur.name}」的分類`;
  const hint = () => { $("#cats-hint").hidden = $("#copy-cats").checked && !!cur; };
  $("#copy-cats").onchange = hint;
  hint();
  ledgerDialog.showModal();
}
$("#new-ledger").addEventListener("click", openNewLedger);
$("#onboard-create").addEventListener("click", openNewLedger);
$("#ledger-cancel").addEventListener("click", () => ledgerDialog.close());
$("#ledger-add-party").addEventListener("click", () => {
  const row = partyInputRow();
  $("#ledger-parties").append(row);
  row.querySelector("input").focus();
});
$("#ledger-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = e.target.name.value.trim();
  const parties = [...$("#ledger-parties").querySelectorAll("input")].map((i) => i.value.trim()).filter(Boolean);
  if (!parties.length) { $("#ledger-error").textContent = "至少要有一位付款／收款人"; return; }
  try {
    const body = { name, parties };
    if (state.ledger && $("#copy-cats").checked) body.copy_categories_from = state.ledger.id;
    const led = await api("/api/ledgers", { method: "POST", body });
    ledgerDialog.close();
    await loadLedgers();
    await switchLedger(led.id);
  } catch (err) {
    $("#ledger-error").textContent = err.message;
  }
});

// ---------- 帳本設定 ----------
const setDialog = $("#settings-dialog");
const setErr = (msg = "") => { $("#set-error").textContent = msg; };

async function settingsCall(fn) {
  setErr();
  try { await fn(); } catch (err) { setErr(err.message); }
  await reloadLedger();
  renderSettings();
}

// 一列可編輯的項目：（圖示）名稱、往上、封存／恢復、刪除
function manageRow(item, i, list, { path, icon }) {
  const name = el("input", { value: item.name, maxLength: 20, ariaLabel: "名稱" });
  const iconIn = icon ? el("input", { className: "icon-input", value: item.icon, maxLength: 8, ariaLabel: "圖示" }) : null;
  const save = () => {
    const body = {};
    if (name.value.trim() && name.value.trim() !== item.name) body.name = name.value.trim();
    if (iconIn && iconIn.value !== item.icon) body.icon = iconIn.value;
    if (!name.value.trim()) name.value = item.name;
    if (Object.keys(body).length) settingsCall(() => api(L(`${path}/${item.id}`), { method: "PATCH", body }));
  };
  name.addEventListener("change", save);
  iconIn?.addEventListener("change", save);
  const up = el("button", { type: "button", textContent: "↑", disabled: i === 0, ariaLabel: "往上" });
  up.addEventListener("click", () => settingsCall(() => {
    // 整組重新編號再交換，sort_order 有重複時順序才不會亂
    const order = list.map((x) => x.id);
    [order[i - 1], order[i]] = [order[i], order[i - 1]];
    return Promise.all(order.map((id, n) => api(L(`${path}/${id}`), { method: "PATCH", body: { sort_order: n } })));
  }));
  const arch = el("button", { type: "button", textContent: item.archived ? "恢復" : "封存" });
  arch.addEventListener("click", () => settingsCall(() => api(L(`${path}/${item.id}`), { method: "PATCH", body: { archived: !item.archived } })));
  const del = el("button", { type: "button", className: "danger", textContent: "刪除" });
  del.addEventListener("click", () => {
    if (confirm(`刪除「${item.name}」？`)) settingsCall(() => api(L(`${path}/${item.id}`), { method: "DELETE" }));
  });
  return el("li", { className: item.archived ? "archived" : "" }, iconIn, name, up, arch, del);
}

function renderSettings() {
  const led = state.ledger;
  $("#set-name").value = led.name;
  $("#set-parties").replaceChildren(...led.parties.map((p, i, a) => manageRow(p, i, a, { path: "/parties" })));
  for (const b of $("#set-cat-kind").children) b.classList.toggle("active", b.dataset.kind === state.setCatKind);
  const cats = led.categories.filter((c) => c.kind === state.setCatKind);
  $("#set-cats").replaceChildren(...cats.map((c, i, a) => manageRow(c, i, a, { path: "/categories", icon: true })));
  $("#set-archive").textContent = led.archived ? "取消封存" : "封存帳本";
}

$("#ledger-settings").addEventListener("click", () => { closePopovers(); setErr(); renderSettings(); setDialog.showModal(); });
$("#set-cat-kind").addEventListener("click", (e) => { if (e.target.dataset.kind) { state.setCatKind = e.target.dataset.kind; renderSettings(); } });
$("#set-name").addEventListener("change", (e) => {
  const name = e.target.value.trim();
  if (!name) { e.target.value = state.ledger.name; return; }
  settingsCall(async () => { await api(L(), { method: "PATCH", body: { name } }); await loadLedgers(); });
});
$("#party-add-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target;
  settingsCall(async () => { await api(L("/parties"), { method: "POST", body: { name: f.name.value.trim() } }); f.reset(); });
});
$("#cat-add-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target;
  settingsCall(async () => {
    await api(L("/categories"), { method: "POST", body: { kind: state.setCatKind, name: f.name.value.trim(), icon: f.icon.value.trim() } });
    f.reset();
  });
});
$("#set-archive").addEventListener("click", () => settingsCall(async () => {
  await api(L(), { method: "PATCH", body: { archived: !state.ledger.archived } });
  await loadLedgers();
}));
$("#set-delete").addEventListener("click", async () => {
  if (!confirm(`刪除帳本「${state.ledger.name}」？`)) return;
  try {
    await api(L(), { method: "DELETE" });
    store.set("account.ledger", "");
    setDialog.close();
    state.ledger = null;
    await loadLedgers();
    const next = state.ledgers.find((l) => !l.archived);
    if (next) await switchLedger(next.id);
    else { renderLedgerMenu(); showView(); }
  } catch (err) {
    setErr(err.message);
  }
});
$("#set-close").addEventListener("click", () => setDialog.close());
setDialog.addEventListener("close", () => { $("#ledger-name").textContent = state.ledger?.name ?? "記帳本"; if (state.ledger) showView(); });

// ---------- 結算 ----------
function diffText(diff) {
  if (diff > 0) return { label: "超支", value: fmt.format(diff), over: true };
  if (diff < 0) return { label: "剩餘", value: fmt.format(-diff), over: false };
  return { label: "剛好用完", value: "0", over: false };
}

function localTime(utc) {  // SQLite datetime('now') 是 UTC 的「YYYY-MM-DD HH:MM:SS」
  const d = new Date(utc.replace(" ", "T") + "Z");
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

async function loadSettlement() {
  const s = await api(L("/settlement"));
  const status = $("#settle-status");
  status.textContent = s.settled ? "已結算" : "試算中";
  status.className = s.settled ? "status done" : "status";
  $("#settle-note").textContent = s.settled
    ? `${s.settled_by || ""} 於 ${localTime(s.settled_at)} 完成結算，數字是當時的預算與花費。`
    : "用目前的全域預算即時試算；按「完成結算」後會存下結果，並鎖定這本帳本的記錄。";
  $("#settle-nobudget").hidden = s.settled || s.lines.some((l) => l.budget > 0);

  const cards = $("#settle-cards");
  cards.replaceChildren();
  for (const l of s.lines) {
    const d = diffText(l.diff);
    const pct = l.budget > 0 ? Math.round((l.spent / l.budget) * 100) : null;
    const meter = el("div", { className: `meter${d.over ? " over" : ""}` }, el("div"));
    meter.firstChild.style.width = `${pct === null ? (l.spent ? 100 : 0) : Math.min(100, pct)}%`;
    cards.append(el("section", { className: "settle-card" },
      el("h3", { textContent: l.name }),
      el("div", { className: "verdict", textContent: d.label }),
      el("div", { className: `big${d.over ? " over" : ""}`, textContent: d.value }),
      meter,
      el("div", { className: "meter-label", textContent: pct === null ? "沒有設定預算" : `已用預算 ${pct}%` }),
      el("dl", {},
        el("dt", { textContent: "預算" }), el("dd", { textContent: fmt.format(l.budget) }),
        el("dt", { textContent: "花費" }), el("dd", { textContent: fmt.format(l.spent) }),
        el("dt", { textContent: "差額（花費 − 預算）" }), el("dd", { textContent: (l.diff > 0 ? "+" : "") + money(l.diff) }))));
  }

  const tbl = $("#settle-table");
  tbl.replaceChildren(el("thead", {}, el("tr", {}, ...["", "預算", "花費", "差額", ""].map((h) => el("th", { textContent: h })))));
  const body = el("tbody");
  const row = (name, x, strong) => {
    const d = diffText(x.diff);
    return el("tr", {},
      el("td", { textContent: name }),
      el("td", { textContent: fmt.format(x.budget) }),
      el("td", { textContent: fmt.format(x.spent) }),
      el("td", { textContent: (x.diff > 0 ? "+" : "") + money(x.diff) }),
      el("td", { textContent: d.label, className: d.over ? "danger" : "muted" }));
  };
  for (const l of s.lines) body.append(row(l.name, l));
  tbl.append(body, el("tfoot", {}, row("合計", s.total)));

  $("#settle-do").hidden = s.settled;
  $("#settle-undo").hidden = !s.settled;
}

async function afterSettleChange() {
  await reloadLedger();
  await loadLedgers();
  showView();
}
$("#settle-do").addEventListener("click", async () => {
  if (!confirm(`完成「${state.ledger.name}」的結算？\n結算後這本帳本的記錄會鎖定，之後改全域預算也不會影響這次的結果。`)) return;
  try { await api(L("/settlement"), { method: "POST" }); } catch (err) { alert(err.message); }
  await afterSettleChange();
});
$("#settle-undo").addEventListener("click", async () => {
  if (!confirm("取消結算？記錄會解鎖，結算會改回用目前的全域預算試算。")) return;
  try { await api(L("/settlement"), { method: "DELETE" }); } catch (err) { alert(err.message); }
  await afterSettleChange();
});

// ---------- 全域設定（成、圓的預算） ----------
const globalDialog = $("#global-dialog");
async function openGlobal() {
  closePopovers();
  $("#global-error").textContent = "";
  const budgets = await api("/api/budgets");
  $("#budget-inputs").replaceChildren(...budgets.map((b) =>
    el("label", {}, el("span", { textContent: b.name }),
      el("input", { name: b.name, inputMode: "numeric", value: b.amount ? b.amount : "", placeholder: "0", autocomplete: "off", ariaLabel: `${b.name}的預算` }))));
  globalDialog.showModal();
}
$("#global-settings").addEventListener("click", openGlobal);
$("#settle-set-budget").addEventListener("click", openGlobal);
$("#settle-edit-budget").addEventListener("click", openGlobal);
$("#global-cancel").addEventListener("click", () => globalDialog.close());
$("#global-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const items = [];
  for (const input of $("#budget-inputs").querySelectorAll("input")) {
    const v = input.value.replace(/[,\s]/g, "");
    const amount = v === "" ? 0 : Number(v);
    if (!Number.isInteger(amount) || amount < 0) { $("#global-error").textContent = `${input.name}的預算要是 0 或正整數`; return; }
    items.push({ name: input.name, amount });
  }
  try {
    await api("/api/budgets", { method: "PUT", body: { items } });
    globalDialog.close();
    if (state.ledger && state.view === "settle") loadSettlement();
  } catch (err) {
    $("#global-error").textContent = err.message;
  }
});

// ---------- 外觀（自動／淺色／深色） ----------
// 自動＝跟著系統；手動選的存在這台裝置的 localStorage（index.html 開頭會在載入 CSS 前先套用）
const THEME_BG = { light: "#f5f5f4", dark: "#1a1a19" };
function applyTheme(choice) {
  const root = document.documentElement;
  if (choice === "light" || choice === "dark") root.dataset.theme = choice;
  else { delete root.dataset.theme; choice = "auto"; }
  for (const b of document.querySelectorAll("[data-theme-choice]")) {
    b.classList.toggle("active", b.dataset.themeChoice === choice);
    b.setAttribute("aria-pressed", String(b.dataset.themeChoice === choice));
  }
  // 手機瀏覽器網址列的顏色：手動選了就兩個都換成那個顏色，自動就各自對應系統
  const metas = document.querySelectorAll('meta[name="theme-color"]');
  metas.forEach((m) => { m.content = choice === "auto" ? THEME_BG[m.media.includes("dark") ? "dark" : "light"] : THEME_BG[choice]; });
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-theme-choice]");
  if (!b) return;
  e.stopPropagation();  // 不要讓點選外觀把選單關掉
  store.set("account.theme", b.dataset.themeChoice);
  applyTheme(b.dataset.themeChoice);
}, true);
applyTheme(store.get("account.theme"));

// ---------- 啟動 ----------
(async () => {
  try {
    state.me = await api("/api/me");
  } catch {
    showLogin();
    return;
  }
  await enter();
})();
