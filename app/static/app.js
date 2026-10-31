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
  month: thisMonth(),
  partyFilter: null,
  period: "all",
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
function short(n) {  // 圖表座標軸：1.2萬、350
  if (n >= 1e8) return `${+(n / 1e8).toFixed(1)}億`;
  if (n >= 1e4) return `${+(n / 1e4).toFixed(1)}萬`;
  return fmt.format(n);
}
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
    const b = el("button", { type: "button", textContent: l.name, className: l.id === state.ledger?.id ? "current" : "" });
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
  state.view = v === "records" ? "records" : "dashboard";
  const has = !!state.ledger;
  $("#view-empty").hidden = has;
  $("#view-dashboard").hidden = !has || state.view !== "dashboard";
  $("#view-records").hidden = !has || state.view !== "records";
  $("#add-btn").hidden = !has;
  $(".tabs").hidden = !has;
  for (const a of document.querySelectorAll(".tabs a")) a.classList.toggle("active", a.dataset.view === state.view);
  if (!has) return;
  if (state.view === "dashboard") loadDashboard();
  else loadMonth();
}
window.addEventListener("hashchange", showView);

// ---------- 儀表板 ----------
function periodRange() {
  const d = new Date();
  if (state.period === "month") return { start: `${thisMonth()}-01`, end: today(), label: `${d.getFullYear()} 年 ${d.getMonth() + 1} 月` };
  if (state.period === "year") return { start: `${d.getFullYear()}-01-01`, end: today(), label: `${d.getFullYear()} 年` };
  return { label: "全部期間" };
}

$("#period").addEventListener("click", (e) => {
  const p = e.target.dataset.period;
  if (!p) return;
  state.period = p;
  for (const b of $("#period").children) b.classList.toggle("active", b.dataset.period === p);
  loadDashboard();
});
$("#cat-period-kind").addEventListener("click", (e) => {
  const k = e.target.dataset.kind;
  if (!k) return;
  state.catKind = k;
  for (const b of $("#cat-period-kind").children) b.classList.toggle("active", b.dataset.kind === k);
  renderCatBars();
});

async function loadDashboard() {
  const r = periodRange();
  const q = new URLSearchParams();
  if (r.start) { q.set("start", r.start); q.set("end", r.end); }
  const d = await api(L(`/dashboard?${q}`));
  state.dash = d;
  const t = d.totals;
  $("#t-income").textContent = fmt.format(t.income);
  $("#t-expense").textContent = fmt.format(t.expense);
  $("#t-balance").textContent = money(t.balance);
  $("#t-count").textContent = fmt.format(t.count);
  $("#period-note").textContent = t.count && state.period === "all" ? `${r.label}：${t.first_date} 到 ${t.last_date}` : r.label;
  const any = d.monthly.some((m) => m.income || m.expense) || t.count;
  $("#dash-empty").hidden = !!t.count;
  $("#dash-body").hidden = !any;
  renderChart();
  renderMonthlyTable();
  renderPartyTable();
  renderCatBars();
}

function renderPartyTable() {
  const tbl = $("#party-table");
  tbl.replaceChildren();
  const rows = state.dash.by_party;
  tbl.append(el("thead", {}, el("tr", {}, ...["", "付款（支出）", "收款（收入）", "淨額"].map((h) => el("th", { textContent: h })))));
  const body = el("tbody");
  for (const p of rows) {
    body.append(el("tr", {},
      el("td", { textContent: p.name }),
      el("td", { textContent: fmt.format(p.expense) }),
      el("td", { textContent: fmt.format(p.income) }),
      el("td", { textContent: money(p.income - p.expense) })));
  }
  const sum = (k) => rows.reduce((a, p) => a + p[k], 0);
  tbl.append(body, el("tfoot", {}, el("tr", {},
    el("td", { textContent: "合計" }),
    el("td", { textContent: fmt.format(sum("expense")) }),
    el("td", { textContent: fmt.format(sum("income")) }),
    el("td", { textContent: money(sum("income") - sum("expense")) }))));
}

function renderCatBars() {
  const ul = $("#cat-bars");
  ul.replaceChildren();
  const cats = state.dash.by_category.filter((c) => c.kind === state.catKind);
  const total = cats.reduce((a, c) => a + c.amount, 0);
  if (!cats.length) { ul.append(el("li", { className: "muted small", textContent: `這段期間沒有${KIND[state.catKind]}` })); return; }
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

function renderMonthlyTable() {
  const tbl = $("#monthly-table");
  tbl.replaceChildren(el("thead", {}, el("tr", {}, ...["月份", "收入", "支出", "結餘"].map((h) => el("th", { textContent: h })))));
  const body = el("tbody");
  for (const m of [...state.dash.monthly].reverse()) {
    body.append(el("tr", {},
      el("td", { textContent: m.month }),
      el("td", { textContent: fmt.format(m.income) }),
      el("td", { textContent: fmt.format(m.expense) }),
      el("td", { textContent: money(m.income - m.expense) })));
  }
  tbl.append(body);
}

// 每月收支：群組長條圖（收入／支出並排），單一 y 軸，滑過顯示數字
const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}) {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
function niceMax(v) {
  if (v <= 0) return 1000;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}
function barPath(x, y, w, h, r) {  // 上方圓角、貼底的長條
  if (h <= 0) return "";
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function renderChart() {
  const box = $("#chart");
  const tip = $("#tooltip");
  box.replaceChildren();
  tip.hidden = true;
  if (!state.dash || $("#dash-body").hidden) return;
  const data = state.dash.monthly;
  const W = box.clientWidth || 600, H = box.clientHeight || 220;
  const m = { t: 8, r: 4, b: 24, l: 44 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const max = niceMax(Math.max(...data.map((d) => Math.max(d.income, d.expense))));
  const y = (v) => m.t + ih - (v / max) * ih;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "近 12 個月每月收入與支出長條圖" });

  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i, yy = Math.round(y(v)) + 0.5;
    root.append(svg("line", { x1: m.l, x2: W - m.r, y1: yy, y2: yy, class: "grid-line" }));
    const t = svg("text", { x: m.l - 8, y: yy + 4, "text-anchor": "end", class: "axis-text" });
    t.textContent = short(v);
    root.append(t);
  }
  const band = iw / data.length;
  const bw = Math.max(3, Math.min(18, (band - 8) / 2 - 1));
  const narrow = band < 34;
  data.forEach((d, i) => {
    const cx = m.l + band * i + band / 2;
    const g = svg("g");
    g.append(svg("path", { d: barPath(cx - bw - 1, y(d.income), bw, m.t + ih - y(d.income), 4), class: "bar-income" }));
    g.append(svg("path", { d: barPath(cx + 1, y(d.expense), bw, m.t + ih - y(d.expense), 4), class: "bar-expense" }));
    const mo = Number(d.month.slice(5));
    if (!narrow || i % 2 === (data.length - 1) % 2) {
      const t = svg("text", { x: cx, y: H - 6, "text-anchor": "middle", class: "axis-text" });
      t.textContent = mo === 1 ? `${d.month.slice(2, 4)}年1月` : `${mo}月`;
      g.append(t);
    }
    const hit = svg("rect", { x: m.l + band * i, y: m.t, width: band, height: ih, class: "hit" });
    const show = () => {
      for (const h of root.querySelectorAll(".hit.on")) h.classList.remove("on");
      hit.classList.add("on");
      tip.replaceChildren(
        el("b", { textContent: `${d.month.slice(0, 4)} 年 ${mo} 月` }),
        el("div", {}, el("span", {}, el("i", { className: "key key-income" }), "收入"), el("span", { textContent: fmt.format(d.income) })),
        el("div", {}, el("span", {}, el("i", { className: "key key-expense" }), "支出"), el("span", { textContent: fmt.format(d.expense) })),
        el("div", {}, el("span", { textContent: "結餘" }), el("span", { textContent: money(d.income - d.expense) })));
      tip.hidden = false;
      const cardRect = box.parentElement.getBoundingClientRect(), boxRect = box.getBoundingClientRect();
      const left = boxRect.left - cardRect.left + cx;
      const tw = tip.offsetWidth;
      tip.style.left = `${Math.max(8, Math.min(cardRect.width - tw - 8, left - tw / 2))}px`;
      tip.style.top = `${boxRect.top - cardRect.top + Math.max(0, y(Math.max(d.income, d.expense)) - tip.offsetHeight - 10)}px`;
    };
    hit.addEventListener("pointerenter", show);
    hit.addEventListener("click", show);
    g.append(hit);
    root.append(g);
  });
  root.addEventListener("pointerleave", (e) => {
    if (e.pointerType === "mouse") { tip.hidden = true; for (const h of root.querySelectorAll(".hit.on")) h.classList.remove("on"); }
  });
  box.append(root);
}
let resizeTimer;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => state.view === "dashboard" && renderChart(), 120); });

// ---------- 記帳 ----------
function shiftMonth(delta) {
  const [y, m] = state.month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  state.month = `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
  loadMonth();
}
$("#prev-month").addEventListener("click", () => shiftMonth(-1));
$("#next-month").addEventListener("click", () => shiftMonth(1));

function renderPartyFilter() {
  const box = $("#party-filter");
  box.replaceChildren();
  const parties = state.ledger.parties.filter((p) => !p.archived || p.id === state.partyFilter);
  const chip = (id, name) => {
    const b = el("button", { type: "button", textContent: name, className: state.partyFilter === id ? "active" : "" });
    b.setAttribute("aria-pressed", String(state.partyFilter === id));
    b.addEventListener("click", () => { state.partyFilter = id; renderPartyFilter(); loadMonth(); });
    return b;
  };
  box.append(chip(null, "全部"), ...parties.map((p) => chip(p.id, p.name)));
}

async function loadMonth() {
  const [y, m] = state.month.split("-");
  $("#month-label").textContent = `${y} 年 ${Number(m)} 月`;
  const q = new URLSearchParams({ month: state.month });
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
  $("#empty").textContent = state.partyFilter ? "這個月沒有這個人的記錄" : "這個月還沒有記錄，按「＋」記一筆";
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
  // 新增時：看的是本月就預設今天，看別的月份就預設那個月 1 號
  txForm.date.value = t ? t.date : (state.view !== "records" || state.month === thisMonth() ? today() : `${state.month}-01`);
  txForm.note.value = t ? t.note : "";
  $("#tx-delete").hidden = !t;
  $("#tx-meta").textContent = t ? `${t.created_by} 記的` + (t.updated_at !== t.created_at ? "（改過）" : "") : "";
  renderTxChoices();
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
    if (state.view === "records") {
      state.month = body.date.slice(0, 7);
      await loadMonth();
    } else {
      await loadDashboard();
    }
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
    await loadMonth();
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
function openNewLedger() {
  closePopovers();
  const f = $("#ledger-form");
  f.reset();
  $("#ledger-error").textContent = "";
  $("#ledger-parties").replaceChildren(...DEFAULT_PARTIES.map((n) => partyInputRow(n)));
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
    const led = await api("/api/ledgers", { method: "POST", body: { name, parties } });
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
