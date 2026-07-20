/* Hyperliquid Liquid Glass dashboard UI — visual v2 */

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

const state = {
  data: null,
  period: "week",
  loading: false,
  chart: null, // last drawn history for hover
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
  if (compact && abs >= 1e6) body = (n / 1e6).toFixed(2) + "M";
  else if (compact && abs >= 1e4) body = n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  else if (abs >= 100) body = n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  else body = n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  if (signed && n > 0) return "+" + body;
  return body;
}

function fmtPct(x, signed = false) {
  if (x === null || x === undefined || Number.isNaN(Number(x))) return "—";
  const n = fnum(x);
  const s = n.toFixed(2) + "%";
  return signed && n > 0 ? "+" + s : s;
}

function fmtPx(x) {
  if (x === null || x === undefined) return "—";
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
  return new Date(ms).toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
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
  if (dist === null || dist === undefined) return { t: "n/a", c: "na" };
  if (dist < 5) return { t: "CRIT", c: "crit" };
  if (dist < 15) return { t: "HIGH", c: "high" };
  if (dist < 30) return { t: "MED", c: "med" };
  return { t: "OK", c: "ok" };
}

function bufferWidth(dist) {
  // map 0..100%+ distance into a bar fill (capped)
  if (dist == null || Number.isNaN(dist)) return 0;
  if (dist < 0) return 2;
  return Math.max(4, Math.min(100, (Math.log10(dist + 1) / Math.log10(101)) * 100));
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
    setTimeout(() => {
      el.hidden = true;
    }, 250);
  }, 3200);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── API ───────────────────────────────────────────────────────────────────

async function fetchAccount(address, { refresh = false } = {}) {
  const q = new URLSearchParams({ address });
  if (refresh) q.set("refresh", "1");
  const res = await fetch(`/api/account?${q}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function fetchHealth() {
  try {
    const res = await fetch("/api/health");
    return await res.json();
  } catch {
    return null;
  }
}

// ── meters / loading ──────────────────────────────────────────────────────

function setMeter(el, pct, { invert = false } = {}) {
  if (!el) return;
  const v = Math.max(0, Math.min(100, fnum(pct)));
  const fill = $("i", el) || el.querySelector("i");
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

  const sk = $("#skeleton");
  const empty = $("#empty");
  const dash = $("#dashboard");
  if (on && !state.data) {
    empty.hidden = true;
    dash.hidden = true;
    sk.hidden = false;
  }
  if (!on) sk.hidden = true;
}

// ── render ────────────────────────────────────────────────────────────────

function renderOverview(d) {
  $("#kpi-av").textContent = fmtUsd(d.account_value);
  $("#kpi-av").className = "kpi-value mono";
  $("#kpi-addr").textContent = shortAddr(d.address);
  $("#kpi-addr").title = d.address;

  $("#kpi-wd").textContent = fmtUsd(d.withdrawable);
  $("#kpi-free").textContent = `free ${fmtPct(d.risk?.free_margin_pct)} of equity`;
  setMeter($("#free-meter"), d.risk?.free_margin_pct, { invert: true });

  $("#kpi-mu").textContent = fmtUsd(d.margin_used);
  $("#kpi-mu-pct").textContent = `${fmtPct(d.margin_used_pct)} · notional ${fmtUsd(d.total_notional)}`;
  setMeter($("#margin-meter"), d.margin_used_pct);

  const up = fnum(d.total_u_pnl);
  const upEl = $("#kpi-upnl");
  upEl.textContent = fmtUsd(up, { signed: true });
  upEl.className = "kpi-value mono " + clsPnL(up);

  const modes = [];
  if (d.n_cross) modes.push(`${d.n_cross} cross`);
  if (d.n_isolated) modes.push(`${d.n_isolated} isolated`);
  $("#kpi-mode").textContent = modes.length
    ? modes.join(" · ") + ` · lev ${fnum(d.risk?.leverage_effective).toFixed(2)}x`
    : "flat · no positions";
}

function renderRisk(d) {
  const r = d.risk || {};
  const level = r.risk_level || "FLAT";
  const badge = $("#risk-level");
  badge.textContent = level;
  badge.className = "risk-badge " + level;

  const score = Math.max(0, Math.min(100, fnum(r.risk_score)));
  $("#risk-score").textContent = String(Math.round(score));

  const circ = 2 * Math.PI * 52;
  const fg = $("#ring-fg");
  fg.style.strokeDasharray = String(circ);
  // re-trigger transition
  fg.style.strokeDashoffset = String(circ);
  requestAnimationFrame(() => {
    fg.style.strokeDashoffset = String(circ * (1 - score / 100));
  });

  if (score >= 75) {
    fg.style.stroke = "var(--red)";
    fg.style.filter = "drop-shadow(0 0 6px rgba(255,109,128,0.45))";
  } else if (score >= 45) {
    fg.style.stroke = "var(--amber)";
    fg.style.filter = "drop-shadow(0 0 6px rgba(255,200,87,0.35))";
  } else {
    fg.style.stroke = "url(#ringGrad)";
    fg.style.filter = "drop-shadow(0 0 6px rgba(94,239,200,0.35))";
  }

  $("#risk-lev").textContent = fnum(r.leverage_effective).toFixed(2) + "x";
  $("#risk-mu").textContent = fmtPct(r.margin_used_pct);
  $("#risk-free").textContent = fmtPct(r.free_margin_pct);

  if (r.min_dist_to_liq_pct != null) {
    $("#risk-closest").textContent = `${r.min_dist_coin || "?"} · ${fmtPct(r.min_dist_to_liq_pct)}`;
  } else {
    $("#risk-closest").textContent = "—";
  }

  $("#risk-notes").innerHTML = (r.notes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join("");

  const ranked = [...(d.positions || [])]
    .sort((a, b) => {
      const da = a.dist_to_liq_pct == null ? 1e18 : a.dist_to_liq_pct;
      const db = b.dist_to_liq_pct == null ? 1e18 : b.dist_to_liq_pct;
      return da - db;
    })
    .slice(0, 15);

  const tbody = $("#risk-table tbody");
  tbody.innerHTML = ranked.length
    ? ranked
        .map((p) => {
          const tag = riskTag(p.dist_to_liq_pct);
          const bw = bufferWidth(p.dist_to_liq_pct);
          return `<tr>
          <td><strong>${escapeHtml(p.coin)}</strong></td>
          <td><span class="side ${p.side}">${p.side}</span></td>
          <td class="num">${fmtPct(p.dist_to_liq_pct)} <span class="tag ${tag.c}">${tag.t}</span></td>
          <td class="num"><div class="buf"><span class="buf-track"><i style="width:${bw}%"></i></span></div></td>
          <td class="num">${fmtPx(p.liquidation_px)}</td>
          <td class="num">${fmtPx(p.mark_px)}</td>
          <td class="num ${clsPnL(p.u_pnl)}">${fmtUsd(p.u_pnl, { signed: true })}</td>
        </tr>`;
        })
        .join("")
    : `<tr><td colspan="7" style="color:var(--text-mute);text-align:center;padding:24px">No open positions</td></tr>`;
}

function renderEquity(d) {
  const equity = d.equity || {};
  const periods = Object.keys(equity);
  const grid = $("#period-grid");
  const preferred = ["day", "week", "month", "allTime", "perpDay", "perpWeek", "perpMonth", "perpAllTime"];
  const ordered = [
    ...preferred.filter((k) => equity[k]),
    ...periods.filter((k) => !preferred.includes(k)),
  ];

  grid.innerHTML = ordered
    .map((k) => {
      const ep = equity[k];
      const pnl = fnum(ep.pnl);
      const active = k === state.period ? "active" : "";
      return `<div class="period-chip ${active}" data-period="${escapeHtml(k)}">
        <div class="p-name">${escapeHtml(k)}</div>
        <div class="p-pnl ${clsPnL(pnl)}">${fmtUsd(pnl, { signed: true })}</div>
      </div>`;
    })
    .join("");

  $$(".period-chip", grid).forEach((el) => {
    el.addEventListener("click", () => {
      state.period = el.dataset.period;
      const opts = { day: 1, week: 1, month: 1, allTime: 1, perpWeek: 1, perpAllTime: 1 };
      if (state.period in opts) $("#period").value = state.period;
      renderEquity(d);
    });
  });

  let key = state.period;
  if (!equity[key] || !(equity[key].history || []).length) {
    key = ordered.find((k) => (equity[k].history || []).length) || ordered[0];
  }
  const ep = equity[key];
  const hist = (ep && ep.history) || [];
  const summary = $("#equity-summary");

  if (ep && hist.length) {
    const start = hist[0][1];
    const end = hist[hist.length - 1][1];
    const delta = end - start;
    summary.innerHTML = `
      <span class="chip">Period <strong>${escapeHtml(ep.period)}</strong></span>
      <span class="chip">${fmtTs(hist[0][0])} → ${fmtTs(hist[hist.length - 1][0])}</span>
      <span class="chip">${fmtUsd(start)} → <strong>${fmtUsd(end)}</strong></span>
      <span class="chip ${clsPnL(delta)}">Δ <strong>${fmtUsd(delta, { signed: true })}</strong></span>
      <span class="chip">PnL <strong class="${clsPnL(ep.pnl)}">${fmtUsd(ep.pnl, { signed: true })}</strong></span>
      <span class="chip">Vol <strong>${fmtUsd(ep.volume)}</strong></span>`;
    drawChart(hist);
  } else {
    summary.innerHTML = `<span class="chip" style="color:var(--text-mute)">No equity history</span>`;
    drawChart([]);
  }
}

function smoothPath(ctx, points) {
  if (points.length < 2) return;
  ctx.moveTo(points[0].x, points[0].y);
  if (points.length === 2) {
    ctx.lineTo(points[1].x, points[1].y);
    return;
  }
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i === 0 ? i : i - 1];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] || p2;
    const cp1x = p1.x + (p2.x - p0.x) / 6;
    const cp1y = p1.y + (p2.y - p0.y) / 6;
    const cp2x = p2.x - (p3.x - p1.x) / 6;
    const cp2y = p2.y - (p3.y - p1.y) / 6;
    ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p2.x, p2.y);
  }
}

function drawChart(history) {
  const canvas = $("#equity-chart");
  const tip = $("#chart-tip");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || canvas.parentElement.clientWidth;
  const cssH = canvas.clientHeight || 240;
  canvas.width = Math.floor(cssW * dpr);
  canvas.height = Math.floor(cssH * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  state.chart = null;
  if (tip) tip.hidden = true;

  if (!history.length) {
    ctx.fillStyle = "rgba(255,255,255,0.35)";
    ctx.font = "500 13px Inter, sans-serif";
    ctx.fillText("No data", 20, cssH / 2);
    return;
  }

  const pad = { t: 18, r: 14, b: 30, l: 58 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;
  const vals = history.map((p) => p[1]);
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  if (lo === hi) {
    lo *= 0.995;
    hi *= 1.005;
  }
  // pad range slightly
  const mid = (lo + hi) / 2;
  const half = ((hi - lo) / 2) * 1.08 || 1;
  lo = mid - half;
  hi = mid + half;
  const span = hi - lo || 1;

  const xAt = (i) => pad.l + (i / Math.max(1, history.length - 1)) * w;
  const yAt = (v) => pad.t + (1 - (v - lo) / span) * h;
  const points = history.map((p, i) => ({ x: xAt(i), y: yAt(p[1]), t: p[0], v: p[1] }));

  // vertical soft grid
  ctx.strokeStyle = "rgba(255,255,255,0.04)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const x = pad.l + (w * i) / 4;
    ctx.beginPath();
    ctx.moveTo(x, pad.t);
    ctx.lineTo(x, pad.t + h);
    ctx.stroke();
  }

  // horizontal grid + labels
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (h * i) / 4;
    ctx.strokeStyle = "rgba(255,255,255,0.055)";
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(pad.l + w, y);
    ctx.stroke();
    const val = hi - (span * i) / 4;
    ctx.fillStyle = "rgba(210,220,255,0.38)";
    ctx.font = "500 10px JetBrains Mono, monospace";
    ctx.textAlign = "right";
    ctx.fillText(fmtUsd(val, { compact: true }), pad.l - 8, y + 3);
  }

  const up = vals[vals.length - 1] >= vals[0];
  const c1 = up ? "rgba(77,255,181,0.95)" : "rgba(255,109,128,0.95)";
  const c0 = up ? "rgba(108,182,255,0.9)" : "rgba(255,160,120,0.85)";

  // area
  const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + h);
  grad.addColorStop(0, up ? "rgba(77,255,181,0.28)" : "rgba(255,109,128,0.26)");
  grad.addColorStop(0.55, up ? "rgba(108,182,255,0.08)" : "rgba(255,109,128,0.06)");
  grad.addColorStop(1, "rgba(0,0,0,0)");

  ctx.beginPath();
  smoothPath(ctx, points);
  ctx.lineTo(points[points.length - 1].x, pad.t + h);
  ctx.lineTo(points[0].x, pad.t + h);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // line stroke with gradient
  const lg = ctx.createLinearGradient(pad.l, 0, pad.l + w, 0);
  lg.addColorStop(0, c0);
  lg.addColorStop(1, c1);
  ctx.beginPath();
  smoothPath(ctx, points);
  ctx.strokeStyle = lg;
  ctx.lineWidth = 2.4;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.stroke();

  // outer glow pass
  ctx.save();
  ctx.globalAlpha = 0.35;
  ctx.shadowColor = c1;
  ctx.shadowBlur = 12;
  ctx.beginPath();
  smoothPath(ctx, points);
  ctx.strokeStyle = c1;
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.restore();

  // end point
  const last = points[points.length - 1];
  const g2 = ctx.createRadialGradient(last.x, last.y, 0, last.x, last.y, 16);
  g2.addColorStop(0, c1);
  g2.addColorStop(1, "transparent");
  ctx.fillStyle = g2;
  ctx.beginPath();
  ctx.arc(last.x, last.y, 16, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.beginPath();
  ctx.arc(last.x, last.y, 3.4, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = c1;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(last.x, last.y, 5.5, 0, Math.PI * 2);
  ctx.stroke();

  // x labels
  ctx.fillStyle = "rgba(210,220,255,0.38)";
  ctx.font = "500 10px Inter, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(fmtTs(history[0][0]), pad.l, cssH - 10);
  ctx.textAlign = "right";
  ctx.fillText(fmtTs(history[history.length - 1][0]), pad.l + w, cssH - 10);

  state.chart = { points, pad, cssW, cssH, history };

  // hover
  canvas.onmousemove = (e) => {
    if (!state.chart) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const { points: pts } = state.chart;
    let best = 0;
    let bestD = Infinity;
    pts.forEach((p, i) => {
      const d = Math.abs(p.x - mx);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    });
    const p = pts[best];
    // redraw with crosshair — lightweight: just tip
    if (tip) {
      tip.hidden = false;
      tip.textContent = `${fmtTs(p.t)}  ·  ${fmtUsd(p.v)}`;
      const left = Math.min(Math.max(p.x, 60), cssW - 60);
      tip.style.left = left + "px";
      tip.style.top = Math.max(28, p.y) + "px";
    }
  };
  canvas.onmouseleave = () => {
    if (tip) tip.hidden = true;
  };
}

function renderPositions(d) {
  const rows = d.positions || [];
  $("#pos-count").textContent = `(${rows.length})`;
  const tbody = $("#pos-table tbody");
  tbody.innerHTML = rows.length
    ? rows
        .map(
          (p) => `<tr>
      <td><strong>${escapeHtml(p.coin)}</strong></td>
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
    : `<tr><td colspan="12" style="color:var(--text-mute);text-align:center;padding:28px">No positions</td></tr>`;
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
      <td style="color:var(--text-dim)">${escapeHtml(o.order_type || "")}${o.reduce_only ? " · RO" : ""}${o.is_tpsl ? " · TP/SL" : ""}</td>
      <td style="color:var(--text-mute)">${fmtTs(o.timestamp)}</td>
    </tr>`
        )
        .join("")
    : `<tr><td colspan="6" style="color:var(--text-mute);text-align:center;padding:24px">None</td></tr>`;

  $("#ho-table tbody").innerHTML = ho.length
    ? ho
        .map(
          (o) => `<tr>
      <td><strong>${escapeHtml(o.coin)}</strong></td>
      <td><span class="side ${o.side}">${o.side}</span></td>
      <td class="num">${fmtSz(o.orig_sz || o.size)}</td>
      <td class="num">${fmtPx(o.limit_px)}</td>
      <td style="color:var(--text-dim)">${escapeHtml(o.status)}</td>
      <td style="color:var(--text-mute)">${fmtTs(o.status_timestamp || o.timestamp)}</td>
    </tr>`
        )
        .join("")
    : `<tr><td colspan="6" style="color:var(--text-mute);text-align:center;padding:24px">None</td></tr>`;
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
      <td class="num">${r.n_fills}</td>
    </tr>`
        )
        .join("")
    : `<tr><td colspan="6" style="color:var(--text-mute);text-align:center;padding:24px">No fills</td></tr>`;
}

function renderSubsVaults(d) {
  const subs = d.sub_accounts || [];
  const vaults = d.vault_equities || [];

  $("#subs-list").innerHTML = subs.length
    ? subs
        .map((s, i) => {
          const name = s.name || s.subAccountUser || s.address || `sub-${i}`;
          const user = s.subAccountUser || s.address || "";
          const ch = s.clearinghouseState || s.clearinghouse || {};
          const ms = ch.marginSummary || {};
          const av = ms.accountValue ?? s.accountValue;
          return `<li>
            <span style="color:var(--text-dim)">${escapeHtml(String(name))}</span>
            <div style="color:var(--text-mute);font-size:11px;margin-top:3px">${escapeHtml(shortAddr(String(user)))}</div>
            ${av != null ? `<strong>${fmtUsd(av)}</strong>` : ""}
          </li>`;
        })
        .join("")
    : `<li class="empty">No subaccounts</li>`;

  $("#vaults-list").innerHTML = vaults.length
    ? vaults
        .map((v) => {
          const addr = v.vaultAddress || v.vault || "?";
          return `<li>
            <span style="color:var(--text-mute);font-size:11px">${escapeHtml(shortAddr(String(addr)))}</span>
            <strong>${fmtUsd(v.equity)}</strong>
          </li>`;
        })
        .join("")
    : `<li class="empty">No vault equities</li>`;
}

function renderAll(d) {
  state.data = d;
  $("#empty").hidden = true;
  $("#skeleton").hidden = true;
  $("#dashboard").hidden = false;

  // re-trigger reveal animations
  $$("#dashboard .reveal").forEach((el) => {
    el.style.animation = "none";
    // force reflow
    void el.offsetWidth;
    el.style.animation = "";
  });

  $("#network-pill").innerHTML = `<i class="pulse"></i>${escapeHtml(d.network || "mainnet")}`;
  $("#status-pill").textContent = "live";
  if (d.fetched_at) {
    $("#fetched-at").textContent = "Updated " + fmtTs(d.fetched_at);
  }
  renderOverview(d);
  renderRisk(d);
  renderEquity(d);
  renderPositions(d);
  renderOrders(d);
  renderPnl(d);
  renderSubsVaults(d);
}

// ── load flow ─────────────────────────────────────────────────────────────

async function load(address, { refresh = false } = {}) {
  address = (address || "").trim();
  if (!address) {
    toast("Enter an address", true);
    return;
  }
  setLoading(true);
  try {
    const data = await fetchAccount(address, { refresh });
    state.period = $("#period").value || "week";
    renderAll(data);
    const url = new URL(window.location.href);
    url.searchParams.set("address", address);
    history.replaceState(null, "", url);
  } catch (err) {
    console.error(err);
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

// ── boot ──────────────────────────────────────────────────────────────────

async function boot() {
  const health = await fetchHealth();
  if (health?.network) {
    $("#network-pill").innerHTML = `<i class="pulse"></i>${escapeHtml(health.network)}`;
  }

  const params = new URLSearchParams(location.search);
  const fromUrl = params.get("address");
  const defaultAddr = health?.default_address || "";
  const initial = fromUrl || defaultAddr;
  if (initial) {
    $("#address").value = initial;
    load(initial);
  }

  $("#search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    load($("#address").value);
  });

  $("#refresh-btn").addEventListener("click", () => {
    const btn = $("#refresh-btn");
    btn.style.transform = "rotate(360deg)";
    btn.style.transition = "transform 0.5s ease";
    setTimeout(() => {
      btn.style.transform = "";
    }, 500);
    load($("#address").value || state.data?.address, { refresh: true });
  });

  $("#period").addEventListener("change", () => {
    state.period = $("#period").value;
    if (state.data) renderEquity(state.data);
  });

  let resizeT;
  window.addEventListener("resize", () => {
    clearTimeout(resizeT);
    resizeT = setTimeout(() => {
      if (state.data) renderEquity(state.data);
    }, 100);
  });
}

boot();
