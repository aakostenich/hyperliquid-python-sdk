/* Hyperliquid Liquid Glass Terminal — app.js */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const LS = {
  get(k, d) {
    try {
      const v = localStorage.getItem(k);
      return v ? JSON.parse(v) : d;
    } catch {
      return d;
    }
  },
  set(k, v) {
    localStorage.setItem(k, JSON.stringify(v));
  },
};

const state = {
  data: null,
  market: null,
  period: "week",
  loading: false,
  chart: null,
  tab: "portfolio",
  watchlist: LS.get("hl_watchlist", ["BTC", "ETH", "SOL", "HYPE"]),
  alerts: LS.get("hl_alerts", []),
  liveCoin: "BTC",
  candles: [],
  whaleOnly: false,
  es: null,
};

// ── formatters ────────────────────────────────────────────────────────────

function fnum(x) {
  const n = Number(x);
  return Number.isFinite(n) ? n : 0;
}
function fmtUsd(x, { signed = false, compact = true } = {}) {
  const n = fnum(x);
  const abs = Math.abs(n);
  let body;
  if (compact && abs >= 1e9) body = (n / 1e9).toFixed(2) + "B";
  else if (compact && abs >= 1e6) body = (n / 1e6).toFixed(2) + "M";
  else if (compact && abs >= 1e4) body = n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  else if (abs >= 100) body = n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  else body = n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  if (signed && n > 0) return "+" + body;
  return body;
}
function fmtPct(x, signed = false, digits = 2) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  const n = fnum(x);
  const s = n.toFixed(digits) + "%";
  return signed && n > 0 ? "+" + s : s;
}
function fmtPx(x) {
  if (x == null) return "—";
  const n = fnum(x);
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (n >= 1) return n.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return n.toPrecision(4);
}
function fmtSz(x) {
  const n = fnum(x);
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(3) + "M";
  if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return n.toLocaleString(undefined, { maximumFractionDigits: 6 });
}
function fmtTs(ms) {
  if (!ms) return "—";
  return new Date(ms).toLocaleString(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function shortAddr(a) {
  if (!a || a.length < 12) return a || "—";
  return a.slice(0, 6) + "…" + a.slice(-4);
}
function clsPnL(n) {
  if (n > 0) return "pos";
  if (n < 0) return "neg";
  return "";
}
function riskTag(dist) {
  if (dist == null) return { t: "n/a", c: "na" };
  if (dist < 5) return { t: "CRIT", c: "crit" };
  if (dist < 15) return { t: "HIGH", c: "high" };
  if (dist < 30) return { t: "MED", c: "med" };
  return { t: "OK", c: "ok" };
}
function bufferWidth(dist) {
  if (dist == null || Number.isNaN(dist)) return 0;
  if (dist < 0) return 2;
  return Math.max(4, Math.min(100, (Math.log10(dist + 1) / Math.log10(101)) * 100));
}
function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  el.classList.toggle("error", isError);
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => (el.hidden = true), 250);
  }, 3200);
}
function downloadCsv(filename, rows) {
  const csv = rows.map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── API ───────────────────────────────────────────────────────────────────

async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

// ── Tabs ──────────────────────────────────────────────────────────────────

function setTab(name) {
  state.tab = name;
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  ["portfolio", "markets", "live", "analytics", "tools"].forEach((id) => {
    const el = $(`#panel-${id}`);
    if (el) el.hidden = id !== name;
  });
  const url = new URL(location.href);
  url.searchParams.set("tab", name);
  history.replaceState(null, "", url);
  if (name === "markets" && !state.market) loadMarkets();
  if (name === "analytics") renderAnalytics();
  if (name === "live") renderWatchlist();
}

// ── Portfolio (existing renderers, condensed) ─────────────────────────────

function setMeter(el, pct, { invert = false } = {}) {
  if (!el) return;
  const v = Math.max(0, Math.min(100, fnum(pct)));
  const fill = $("i", el);
  if (fill) fill.style.width = v + "%";
  el.classList.remove("warn", "danger");
  const danger = invert ? v < 15 : v >= 70;
  const warn = invert ? v < 30 : v >= 45;
  if (danger) el.classList.add("danger");
  else if (warn) el.classList.add("warn");
}

function setLoading(on) {
  state.loading = on;
  const btn = $("#load-btn");
  btn.classList.toggle("loading", on);
  btn.disabled = on;
  $(".btn-spinner", btn).hidden = !on;
  $("#status-pill").textContent = on ? "loading…" : state.data ? "live" : "idle";
  if (on && !state.data) {
    $("#empty").hidden = true;
    $("#dashboard").hidden = true;
    $("#skeleton").hidden = false;
  }
  if (!on) $("#skeleton").hidden = true;
}

function renderOverview(d) {
  $("#kpi-av").textContent = fmtUsd(d.account_value);
  $("#kpi-addr").textContent = shortAddr(d.address);
  $("#kpi-addr").title = d.address;
  $("#kpi-wd").textContent = fmtUsd(d.withdrawable);
  $("#kpi-free").textContent = `free ${fmtPct(d.risk?.free_margin_pct)}`;
  setMeter($("#free-meter"), d.risk?.free_margin_pct, { invert: true });
  $("#kpi-mu").textContent = fmtUsd(d.margin_used);
  $("#kpi-mu-pct").textContent = `${fmtPct(d.margin_used_pct)} · ntl ${fmtUsd(d.total_notional)}`;
  setMeter($("#margin-meter"), d.margin_used_pct);
  const up = fnum(d.total_u_pnl);
  const upEl = $("#kpi-upnl");
  upEl.textContent = fmtUsd(up, { signed: true });
  upEl.className = "kpi-value mono " + clsPnL(up);
  const modes = [];
  if (d.n_cross) modes.push(`${d.n_cross} cross`);
  if (d.n_isolated) modes.push(`${d.n_isolated} isolated`);
  $("#kpi-mode").textContent = modes.length
    ? modes.join(" · ") + ` · ${fnum(d.risk?.leverage_effective).toFixed(2)}x`
    : "flat";
}

function renderRisk(d) {
  const r = d.risk || {};
  const level = r.risk_level || "FLAT";
  $("#risk-level").textContent = level;
  $("#risk-level").className = "risk-badge " + level;
  const score = Math.max(0, Math.min(100, fnum(r.risk_score)));
  $("#risk-score").textContent = String(Math.round(score));
  const circ = 2 * Math.PI * 52;
  const fg = $("#ring-fg");
  fg.style.strokeDasharray = String(circ);
  requestAnimationFrame(() => {
    fg.style.strokeDashoffset = String(circ * (1 - score / 100));
  });
  fg.style.stroke = score >= 75 ? "var(--red)" : score >= 45 ? "var(--amber)" : "url(#ringGrad)";
  $("#risk-lev").textContent = fnum(r.leverage_effective).toFixed(2) + "x";
  $("#risk-mu").textContent = fmtPct(r.margin_used_pct);
  $("#risk-free").textContent = fmtPct(r.free_margin_pct);
  $("#risk-closest").textContent =
    r.min_dist_to_liq_pct != null ? `${r.min_dist_coin} · ${fmtPct(r.min_dist_to_liq_pct)}` : "—";
  $("#risk-notes").innerHTML = (r.notes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  const ranked = [...(d.positions || [])]
    .sort((a, b) => (a.dist_to_liq_pct ?? 1e18) - (b.dist_to_liq_pct ?? 1e18))
    .slice(0, 15);
  $("#risk-table tbody").innerHTML = ranked.length
    ? ranked
        .map((p) => {
          const tag = riskTag(p.dist_to_liq_pct);
          return `<tr>
        <td><strong>${escapeHtml(p.coin)}</strong></td>
        <td><span class="side ${p.side}">${p.side}</span></td>
        <td class="num">${fmtPct(p.dist_to_liq_pct)} <span class="tag ${tag.c}">${tag.t}</span></td>
        <td class="num"><div class="buf"><span class="buf-track"><i style="width:${bufferWidth(p.dist_to_liq_pct)}%"></i></span></div></td>
        <td class="num">${fmtPx(p.liquidation_px)}</td>
        <td class="num">${fmtPx(p.mark_px)}</td>
        <td class="num ${clsPnL(p.u_pnl)}">${fmtUsd(p.u_pnl, { signed: true })}</td>
      </tr>`;
        })
        .join("")
    : `<tr><td colspan="7" style="color:var(--text-mute);text-align:center;padding:20px">No positions</td></tr>`;
}

function smoothPath(ctx, points) {
  if (points.length < 2) return;
  ctx.moveTo(points[0].x, points[0].y);
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i === 0 ? i : i - 1];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] || p2;
    ctx.bezierCurveTo(
      p1.x + (p2.x - p0.x) / 6,
      p1.y + (p2.y - p0.y) / 6,
      p2.x - (p3.x - p1.x) / 6,
      p2.y - (p3.y - p1.y) / 6,
      p2.x,
      p2.y
    );
  }
}

function drawLineChart(canvasId, history, { tipId } = {}) {
  const canvas = $(canvasId);
  if (!canvas) return;
  const tip = tipId ? $(tipId) : null;
  const ctx = canvas.getContext("2d");
  const dpr = devicePixelRatio || 1;
  const cssW = canvas.clientWidth || canvas.parentElement.clientWidth;
  const cssH = canvas.clientHeight || 220;
  canvas.width = Math.floor(cssW * dpr);
  canvas.height = Math.floor(cssH * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  if (!history.length) {
    ctx.fillStyle = "rgba(255,255,255,0.35)";
    ctx.font = "13px Inter";
    ctx.fillText("No data", 16, cssH / 2);
    return;
  }
  const pad = { t: 16, r: 12, b: 28, l: 54 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;
  const vals = history.map((p) => p[1]);
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  if (lo === hi) {
    lo *= 0.99;
    hi *= 1.01;
  }
  const mid = (lo + hi) / 2;
  const half = ((hi - lo) / 2) * 1.08 || 1;
  lo = mid - half;
  hi = mid + half;
  const span = hi - lo || 1;
  const xAt = (i) => pad.l + (i / Math.max(1, history.length - 1)) * w;
  const yAt = (v) => pad.t + (1 - (v - lo) / span) * h;
  const points = history.map((p, i) => ({ x: xAt(i), y: yAt(p[1]), t: p[0], v: p[1] }));
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (h * i) / 4;
    ctx.strokeStyle = "rgba(255,255,255,0.05)";
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(pad.l + w, y);
    ctx.stroke();
    ctx.fillStyle = "rgba(210,220,255,0.35)";
    ctx.font = "10px JetBrains Mono, monospace";
    ctx.textAlign = "right";
    ctx.fillText(fmtUsd(hi - (span * i) / 4), pad.l - 6, y + 3);
  }
  const up = vals[vals.length - 1] >= vals[0];
  const c1 = up ? "rgba(77,255,181,0.95)" : "rgba(255,109,128,0.95)";
  const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + h);
  grad.addColorStop(0, up ? "rgba(77,255,181,0.25)" : "rgba(255,109,128,0.22)");
  grad.addColorStop(1, "transparent");
  ctx.beginPath();
  smoothPath(ctx, points);
  ctx.lineTo(points[points.length - 1].x, pad.t + h);
  ctx.lineTo(points[0].x, pad.t + h);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();
  ctx.beginPath();
  smoothPath(ctx, points);
  ctx.strokeStyle = c1;
  ctx.lineWidth = 2.2;
  ctx.stroke();
  const last = points[points.length - 1];
  ctx.fillStyle = "#fff";
  ctx.beginPath();
  ctx.arc(last.x, last.y, 3, 0, Math.PI * 2);
  ctx.fill();
  if (tip) {
    canvas.onmousemove = (e) => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      let best = 0,
        bestD = 1e9;
      points.forEach((p, i) => {
        const d = Math.abs(p.x - mx);
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      });
      const p = points[best];
      tip.hidden = false;
      tip.textContent = `${fmtTs(p.t)} · ${fmtUsd(p.v, { compact: false })}`;
      tip.style.left = Math.min(Math.max(p.x, 50), cssW - 50) + "px";
      tip.style.top = Math.max(24, p.y) + "px";
    };
    canvas.onmouseleave = () => {
      tip.hidden = true;
    };
  }
}

function renderEquity(d) {
  const equity = d.equity || {};
  const preferred = ["day", "week", "month", "allTime", "perpDay", "perpWeek", "perpMonth", "perpAllTime"];
  const ordered = [...preferred.filter((k) => equity[k]), ...Object.keys(equity).filter((k) => !preferred.includes(k))];
  $("#period-grid").innerHTML = ordered
    .map((k) => {
      const ep = equity[k];
      const pnl = fnum(ep.pnl);
      return `<div class="period-chip ${k === state.period ? "active" : ""}" data-period="${escapeHtml(k)}">
      <div class="p-name">${escapeHtml(k)}</div>
      <div class="p-pnl ${clsPnL(pnl)}">${fmtUsd(pnl, { signed: true })}</div>
    </div>`;
    })
    .join("");
  $$("#period-grid .period-chip").forEach((el) =>
    el.addEventListener("click", () => {
      state.period = el.dataset.period;
      if (["day", "week", "month", "allTime", "perpWeek"].includes(state.period)) $("#period").value = state.period;
      renderEquity(d);
    })
  );
  let key = state.period;
  if (!equity[key]?.history?.length) key = ordered.find((k) => equity[k]?.history?.length) || ordered[0];
  const ep = equity[key];
  const hist = ep?.history || [];
  if (ep && hist.length) {
    const start = hist[0][1],
      end = hist[hist.length - 1][1],
      delta = end - start;
    $("#equity-summary").innerHTML = `
      <span class="chip">Period <strong>${escapeHtml(ep.period)}</strong></span>
      <span class="chip">${fmtUsd(start)} → <strong>${fmtUsd(end)}</strong></span>
      <span class="chip ${clsPnL(delta)}">Δ <strong>${fmtUsd(delta, { signed: true })}</strong></span>
      <span class="chip">PnL <strong class="${clsPnL(ep.pnl)}">${fmtUsd(ep.pnl, { signed: true })}</strong></span>`;
    drawLineChart("#equity-chart", hist, { tipId: "#chart-tip" });
  } else {
    $("#equity-summary").innerHTML = `<span class="chip">No history</span>`;
    drawLineChart("#equity-chart", []);
  }
}

function renderPositions(d) {
  const rows = d.positions || [];
  $("#pos-count").textContent = `(${rows.length})`;
  $("#pos-table tbody").innerHTML = rows.length
    ? rows
        .map(
          (p) => `<tr>
      <td><strong>${escapeHtml(p.coin)}</strong>
        <button class="star-btn ${state.watchlist.includes(p.coin) ? "on" : ""}" data-coin="${escapeHtml(p.coin)}">★</button></td>
      <td><span class="side ${p.side}">${p.side}</span></td>
      <td class="num">${fmtSz(p.size)}</td>
      <td class="num">${fmtPx(p.entry_px)}</td>
      <td class="num">${fmtPx(p.mark_px)}</td>
      <td class="num ${clsPnL(p.u_pnl)}">${fmtUsd(p.u_pnl, { signed: true })}</td>
      <td class="num ${clsPnL(p.roe_pct)}">${fmtPct(p.roe_pct, true)}</td>
      <td class="num">${fmtPx(p.liquidation_px)}</td>
      <td class="num">${fmtPct(p.dist_to_liq_pct)}</td>
      <td class="num ${clsPnL(p.funding_since_open)}">${fmtUsd(p.funding_since_open, { signed: true })}</td>
      <td class="num ${clsPnL(p.funding_all_time)}">${fmtUsd(p.funding_all_time, { signed: true })}</td>
      <td style="color:var(--text-dim)">${escapeHtml(p.margin_mode)} ${p.leverage}x</td>
    </tr>`
        )
        .join("")
    : `<tr><td colspan="12" style="color:var(--text-mute);text-align:center;padding:24px">No positions</td></tr>`;
  $$("#pos-table .star-btn").forEach((b) =>
    b.addEventListener("click", () => {
      toggleWatch(b.dataset.coin);
      b.classList.toggle("on", state.watchlist.includes(b.dataset.coin));
    })
  );
}

function renderOrders(d) {
  const oo = d.open_orders || [];
  const ho = (d.historical_orders || []).slice(0, 40);
  $("#oo-count").textContent = `(${oo.length})`;
  $("#ho-count").textContent = `(${(d.historical_orders || []).length})`;
  $("#oo-table tbody").innerHTML = oo.length
    ? oo
        .map(
          (o) => `<tr>
      <td><strong>${escapeHtml(o.coin)}</strong></td>
      <td><span class="side ${o.side}">${o.side}</span></td>
      <td class="num">${fmtSz(o.size)}</td>
      <td class="num">${fmtPx(o.limit_px)}</td>
      <td style="color:var(--text-dim)">${escapeHtml(o.order_type || "")}</td>
      <td style="color:var(--text-mute)">${fmtTs(o.timestamp)}</td></tr>`
        )
        .join("")
    : `<tr><td colspan="6" style="color:var(--text-mute);text-align:center;padding:20px">None</td></tr>`;
  $("#ho-table tbody").innerHTML = ho.length
    ? ho
        .map(
          (o) => `<tr>
      <td><strong>${escapeHtml(o.coin)}</strong></td>
      <td><span class="side ${o.side}">${o.side}</span></td>
      <td class="num">${fmtSz(o.orig_sz || o.size)}</td>
      <td class="num">${fmtPx(o.limit_px)}</td>
      <td style="color:var(--text-dim)">${escapeHtml(o.status)}</td>
      <td style="color:var(--text-mute)">${fmtTs(o.status_timestamp || o.timestamp)}</td></tr>`
        )
        .join("")
    : `<tr><td colspan="6" style="color:var(--text-mute);text-align:center;padding:20px">None</td></tr>`;
}

function renderPnl(d) {
  const rows = (d.coin_pnl || []).slice(0, 30);
  $("#pnl-table tbody").innerHTML = rows.length
    ? rows
        .map(
          (r) => `<tr>
      <td><strong>${escapeHtml(r.coin)}</strong></td>
      <td class="num ${clsPnL(r.closed_pnl)}">${fmtUsd(r.closed_pnl, { signed: true })}</td>
      <td class="num">${fmtUsd(r.fees)}</td>
      <td class="num ${clsPnL(r.realized_net)}">${fmtUsd(r.realized_net, { signed: true })}</td>
      <td class="num">${fmtUsd(r.volume)}</td>
      <td class="num">${r.n_fills}</td></tr>`
        )
        .join("")
    : `<tr><td colspan="6" style="color:var(--text-mute);text-align:center;padding:20px">No fills</td></tr>`;
}

function renderSubsVaults(d) {
  const subs = d.sub_accounts || [];
  const vaults = d.vault_equities || [];
  $("#subs-list").innerHTML = subs.length
    ? subs
        .map((s, i) => {
          const name = s.name || s.subAccountUser || `sub-${i}`;
          const ch = s.clearinghouseState || {};
          const av = ch.marginSummary?.accountValue ?? s.accountValue;
          return `<li>${escapeHtml(String(name))}${av != null ? `<strong>${fmtUsd(av)}</strong>` : ""}</li>`;
        })
        .join("")
    : `<li class="empty">None</li>`;
  $("#vaults-list").innerHTML = vaults.length
    ? vaults
        .map((v) => `<li>${escapeHtml(shortAddr(v.vaultAddress || v.vault || "?"))}<strong>${fmtUsd(v.equity)}</strong></li>`)
        .join("")
    : `<li class="empty">None</li>`;
}

function checkAlerts(d, market) {
  const hits = [];
  for (const a of state.alerts) {
    const thr = fnum(a.value);
    if (a.type === "funding" && market) {
      for (const m of market.markets || []) {
        if (a.coin && m.coin !== a.coin) continue;
        if (Math.abs(m.funding_pct) >= thr) hits.push(`Funding ${m.coin} ${fmtPct(m.funding_pct, true, 4)}/h ≥ ${thr}%`);
      }
    }
    if (a.type === "liq" && d) {
      for (const p of d.positions || []) {
        if (a.coin && p.coin !== a.coin) continue;
        if (p.dist_to_liq_pct != null && p.dist_to_liq_pct <= thr)
          hits.push(`${p.coin} dist to liq ${fmtPct(p.dist_to_liq_pct)} ≤ ${thr}%`);
      }
    }
    if (a.type === "upnl" && d) {
      for (const p of d.positions || []) {
        if (a.coin && p.coin !== a.coin) continue;
        if (Math.abs(p.u_pnl) >= thr) hits.push(`${p.coin} |uPnL| ${fmtUsd(p.u_pnl)} ≥ $${thr}`);
      }
    }
  }
  const box = $("#alert-hits");
  if (box) {
    box.innerHTML = hits.length
      ? hits.slice(0, 12).map((h) => `<div class="hit">${escapeHtml(h)}</div>`).join("")
      : `<div class="hint-inline">No alert hits</div>`;
  }
  if (hits.length) toast(`${hits.length} alert hit(s)`, false);
}

function renderAll(d) {
  state.data = d;
  $("#empty").hidden = true;
  $("#skeleton").hidden = true;
  $("#dashboard").hidden = false;
  $("#network-pill").innerHTML = `<i class="pulse"></i>${escapeHtml(d.network || "mainnet")}`;
  $("#status-pill").textContent = "live";
  if (d.fetched_at) $("#fetched-at").textContent = "Updated " + fmtTs(d.fetched_at);
  renderOverview(d);
  renderRisk(d);
  renderEquity(d);
  renderPositions(d);
  renderOrders(d);
  renderPnl(d);
  renderSubsVaults(d);
  checkAlerts(d, state.market);
  // wire user fills on live hub
  if (d.address) {
    fetch("/api/live/user", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: d.address }),
    }).catch(() => {});
  }
  if (state.tab === "analytics") renderAnalytics();
}

async function load(address, { refresh = false } = {}) {
  address = (address || "").trim();
  if (!address) {
    toast("Enter an address", true);
    return;
  }
  setLoading(true);
  try {
    const q = new URLSearchParams({ address });
    if (refresh) q.set("refresh", "1");
    const data = await api(`/api/account?${q}`);
    state.period = $("#period")?.value || "week";
    renderAll(data);
    const url = new URL(location.href);
    url.searchParams.set("address", address);
    history.replaceState(null, "", url);
  } catch (err) {
    toast(err.message || String(err), true);
    $("#status-pill").textContent = "error";
    if (!state.data) {
      $("#skeleton").hidden = true;
      $("#empty").hidden = false;
    }
  } finally {
    setLoading(false);
  }
}

// ── Markets ───────────────────────────────────────────────────────────────

async function loadMarkets() {
  try {
    $("#mkt-meta").textContent = "loading…";
    state.market = await api("/api/market");
    renderMarkets();
    checkAlerts(state.data, state.market);
    // coin datalist
    const dl = $("#coin-list");
    if (dl) {
      dl.innerHTML = (state.market.markets || [])
        .slice()
        .sort((a, b) => a.coin.localeCompare(b.coin))
        .map((m) => `<option value="${escapeHtml(m.coin)}">`)
        .join("");
    }
  } catch (e) {
    toast(e.message, true);
  }
}

function fundingColor(pct) {
  // pct is %/h e.g. 0.01
  const x = Math.max(-1, Math.min(1, pct / 0.05)); // scale
  if (x >= 0) {
    const a = 0.12 + Math.abs(x) * 0.45;
    return `rgba(255, 109, 128, ${a})`;
  }
  const a = 0.12 + Math.abs(x) * 0.45;
  return `rgba(77, 255, 181, ${a})`;
}

function renderMarkets() {
  const m = state.market;
  if (!m) return;
  $("#mkt-meta").textContent = `${m.count} perps · ${fmtTs(m.fetched_at)}`;

  const topVol = m.top_volume?.[0];
  const topFund = m.top_funding_long?.[0] || m.opportunities?.[0];
  const topOi = m.top_oi?.[0];
  const gainer = m.top_gainers?.[0];
  $("#mkt-stats").innerHTML = [
    ["Top volume", topVol ? `${topVol.coin} · ${fmtUsd(topVol.volume_24h)}` : "—"],
    ["Hottest funding", topFund ? `${topFund.coin} · ${fmtPct(topFund.funding_pct, true, 4)}/h` : "—"],
    ["Largest OI", topOi ? `${topOi.coin} · ${fmtUsd(topOi.open_interest_usd)}` : "—"],
    ["Top gainer", gainer ? `${gainer.coin} · ${fmtPct(gainer.change_24h_pct, true)}` : "—"],
  ]
    .map(
      ([l, v]) => `<article class="card glass stat-card"><div class="sl">${l}</div><div class="sv mono" style="font-size:15px">${escapeHtml(v)}</div></article>`
    )
    .join("");

  // heatmap
  $("#funding-heatmap").innerHTML = (m.heatmap || [])
    .map(
      (h) => `<div class="heat-cell" data-coin="${escapeHtml(h.coin)}" style="background:${fundingColor(h.funding_pct)}" title="${escapeHtml(h.coin)}">
      <div class="hc">${escapeHtml(h.coin)}</div>
      <div class="hv ${clsPnL(h.funding_pct)}">${fmtPct(h.funding_pct, true, 3)}</div>
    </div>`
    )
    .join("");
  $$("#funding-heatmap .heat-cell").forEach((el) =>
    el.addEventListener("click", () => {
      state.liveCoin = el.dataset.coin;
      $("#live-coin").value = state.liveCoin;
      $("#fh-coin").value = state.liveCoin;
      setTab("live");
      setLiveCoin(state.liveCoin);
    })
  );

  // opportunities
  $("#opp-table tbody").innerHTML = (m.opportunities || [])
    .map(
      (o) => `<tr>
      <td><strong>${escapeHtml(o.coin)}</strong></td>
      <td><span class="side ${o.side}">${o.side}</span></td>
      <td class="num ${clsPnL(o.funding_pct)}">${fmtPct(o.funding_pct, true, 4)}</td>
      <td class="num">${fmtPct(o.funding_apr_pct, true, 1)}</td>
      <td class="num">${fmtUsd(o.volume_24h)}</td>
      <td style="color:var(--text-dim);white-space:normal;max-width:280px;font-size:11.5px">${escapeHtml(o.thesis)}</td>
    </tr>`
    )
    .join("");

  renderMarketTable();
}

function renderMarketTable() {
  const m = state.market;
  if (!m) return;
  const q = ($("#mkt-filter").value || "").trim().toUpperCase();
  const sort = $("#mkt-sort").value;
  let rows = [...(m.markets || [])];
  if (q) rows = rows.filter((r) => r.coin.toUpperCase().includes(q));
  rows.sort((a, b) => {
    if (sort === "coin") return a.coin.localeCompare(b.coin);
    if (sort === "funding_pct_asc") return a.funding_pct - b.funding_pct;
    const av = a[sort] ?? 0;
    const bv = b[sort] ?? 0;
    return bv - av;
  });
  $("#mkt-table tbody").innerHTML = rows
    .map(
      (r) => `<tr>
      <td><strong>${escapeHtml(r.coin)}</strong>
        <button class="star-btn ${state.watchlist.includes(r.coin) ? "on" : ""}" data-coin="${escapeHtml(r.coin)}">★</button></td>
      <td class="num">${fmtPx(r.mark)}</td>
      <td class="num ${clsPnL(r.change_24h_pct)}">${fmtPct(r.change_24h_pct, true)}</td>
      <td class="num ${clsPnL(r.funding_pct)}">${fmtPct(r.funding_pct, true, 4)}</td>
      <td class="num">${fmtPct(r.funding_apr_pct, true, 1)}</td>
      <td class="num">${fmtUsd(r.open_interest_usd)}</td>
      <td class="num">${fmtUsd(r.volume_24h)}</td>
      <td><button class="btn sm" data-live="${escapeHtml(r.coin)}" style="height:28px;padding:0 10px">Live</button></td>
    </tr>`
    )
    .join("");
  $$("#mkt-table .star-btn").forEach((b) =>
    b.addEventListener("click", () => {
      toggleWatch(b.dataset.coin);
      renderMarketTable();
      renderWatchlist();
    })
  );
  $$("#mkt-table [data-live]").forEach((b) =>
    b.addEventListener("click", () => {
      state.liveCoin = b.dataset.live;
      $("#live-coin").value = state.liveCoin;
      setTab("live");
      setLiveCoin(state.liveCoin);
    })
  );
}

async function loadPredicted() {
  try {
    const data = await api("/api/predicted-fundings");
    const rows = (data.predicted || []).slice(0, 40);
    $("#pred-table tbody").innerHTML = rows
      .map((r) => {
        const hl = r.hl?.funding_pct;
        const bin = r.venues?.find((v) => v.venue === "BinPerp");
        const byb = r.venues?.find((v) => v.venue === "BybitPerp");
        const arb = r.arb?.find((a) => a.vs === "BinPerp");
        return `<tr>
          <td><strong>${escapeHtml(r.coin)}</strong></td>
          <td class="num ${clsPnL(hl)}">${hl != null ? fmtPct(hl, true, 4) : "—"}</td>
          <td class="num">${bin ? fmtPct(bin.funding_pct, true, 4) : "—"}</td>
          <td class="num">${byb ? fmtPct(byb.funding_pct, true, 4) : "—"}</td>
          <td class="num ${clsPnL(arb?.hl_minus_other_pct)}">${arb ? fmtPct(arb.hl_minus_other_pct, true, 4) : "—"}</td>
        </tr>`;
      })
      .join("");
  } catch (e) {
    toast(e.message, true);
  }
}

async function loadFundingHistory() {
  const coin = $("#fh-coin").value.trim() || "BTC";
  try {
    const data = await api(`/api/funding-history?coin=${encodeURIComponent(coin)}&days=7`);
    const hist = (data.history || []).map((h) => [h.time, h.funding_pct]);
    drawLineChart("#funding-chart", hist);
  } catch (e) {
    toast(e.message, true);
  }
}

// ── Live WS (SSE) ─────────────────────────────────────────────────────────

function connectStream() {
  if (state.es) state.es.close();
  const es = new EventSource("/api/stream");
  state.es = es;
  $("#ws-pill").textContent = "WS · connecting";
  es.onopen = () => {
    $("#ws-pill").textContent = "WS · on";
    $("#ws-pill").classList.add("on");
    $("#live-status").textContent = "stream connected";
  };
  es.onerror = () => {
    $("#ws-pill").textContent = "WS · retry";
    $("#ws-pill").classList.remove("on");
    $("#live-status").textContent = "reconnecting…";
  };
  es.onmessage = (ev) => {
    try {
      handleLive(JSON.parse(ev.data));
    } catch {
      /* ignore */
    }
  };
}

function handleLive(msg) {
  if (msg.type === "hello") {
    $("#live-status").textContent = `coin ${msg.live?.coin || "—"} · whales ≥ $${fmtUsd(msg.live?.whale_usd || 75000)}`;
    $("#whale-thr").textContent = `≥ $${fmtUsd(msg.live?.whale_usd || 75000)}`;
  }
  if (msg.type === "snapshot" || msg.type === "coin") {
    if (msg.book) renderBook(msg.book);
    if (msg.candles) {
      state.candles = msg.candles;
      drawCandles();
    }
    if (msg.trades) msg.trades.forEach((t) => pushTape(t));
    if (msg.coin) {
      state.liveCoin = msg.coin;
      $("#book-title").textContent = `Order book · ${msg.coin}`;
      $("#candle-title").textContent = `${msg.coin} · ${msg.interval || $("#live-interval").value}`;
    }
  }
  if (msg.type === "mids" && msg.mids) {
    const mid = msg.mids[state.liveCoin];
    if (mid) $("#live-mid").textContent = fmtPx(mid);
  }
  if (msg.type === "l2Book") renderBook(msg.data);
  if (msg.type === "trades") (msg.trades || []).forEach((t) => pushTape(t));
  if (msg.type === "whales") (msg.whales || []).forEach((t) => pushWhale(t));
  if (msg.type === "candle") {
    const c = msg.candle;
    if (state.candles.length && state.candles[state.candles.length - 1].t === c.t) state.candles[state.candles.length - 1] = c;
    else state.candles.push(c);
    if (state.candles.length > 200) state.candles.shift();
    drawCandles();
  }
}

function renderBook(book) {
  if (!book?.levels) return;
  const asks = (book.levels[1] || []).slice(0, 12).reverse();
  const bids = (book.levels[0] || []).slice(0, 12);
  const maxSz = Math.max(1, ...asks.map((l) => fnum(l.sz)), ...bids.map((l) => fnum(l.sz)));
  const row = (l, side) => {
    const sz = fnum(l.sz);
    const px = fnum(l.px);
    const w = (sz / maxSz) * 100;
    return `<div class="ob-row ${side}"><span class="bar" style="width:${w}%"></span>
      <span>${fmtPx(px)}</span><span>${fmtSz(sz)}</span><span class="muted">${l.n ?? ""}</span></div>`;
  };
  $("#ob-asks").innerHTML = asks.map((l) => row(l, "ask")).join("");
  $("#ob-bids").innerHTML = bids.map((l) => row(l, "bid")).join("");
  if (asks.length && bids.length) {
    const bestAsk = fnum(asks[asks.length - 1].px);
    const bestBid = fnum(bids[0].px);
    const mid = (bestAsk + bestBid) / 2;
    const spr = bestAsk - bestBid;
    $("#ob-spread").textContent = `spread ${fmtPx(spr)} · mid ${fmtPx(mid)}`;
    $("#live-mid").textContent = fmtPx(mid);
  }
}

function pushTape(t) {
  if (state.whaleOnly && !t.whale) return;
  if (t.coin && t.coin !== state.liveCoin) return;
  const el = $("#trade-tape");
  const row = document.createElement("div");
  row.className = `tape-row ${t.side === "BUY" ? "buy" : "sell"}`;
  row.innerHTML = `<span>${t.side === "BUY" ? "B" : "S"}</span><span>${fmtPx(t.px)}</span><span>${fmtSz(t.sz)}</span><span class="muted">${fmtUsd(t.notional)}</span>`;
  el.prepend(row);
  while (el.children.length > 80) el.removeChild(el.lastChild);
}

function pushWhale(t) {
  const el = $("#whale-tape");
  const row = document.createElement("div");
  row.className = `tape-row ${t.side === "BUY" ? "buy" : "sell"}`;
  row.innerHTML = `<span>${escapeHtml(t.coin || "")}</span><span>${t.side}</span><span>${fmtUsd(t.notional)}</span><span class="muted">${fmtPx(t.px)}</span>`;
  el.prepend(row);
  while (el.children.length > 60) el.removeChild(el.lastChild);
}

function drawCandles() {
  const canvas = $("#candle-chart");
  if (!canvas || !state.candles.length) return;
  const ctx = canvas.getContext("2d");
  const dpr = devicePixelRatio || 1;
  const cssW = canvas.clientWidth || canvas.parentElement.clientWidth;
  const cssH = canvas.clientHeight || 280;
  canvas.width = Math.floor(cssW * dpr);
  canvas.height = Math.floor(cssH * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  const data = state.candles.slice(-80);
  const pad = { t: 12, r: 12, b: 20, l: 52 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;
  let lo = Math.min(...data.map((c) => c.l));
  let hi = Math.max(...data.map((c) => c.h));
  if (lo === hi) {
    lo *= 0.99;
    hi *= 1.01;
  }
  const span = hi - lo || 1;
  const slot = w / data.length;
  const yAt = (v) => pad.t + (1 - (v - lo) / span) * h;
  ctx.strokeStyle = "rgba(255,255,255,0.05)";
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (h * i) / 4;
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(pad.l + w, y);
    ctx.stroke();
    ctx.fillStyle = "rgba(210,220,255,0.35)";
    ctx.font = "10px JetBrains Mono,monospace";
    ctx.textAlign = "right";
    ctx.fillText(fmtPx(hi - (span * i) / 4), pad.l - 6, y + 3);
  }
  data.forEach((c, i) => {
    const x = pad.l + i * slot + slot / 2;
    const up = c.c >= c.o;
    const col = up ? "rgba(77,255,181,0.9)" : "rgba(255,109,128,0.9)";
    ctx.strokeStyle = col;
    ctx.beginPath();
    ctx.moveTo(x, yAt(c.h));
    ctx.lineTo(x, yAt(c.l));
    ctx.stroke();
    const y1 = yAt(c.o);
    const y2 = yAt(c.c);
    const top = Math.min(y1, y2);
    const bh = Math.max(1, Math.abs(y2 - y1));
    ctx.fillStyle = col;
    ctx.fillRect(x - slot * 0.3, top, slot * 0.6, bh);
  });
}

async function setLiveCoin(coin, interval) {
  coin = (coin || state.liveCoin || "BTC").trim();
  interval = interval || $("#live-interval").value || "15m";
  state.liveCoin = coin;
  $("#live-coin").value = coin;
  $("#book-title").textContent = `Order book · ${coin}`;
  $("#candle-title").textContent = `${coin} · ${interval}`;
  $("#trade-tape").innerHTML = "";
  try {
    await fetch("/api/live/coin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ coin, interval }),
    });
    $("#live-status").textContent = `subscribed ${coin}`;
  } catch (e) {
    toast(e.message, true);
  }
  renderWatchlist();
}

function toggleWatch(coin) {
  if (!coin) return;
  const i = state.watchlist.indexOf(coin);
  if (i >= 0) state.watchlist.splice(i, 1);
  else state.watchlist.push(coin);
  LS.set("hl_watchlist", state.watchlist);
  renderWatchlist();
}

function renderWatchlist() {
  $("#watchlist").innerHTML = state.watchlist
    .map(
      (c) =>
        `<button type="button" class="wl-chip ${c === state.liveCoin ? "active" : ""}" data-coin="${escapeHtml(c)}">${escapeHtml(c)}</button>`
    )
    .join("");
  $$("#watchlist .wl-chip").forEach((b) =>
    b.addEventListener("click", () => {
      setLiveCoin(b.dataset.coin);
    })
  );
}

// ── Analytics ─────────────────────────────────────────────────────────────

function renderAnalytics() {
  const d = state.data;
  if (!d) {
    $("#an-empty").hidden = false;
    $("#an-body").hidden = true;
    return;
  }
  $("#an-empty").hidden = true;
  $("#an-body").hidden = false;
  const a = d.analytics || {};
  const hold =
    a.avg_hold_ms != null ? (a.avg_hold_ms / 3600000).toFixed(2) + "h" : "—";
  $("#an-kpis").innerHTML = [
    ["Realized PnL", fmtUsd(a.realized_pnl, { signed: true }), clsPnL(a.realized_pnl)],
    ["Fees", fmtUsd(a.fees), ""],
    ["Net", fmtUsd(a.net_pnl, { signed: true }), clsPnL(a.net_pnl)],
    ["Winrate", a.winrate != null ? fmtPct(a.winrate) : "—", ""],
    ["Volume", fmtUsd(a.volume), ""],
    ["Fills", String(a.n_fills || 0), ""],
    ["Avg hold", hold, ""],
    ["W / L", `${a.n_winning || 0} / ${a.n_losing || 0}`, ""],
  ]
    .map(
      ([l, v, c]) =>
        `<article class="card glass stat-card"><div class="sl">${l}</div><div class="sv mono ${c}" style="font-size:18px">${escapeHtml(v)}</div></article>`
    )
    .join("");

  const best = a.best_trade;
  const worst = a.worst_trade;
  $("#an-best-worst").innerHTML = `
    <div class="bw-card">
      <div class="lab">Best</div>
      <div class="val pos">${best ? fmtUsd(best.closed_pnl, { signed: true }) : "—"}</div>
      <div class="meta">${best ? `${escapeHtml(best.coin)} · ${escapeHtml(best.dir || "")} · ${fmtTs(best.time)}` : ""}</div>
    </div>
    <div class="bw-card">
      <div class="lab">Worst</div>
      <div class="val neg">${worst ? fmtUsd(worst.closed_pnl, { signed: true }) : "—"}</div>
      <div class="meta">${worst ? `${escapeHtml(worst.coin)} · ${escapeHtml(worst.dir || "")} · ${fmtTs(worst.time)}` : ""}</div>
    </div>`;

  const fees = d.user_fees;
  $("#fees-block").textContent = fees
    ? JSON.stringify(
        {
          userAddRate: fees.userAddRate,
          userCrossRate: fees.userCrossRate,
          activeReferralDiscount: fees.activeReferralDiscount,
          feeSchedule: fees.feeSchedule,
          dailyUserVlm: (fees.dailyUserVlm || []).slice(-5),
        },
        null,
        2
      )
    : "No fee data";
}

async function runRisk() {
  const address = state.data?.address || $("#address").value.trim();
  if (!address) return toast("Load wallet first", true);
  const move = fnum($("#risk-range").value);
  try {
    const res = await api("/api/risk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address, price_move_pct: move }),
    });
    $("#risk-result").innerHTML = `
      <div class="equity-summary">
        <span class="chip">Move <strong>${fmtPct(move, true)}</strong></span>
        <span class="chip ${clsPnL(res.total_pnl_impact)}">Impact <strong>${fmtUsd(res.total_pnl_impact, { signed: true })}</strong></span>
        <span class="chip">New AV <strong>${fmtUsd(res.new_account_value)}</strong></span>
        <span class="chip ${clsPnL(res.pct_of_equity)}">% equity <strong>${fmtPct(res.pct_of_equity, true)}</strong></span>
      </div>`;
    $("#risk-pos-table tbody").innerHTML = (res.positions || [])
      .slice(0, 25)
      .map(
        (p) => `<tr>
        <td><strong>${escapeHtml(p.coin)}</strong></td>
        <td><span class="side ${p.side}">${p.side}</span></td>
        <td class="num ${clsPnL(p.pnl_impact)}">${fmtUsd(p.pnl_impact, { signed: true })}</td>
        <td class="num">${fmtPct(p.new_dist_to_liq_pct)}</td></tr>`
      )
      .join("");
  } catch (e) {
    toast(e.message, true);
  }
}

// ── Tools ─────────────────────────────────────────────────────────────────

function renderAlerts() {
  $("#alert-list").innerHTML = state.alerts.length
    ? state.alerts
        .map(
          (a, i) =>
            `<li>${escapeHtml(a.type)} ≥ ${escapeHtml(String(a.value))} ${a.coin ? "· " + escapeHtml(a.coin) : ""}
          <button class="btn sm" data-i="${i}" style="height:26px;margin-top:6px">Remove</button></li>`
        )
        .join("")
    : `<li class="empty">No alerts</li>`;
  $$("#alert-list [data-i]").forEach((b) =>
    b.addEventListener("click", () => {
      state.alerts.splice(+b.dataset.i, 1);
      LS.set("hl_alerts", state.alerts);
      renderAlerts();
    })
  );
}

// ── Boot ──────────────────────────────────────────────────────────────────

async function boot() {
  // tabs
  $$(".tab").forEach((t) => t.addEventListener("click", () => setTab(t.dataset.tab)));

  const health = await api("/api/health").catch(() => null);
  if (health?.network) $("#network-pill").innerHTML = `<i class="pulse"></i>${escapeHtml(health.network)}`;
  if (health?.live?.whale_usd) $("#whale-thr").textContent = `≥ $${fmtUsd(health.live.whale_usd)}`;

  const params = new URLSearchParams(location.search);
  const tab = params.get("tab") || "portfolio";
  setTab(tab);

  const initial = params.get("address") || health?.default_address || "";
  if (initial) {
    $("#address").value = initial;
    load(initial);
  }

  $("#search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    load($("#address").value);
  });
  $("#refresh-btn").addEventListener("click", () => load($("#address").value || state.data?.address, { refresh: true }));
  $("#period")?.addEventListener("change", () => {
    state.period = $("#period").value;
    if (state.data) renderEquity(state.data);
  });
  $("#export-pos")?.addEventListener("click", () => {
    const rows = [["coin", "side", "size", "entry", "mark", "uPnL", "ROE", "liq", "dist", "fundOpen", "fundAll"]];
    for (const p of state.data?.positions || []) {
      rows.push([
        p.coin,
        p.side,
        p.size,
        p.entry_px,
        p.mark_px,
        p.u_pnl,
        p.roe_pct,
        p.liquidation_px,
        p.dist_to_liq_pct,
        p.funding_since_open,
        p.funding_all_time,
      ]);
    }
    downloadCsv("positions.csv", rows);
  });

  // markets
  $("#mkt-refresh")?.addEventListener("click", loadMarkets);
  $("#mkt-filter")?.addEventListener("input", renderMarketTable);
  $("#mkt-sort")?.addEventListener("change", renderMarketTable);
  $("#mkt-export")?.addEventListener("click", () => {
    const rows = [["coin", "mark", "change24h", "funding_pct", "apr", "oi_usd", "vol24h"]];
    for (const r of state.market?.markets || []) {
      rows.push([r.coin, r.mark, r.change_24h_pct, r.funding_pct, r.funding_apr_pct, r.open_interest_usd, r.volume_24h]);
    }
    downloadCsv("markets.csv", rows);
  });
  $("#pred-refresh")?.addEventListener("click", loadPredicted);
  $("#fh-load")?.addEventListener("click", loadFundingHistory);

  // live
  $("#live-set-coin")?.addEventListener("click", () => setLiveCoin($("#live-coin").value, $("#live-interval").value));
  $("#wl-add")?.addEventListener("click", () => {
    toggleWatch($("#live-coin").value.trim().toUpperCase());
  });
  $("#whale-only")?.addEventListener("change", (e) => {
    state.whaleOnly = e.target.checked;
  });
  renderWatchlist();
  connectStream();

  // analytics
  $("#risk-range")?.addEventListener("input", () => {
    $("#risk-range-label").textContent = $("#risk-range").value + "%";
  });
  $("#risk-run")?.addEventListener("click", runRisk);

  // tools
  renderAlerts();
  $("#alert-add")?.addEventListener("click", () => {
    state.alerts.push({
      type: $("#alert-type").value,
      value: fnum($("#alert-val").value),
      coin: ($("#alert-coin").value || "").trim().toUpperCase() || null,
    });
    LS.set("hl_alerts", state.alerts);
    renderAlerts();
    toast("Alert saved");
  });
  $("#compare-run")?.addEventListener("click", async () => {
    const addresses = $("#compare-addrs")
      .value.split(/\n|,/)
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      const res = await api("/api/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ addresses }),
      });
      $("#compare-table tbody").innerHTML = (res.wallets || [])
        .map(
          (w) => `<tr>
          <td class="mono">${escapeHtml(shortAddr(w.address))}</td>
          <td class="num">${fmtUsd(w.account_value)}</td>
          <td class="num ${clsPnL(w.total_u_pnl)}">${fmtUsd(w.total_u_pnl, { signed: true })}</td>
          <td class="num">${fmtPct(w.margin_used_pct)}</td>
          <td class="num">${fnum(w.leverage_effective).toFixed(2)}x</td>
          <td><span class="risk-badge ${w.risk_level}">${escapeHtml(w.risk_level || "")}</span></td></tr>`
        )
        .join("");
    } catch (e) {
      toast(e.message, true);
    }
  });
  $("#vault-load")?.addEventListener("click", async () => {
    const a = $("#vault-addr").value.trim();
    try {
      const v = await api(`/api/vault?address=${encodeURIComponent(a)}`);
      // trim huge portfolio arrays for display
      const slim = { ...v };
      if (Array.isArray(slim.portfolio)) {
        slim.portfolio = slim.portfolio.map((p) => {
          if (Array.isArray(p) && p[1]?.accountValueHistory) {
            return [p[0], { ...p[1], accountValueHistory: `…${p[1].accountValueHistory.length} pts`, pnlHistory: `…` }];
          }
          return p;
        });
      }
      $("#vault-block").textContent = JSON.stringify(slim, null, 2);
    } catch (e) {
      toast(e.message, true);
    }
  });

  window.addEventListener("resize", () => {
    if (state.data) renderEquity(state.data);
    drawCandles();
  });
}

boot();
