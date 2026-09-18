"""Read-only dashboard.

Strictly a thin client: it only reads the SQLite file the scanner writes. It
opens no websocket to Binance and holds no state of its own, so the engine and
the Telegram alerts behave identically whether nobody or twenty people have the
page open. Killing the dashboard does not touch detection.

One responsive page serves desktop and phone — same table, same badges, same
drill-down, only the layout reflows. The phone is now a display client, so
"Add to Home Screen" is safe: nothing about the scanner depends on that tab
staying awake.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from aiohttp import web

from .config import (Config, NEEDS_RESTART, coerce_setting, settings_view)
from .storage import Storage

PAGE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0a0e0a">
<title>VolScan · сканер</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  :root{
    --bg:#0a0e0a; --panel:#0d120d; --line:#1e2b1e; --text:#c8d6c8; --dim:#5a6b5a;
    --green:#5eff8f; --amber:#ffd85e; --red:#ff6b6b; --blue:#7ab8ff; --orange:#ff9f43;
  }
  body{background:var(--bg);color:var(--text);font-family:'SF Mono',Consolas,Menlo,monospace;
       font-size:13px;min-height:100vh;-webkit-text-size-adjust:100%}
  header{position:sticky;top:0;z-index:20;background:var(--panel);
         border-bottom:1px solid var(--line);padding:10px 14px;
         padding-top:calc(10px + env(safe-area-inset-top))}
  .brand{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .logo{width:30px;height:30px;border-radius:3px;background:#1a2e1a;border:1px solid #2f4a2f;
        display:flex;align-items:center;justify-content:center;color:var(--green);font-size:15px}
  h1{font-size:13px;letter-spacing:.18em;color:var(--green);font-weight:600}
  .sub{font-size:10px;color:var(--dim);margin-top:2px}
  .stats{display:flex;gap:12px;flex-wrap:wrap;font-size:11px;margin-top:8px;color:var(--dim)}
  .stats b{color:var(--text);font-weight:600}
  .tabs{display:flex;gap:6px;margin-top:10px;overflow-x:auto;-webkit-overflow-scrolling:touch}
  .tabs button{flex:0 0 auto;background:transparent;border:1px solid var(--line);color:var(--dim);
    font-family:inherit;font-size:12px;padding:9px 14px;border-radius:3px;cursor:pointer;
    min-height:40px}
  .tabs button.on{background:#1a2e1a;color:var(--green);border-color:#2f4a2f}
  main{padding:10px 14px 40px;padding-bottom:calc(40px + env(safe-area-inset-bottom))}
  table{width:100%;border-collapse:collapse}
  th{font-size:10px;font-weight:400;color:var(--dim);text-align:right;padding:6px 6px;
     white-space:nowrap;letter-spacing:.04em}
  th:first-child,td:first-child{text-align:left}
  td{padding:9px 6px;border-bottom:1px solid #141a14;text-align:right;white-space:nowrap}
  tr.row{cursor:pointer}
  tr.row:active{background:#141c14}
  tr.accel{background:#1c2a10}
  tr.cand{background:#12240f}
  .sym{color:#e4ece4;font-weight:600}
  .q{color:#4a5a4a;font-size:11px}
  .badge{display:inline-block;font-size:10px;padding:2px 6px;border-radius:3px;
         border:1px solid;margin-right:3px;white-space:nowrap}
  .b-accel{color:#0a0e0a;background:var(--orange);border-color:var(--orange);font-weight:700}
  .b-cand{color:var(--amber);border-color:#4a3a1e;background:#1e1810}
  .b-accum{color:var(--blue);border-color:#2f3a4a;background:#131a24}
  .b-wake{color:var(--dim);border-color:var(--line)}
  .b-pre{color:var(--green);border-color:#2f4a2f;background:#131f13}
  .score{font-weight:700;font-size:15px}
  .s-hot{color:#0a0e0a;background:var(--green);border-radius:3px;padding:1px 7px}
  .s-mid{color:var(--amber)} .s-low{color:var(--dim)}
  .pos{color:var(--green)} .neg{color:var(--red)}
  .muted{color:var(--dim)}
  .empty{text-align:center;padding:40px;color:#3f4f3f}
  /* drill-down */
  .sheet{position:fixed;inset:0;background:rgba(4,7,4,.86);z-index:40;display:none;
         overflow:auto;padding:0}
  .sheet.open{display:block}
  .sheet-in{max-width:980px;margin:0 auto;background:var(--panel);min-height:100%;
            border-left:1px solid var(--line);border-right:1px solid var(--line)}
  .sheet-hd{position:sticky;top:0;background:var(--panel);border-bottom:1px solid var(--line);
            padding:12px 14px;padding-top:calc(12px + env(safe-area-inset-top));
            display:flex;align-items:center;justify-content:space-between;gap:10px}
  .close{background:#1a1e2e;border:1px solid #2f3a4a;color:var(--blue);font-family:inherit;
         font-size:13px;padding:9px 14px;border-radius:3px;cursor:pointer;min-height:40px}
  .sheet-bd{padding:14px}
  .kv{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:10px 0}
  .kv div{background:#0a0e0a;border:1px solid var(--line);border-radius:3px;padding:8px 10px}
  .kv span{display:block;font-size:10px;color:var(--dim)}
  .kv b{font-size:14px;font-weight:600}
  svg{width:100%;height:170px;display:block;background:#0a0e0a;border:1px solid var(--line);
      border-radius:3px;margin-top:6px}
  .lg{font-size:10px;color:var(--dim);margin-top:4px}
  .siglist{margin-top:14px}
  .sig{border:1px solid var(--line);border-radius:3px;padding:9px 10px;margin-bottom:7px;
       background:#0a0e0a}
  .sig .t{font-size:11px;color:var(--dim)}
  .sig .r{font-size:11px;color:#8fa08f;margin-top:3px;white-space:normal;line-height:1.45}
  /* settings panel */
  .sect{border:1px solid var(--line);border-radius:4px;margin-bottom:12px;background:#0a0e0a}
  .sect h2{font-size:12px;color:var(--green);letter-spacing:.1em;font-weight:600;
           padding:10px 12px;border-bottom:1px solid var(--line)}
  .fields{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:8px;padding:12px}
  .fld{display:flex;align-items:center;gap:7px;background:var(--bg);border:1px solid var(--line);
       border-radius:3px;padding:6px 8px;transition:border-color .2s}
  .fld.saved{border-color:var(--green)}
  .fld.bad{border-color:var(--red)}
  .fld label{flex:1 1 auto;color:var(--dim);font-size:11px;line-height:1.3;min-width:0}
  .fld label b{display:block;color:var(--text);font-weight:400;font-size:12px}
  .fld input[type=text]{width:88px;flex:0 0 auto;background:transparent;border:none;outline:none;
       color:var(--text);font-family:inherit;font-size:16px;text-align:right;min-width:0}
  .fld input[type=checkbox]{width:22px;height:22px;accent-color:var(--green);flex:0 0 auto}
  .fld button{flex:0 0 auto;background:#1a2e1a;border:1px solid #2f4a2f;color:var(--green);
       font-family:inherit;font-size:11px;padding:7px 10px;border-radius:3px;cursor:pointer;min-height:34px}
  .fld button:active{background:#233823}
  .warnrestart{color:var(--amber);font-size:10px}
  .note{font-size:11px;color:var(--dim);padding:0 12px 12px;line-height:1.5}
  .note.warn{color:var(--amber)}
  #toast{position:fixed;left:50%;transform:translateX(-50%);bottom:22px;z-index:60;
         background:#12240f;border:1px solid #2f4a2f;color:var(--green);padding:10px 16px;
         border-radius:4px;font-size:12px;display:none;max-width:90vw;text-align:center}
  #toast.bad{background:#2a1414;border-color:#5a2a2a;color:var(--red)}
  @media (max-width:760px){
    body{font-size:14px}
    .hide-s{display:none}
    th,td{padding:10px 4px}
    .score{font-size:16px}
    main{padding:8px 8px 40px}
    header{padding:10px 10px}
  }
</style>
</head>
<body>
<header>
  <div class="brand">
    <div class="logo">⚡</div>
    <div>
      <h1>VOLSCAN · SERVICE</h1>
      <div class="sub" id="sub">подключение…</div>
    </div>
  </div>
  <div class="stats" id="stats"></div>
  <div class="tabs" id="tabs">
    <button data-f="all" class="on">ВСЕ</button>
    <button data-f="accel">🚀 УСКОРЕНИЕ</button>
    <button data-f="cand">🎯 КАНДИДАТЫ</button>
    <button data-f="accum">🐋 НАКОПЛЕНИЕ</button>
    <button data-f="signals">📜 СИГНАЛЫ</button>
    <button data-f="settings">⚙ ФИЛЬТРЫ</button>
  </div>
</header>
<main>
  <div id="view"></div>
</main>

<div id="toast"></div>

<div class="sheet" id="sheet">
  <div class="sheet-in">
    <div class="sheet-hd">
      <div><b id="sh-sym"></b> <span class="q" id="sh-mkt"></span></div>
      <button class="close" id="sh-close">закрыть ✕</button>
    </div>
    <div class="sheet-bd">
      <div class="kv" id="sh-kv"></div>
      <div class="lg">цена (зелёная линия) и минутный оборот (столбцы) — последние часы</div>
      <svg id="sh-chart" viewBox="0 0 600 170" preserveAspectRatio="none"></svg>
      <div class="siglist" id="sh-sigs"></div>
    </div>
  </div>
</div>

<script>
let filter = "all", rows = [], sigs = [], stats = {};
const $ = s => document.querySelector(s);
const n = (v, d = 2) => (v === null || v === undefined || isNaN(v)) ? "—" : (+v).toFixed(d);
const money = v => {
  if (v === null || v === undefined || isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return (v/1e9).toFixed(2)+"B";
  if (a >= 1e6) return (v/1e6).toFixed(2)+"M";
  if (a >= 1e3) return (v/1e3).toFixed(1)+"K";
  return v.toFixed(0);
};
const price = v => (v === null || v === undefined) ? "—" : (+v).toPrecision(6);
const clock = ts => new Date(ts).toLocaleTimeString();

async function poll() {
  try {
    const r = await fetch("api/state", {cache: "no-store"});
    const d = await r.json();
    rows = d.rows || []; stats = d.stats || {}; sigs = d.signals || [];
    $("#sub").textContent = d.status || "";
    $("#stats").innerHTML = [
      ["пар", stats.pairs], ["сигналов", stats.signals], ["алертов", stats.alerts],
      ["ускорение", rows.filter(r => r.accel).length],
      ["TG", d.telegram ? "вкл" : "выкл"],
    ].map(([k, v]) => `${k} <b>${v ?? "—"}</b>`).join(" · ");
    render();
  } catch (e) {
    $("#sub").textContent = "нет связи с сервисом";
  }
  setTimeout(poll, 3000);
}

function badges(r) {
  let b = "";
  if (r.accel) b += `<span class="badge b-accel">🚀 УСКОРЕНИЕ</span>`;
  if (r.candidate) b += `<span class="badge b-cand">🎯 +10%</span>`;
  if (r.accumulating) b += `<span class="badge b-accum">🐋 НАКОПЛ</span>`;
  if (r.precursor) b += `<span class="badge b-pre">ФОН</span>`;
  if (r.compressed) b += `<span class="badge b-wake">СЖАТИЕ</span>`;
  return b;
}

function render() {
  if (filter === "signals") return renderSignals();
  if (filter === "settings") return renderSettings();
  let list = rows;
  if (filter === "accel") list = rows.filter(r => r.accel);
  if (filter === "cand") list = rows.filter(r => r.candidate);
  if (filter === "accum") list = rows.filter(r => r.accumulating);
  if (!list.length) { $("#view").innerHTML = `<div class="empty">пусто — ждём данные</div>`; return; }
  $("#view").innerHTML = `<table><thead><tr>
    <th>ПАРА</th><th>SCORE</th><th>ЦЕНА</th><th>Δ%1м</th><th>RVOL</th>
    <th class="hide-s">Δ1м $</th><th class="hide-s">ТЕЙКЕР</th><th class="hide-s">24Ч ОБЪЁМ</th>
  </tr></thead><tbody>` + list.map(r => {
    const sc = r.score ?? 0;
    const cls = sc >= 80 ? "s-hot" : (sc >= 60 ? "s-mid" : "s-low");
    const p1 = r.pct1m;
    return `<tr class="row ${r.accel ? "accel" : (r.candidate ? "cand" : "")}" data-k="${r.key}">
      <td><span class="sym">${r.base || r.symbol}</span><span class="q">/${r.market}</span><br>${badges(r)}</td>
      <td><span class="score ${cls}">${sc}</span></td>
      <td>${price(r.price)}</td>
      <td class="${p1 >= 0 ? "pos" : "neg"}">${p1 === undefined || p1 === null ? "—" : (p1 >= 0 ? "+" : "") + n(p1)}%</td>
      <td>${r.rvol === undefined || r.rvol === null ? "—" : "×" + n(r.rvol, 0)}</td>
      <td class="hide-s">$${money(r.d1m)}</td>
      <td class="hide-s">${r.taker === undefined || r.taker === null ? "—" : Math.round(r.taker*100) + "%"}</td>
      <td class="hide-s muted">$${money(r.day_qv)}</td>
    </tr>`;
  }).join("") + `</tbody></table>`;
  document.querySelectorAll("tr.row").forEach(tr =>
    tr.addEventListener("click", () => openPair(tr.dataset.k)));
}

function renderSignals() {
  if (!sigs.length) { $("#view").innerHTML = `<div class="empty">сигналов ещё не было</div>`; return; }
  $("#view").innerHTML = sigs.map(s => `<div class="sig">
      <div class="t">${clock(s.ts)} · <b>${s.symbol}</b> · ${s.market} · ${s.kind}
        ${s.alerted ? "· 📨 TG" : ""} · score ${s.score}</div>
      <div class="r">${(s.reasons || "").replace(/</g, "&lt;")}</div>
    </div>`).join("");
}

// ---- settings panel: same value+OK box as the old HTML scanner, but the value
// lives on the server, so PC and phone always agree and the engine picks it up
// on the very next tick.
let settingsData = null;
const esc = t => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
const tokenKey = "volscan_token";
const getToken = () => { try { return localStorage.getItem(tokenKey) || ""; } catch (e) { return ""; } };

function toast(msg, bad) {
  const t = $("#toast");
  t.textContent = msg; t.className = bad ? "bad" : "";
  t.style.display = "block";
  clearTimeout(t._h); t._h = setTimeout(() => { t.style.display = "none"; }, 3200);
}

async function renderSettings() {
  if (!settingsData) {
    try {
      settingsData = await (await fetch("api/settings", {cache: "no-store"})).json();
    } catch (e) { $("#view").innerHTML = `<div class="empty">не удалось загрузить настройки</div>`; return; }
  }
  const d = settingsData;
  $("#view").innerHTML = d.groups.map(g => `<div class="sect">
      <h2>${esc(g.title)}</h2>
      <div class="fields">${g.items.map(it => fieldHtml(it)).join("")}</div>
    </div>`).join("")
    + `<div class="sect"><h2>ДОСТУП</h2><div class="fields">
        <div class="fld"><label><b>токен этого браузера</b>нужен, только если включён dashboard_token</label>
        <input type="text" id="tok" value="${esc(getToken())}" placeholder="пусто">
        <button id="tok-ok">OK</button></div></div>
        <div class="note${d.protected ? " warn" : ""}">${d.protected
          ? "Запись настроек защищена токеном. Впишите его сюда один раз — он останется в этом браузере."
          : "Запись настроек НЕ защищена: любой, кто откроет этот адрес, может менять пороги. Если дашборд виден в сети — задайте dashboard_token в config.json (см. README)."}</div>
       </div>`
    + `<div class="note">Значения применяются к работающему сканеру сразу и сохраняются в базу — переживут перезапуск. Можно писать сокращения: <b>500m</b>, <b>3млн</b>, <b>50k</b>.</div>`;

  document.querySelectorAll("[data-apply]").forEach(b =>
    b.addEventListener("click", () => applyField(b.dataset.apply)));
  document.querySelectorAll("input[data-name][type=text]").forEach(i =>
    i.addEventListener("keydown", e => { if (e.key === "Enter") applyField(i.dataset.name); }));
  document.querySelectorAll("input[data-name][type=checkbox]").forEach(i =>
    i.addEventListener("change", () => applyField(i.dataset.name)));
  const tk = $("#tok-ok");
  if (tk) tk.addEventListener("click", () => {
    try { localStorage.setItem(tokenKey, $("#tok").value.trim()); } catch (e) {}
    toast("токен сохранён в этом браузере");
  });
}

function fieldHtml(it) {
  const warn = it.restart ? `<span class="warnrestart">нужен перезапуск</span>` : "";
  const hint = it.hint ? `${esc(it.hint)} ` : "";
  const lbl = `<label><b>${esc(it.label)}</b>${hint}${warn}</label>`;
  if (it.kind === "bool") {
    return `<div class="fld" id="fld-${it.name}">${lbl}
      <input type="checkbox" data-name="${it.name}" ${it.value ? "checked" : ""}></div>`;
  }
  return `<div class="fld" id="fld-${it.name}">${lbl}
    <input type="text" data-name="${it.name}" value="${esc(it.value)}"
      inputmode="${it.kind === "text" || it.kind === "secret" ? "text" : "decimal"}">
    <button data-apply="${it.name}">OK</button></div>`;
}

async function applyField(name) {
  const el = document.querySelector(`input[data-name="${name}"]`);
  const box = $("#fld-" + name);
  const value = el.type === "checkbox" ? el.checked : el.value;
  try {
    const r = await fetch("api/settings", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-VolScan-Token": getToken()},
      body: JSON.stringify({[name]: value}),
    });
    const d = await r.json();
    if (!d.ok) { box.className = "fld bad"; toast(d.error || "не применилось", true); return; }
    box.className = "fld saved";
    setTimeout(() => { box.className = "fld"; }, 1400);
    settingsData = null;
    toast((d.restart && d.restart.length)
      ? "сохранено, но это значение подхватится после перезапуска"
      : "применено");
  } catch (e) {
    box.className = "fld bad";
    toast("нет связи с сервисом", true);
  }
}

async function openPair(key) {
  const r = await fetch("api/pair?key=" + encodeURIComponent(key), {cache: "no-store"});
  const d = await r.json();
  const row = d.row || {};
  $("#sh-sym").textContent = row.symbol || key;
  $("#sh-mkt").textContent = row.market || "";
  $("#sh-kv").innerHTML = [
    ["score", row.score], ["цена", price(row.price)],
    ["Δ%1м", n(row.pct1m) + "%"], ["Δ%5м", n(row.pct5m) + "%"],
    ["RVOL", "×" + n(row.rvol, 0)], ["Δ1м", "$" + money(row.d1m)],
    ["тейкер", row.taker == null ? "—" : Math.round(row.taker*100) + "%"],
    ["тейкер 20м", row.taker_w == null ? "—" : Math.round(row.taker_w*100) + "%"],
    ["до 24ч хая", n(row.dist_high24) + "%"], ["RSI 1ч", n(row.hourly_rsi, 0)],
    ["24ч объём", "$" + money(row.day_qv)], ["ускорение", row.accel ? "ДА 🚀" : "нет"],
  ].map(([k, v]) => `<div><span>${k}</span><b>${v ?? "—"}</b></div>`).join("");
  drawChart(d.history || []);
  $("#sh-sigs").innerHTML = (d.signals || []).map(s => `<div class="sig">
      <div class="t">${clock(s.ts)} · ${s.kind} · score ${s.score} · ${price(s.price)}
        ${s.alerted ? "· 📨" : ""}${s.mfe ? " · max " + n(s.mfe) + "%" : ""}</div>
      <div class="r">${(s.reasons || "").replace(/</g, "&lt;")}</div>
    </div>`).join("") || `<div class="empty">по этой паре сигналов не было</div>`;
  $("#sheet").classList.add("open");
}

function drawChart(h) {
  const el = $("#sh-chart");
  if (!h.length) { el.innerHTML = `<text x="300" y="85" fill="#3f4f3f" font-size="12" text-anchor="middle">нет истории</text>`; return; }
  const W = 600, H = 170, pad = 6;
  const ps = h.map(x => x.price), vs = h.map(x => x.d1m || 0);
  const pmin = Math.min(...ps), pmax = Math.max(...ps), vmax = Math.max(...vs, 1);
  const x = i => pad + i * (W - 2*pad) / Math.max(h.length - 1, 1);
  const y = p => H - pad - (pmax === pmin ? (H-2*pad)/2 : (p - pmin) / (pmax - pmin) * (H - 2*pad) * 0.72);
  const bars = h.map((d, i) => {
    const bh = (d.d1m || 0) / vmax * (H - 2*pad) * 0.28;
    return `<rect x="${x(i).toFixed(1)}" y="${(H - pad - bh).toFixed(1)}" width="${Math.max((W-2*pad)/h.length - 0.5, 0.8).toFixed(1)}" height="${bh.toFixed(1)}" fill="${d.accel ? "#ff9f43" : "#24402a"}"/>`;
  }).join("");
  const line = h.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.price).toFixed(1)}`).join("");
  const dots = h.map((d, i) => d.accel ? `<circle cx="${x(i).toFixed(1)}" cy="${y(d.price).toFixed(1)}" r="2.2" fill="#ff9f43"/>` : "").join("");
  el.innerHTML = bars + `<path d="${line}" fill="none" stroke="#5eff8f" stroke-width="1.5"/>` + dots
    + `<text x="${pad+2}" y="12" fill="#5a6b5a" font-size="10">${pmax.toPrecision(6)}</text>`
    + `<text x="${pad+2}" y="${H-pad-2}" fill="#5a6b5a" font-size="10">${pmin.toPrecision(6)}</text>`;
}

$("#sh-close").addEventListener("click", () => $("#sheet").classList.remove("open"));
$("#sheet").addEventListener("click", e => { if (e.target.id === "sheet") $("#sheet").classList.remove("open"); });
document.querySelectorAll("#tabs button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll("#tabs button").forEach(x => x.classList.remove("on"));
  b.classList.add("on"); filter = b.dataset.f; render();
}));
poll();
</script>
</body>
</html>
"""


class Dashboard:
    def __init__(self, cfg: Config, storage: Storage, status_fn, on_settings=None):
        self.cfg = cfg
        self.storage = storage
        self.status_fn = status_fn
        self.on_settings = on_settings
        self.runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/pair/{key:.*}", self._index)
        app.router.add_get("/api/state", self._state)
        app.router.add_get("/api/pair", self._pair)
        app.router.add_get("/api/health", self._health)
        app.router.add_get("/api/settings", self._get_settings)
        app.router.add_post("/api/settings", self._post_settings)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.cfg.dashboard_host, self.cfg.dashboard_port)
        await site.start()

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def _index(self, _req):
        return web.Response(text=PAGE, content_type="text/html", charset="utf-8")

    async def _health(self, _req):
        return web.json_response({"ok": True, "ts": int(time.time() * 1000)})

    async def _state(self, _req):
        st = self.status_fn()
        return web.json_response({
            "rows": self.storage.live_rows(300),
            "signals": self.storage.recent_signals(120),
            "stats": self.storage.stats(),
            "status": st.get("status", ""),
            "telegram": st.get("telegram", False),
        })

    async def _pair(self, req):
        key = req.query.get("key", "")
        since = int(time.time() * 1000) - 6 * 3600 * 1000
        rows = [r for r in self.storage.live_rows(1000) if r.get("key") == key]
        return web.json_response({
            "row": rows[0] if rows else {},
            "history": self.storage.history(key, since),
            "signals": self.storage.recent_signals(40, key),
        })

    # ------------------------------------------------------------- settings
    # The panel edits the LIVE Config object this process is already using, so a
    # change takes effect on the very next tick — no restart, no polling delay.
    # It is also written to SQLite so it survives one.
    def _auth_ok(self, req) -> bool:
        token = (self.cfg.dashboard_token or "").strip()
        if not token:
            return True
        given = (req.headers.get("X-VolScan-Token")
                 or req.query.get("token") or "").strip()
        return given == token

    async def _get_settings(self, _req):
        return web.json_response({
            "groups": settings_view(self.cfg),
            "needs_restart": sorted(NEEDS_RESTART),
            "protected": bool((self.cfg.dashboard_token or "").strip()),
        })

    async def _post_settings(self, req):
        if not self._auth_ok(req):
            return web.json_response(
                {"ok": False, "error": "неверный токен доступа"}, status=403)
        try:
            body = await req.json()
        except Exception:
            return web.json_response({"ok": False, "error": "битый JSON"}, status=400)
        if not isinstance(body, dict) or not body:
            return web.json_response({"ok": False, "error": "пустой запрос"}, status=400)
        clean, errors = {}, []
        for name, raw in body.items():
            # a masked secret means "leave it alone"
            if isinstance(raw, str) and raw.startswith("\u2022"):
                continue
            try:
                clean[name] = coerce_setting(name, raw)
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            return web.json_response({"ok": False, "error": "; ".join(errors)}, status=400)
        if not clean:
            return web.json_response({"ok": True, "applied": {}, "restart": []})
        for name, value in clean.items():
            setattr(self.cfg, name, value)
        try:
            self.storage.save_settings(clean)
        except Exception as exc:
            return web.json_response(
                {"ok": False, "error": f"не удалось сохранить: {exc}"}, status=500)
        if self.on_settings:
            try:
                self.on_settings(clean)
            except Exception:
                pass
        return web.json_response({
            "ok": True,
            "applied": {k: (v if not _is_secret(k) else "сохранено") for k, v in clean.items()},
            "restart": sorted(set(clean) & NEEDS_RESTART),
        })


def _is_secret(name: str) -> bool:
    return "token" in name


