#!/usr/bin/env python3
"""Hyperliquid account dashboard — overview, positions, risk, equity, PnL.

Read-only: only needs a public address (no private key).

Usage:
  python examples/account_dashboard.py --address 0x...
  python examples/account_dashboard.py                    # from examples/config.json
  python examples/account_dashboard.py --testnet
  python examples/account_dashboard.py --period week
  python examples/account_dashboard.py --json
  python examples/account_dashboard.py --section risk
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Allow running as `python examples/account_dashboard.py` from repo root or examples/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hyperliquid.info import Info
from hyperliquid.utils import constants

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIDE_BUY = "B"
SIDE_SELL = "A"


def f(x: Any, default: float = 0.0) -> float:
    if x is None or x == "":
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def fmt_usd(x: float, signed: bool = False, width: int = 0) -> str:
    if abs(x) >= 1_000_000:
        s = f"{x/1_000_000:+,.2f}M" if signed else f"{x/1_000_000:,.2f}M"
    elif abs(x) >= 10_000:
        s = f"{x:+,.0f}" if signed else f"{x:,.0f}"
    elif abs(x) >= 100:
        s = f"{x:+,.2f}" if signed else f"{x:,.2f}"
    else:
        s = f"{x:+,.4f}" if signed else f"{x:,.4f}"
    return s.rjust(width) if width else s


def fmt_pct(x: Optional[float], signed: bool = False, width: int = 0) -> str:
    if x is None:
        s = "—"
    else:
        s = f"{x:+.2f}%" if signed else f"{x:.2f}%"
    return s.rjust(width) if width else s


def fmt_px(x: Optional[float]) -> str:
    if x is None:
        return "—"
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:,.4f}"
    return f"{x:.6g}"


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def ms_ago(days: float) -> int:
    return int((time.time() - days * 86400) * 1000)


def color(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def green(t: str, on: bool) -> str:
    return color(t, "32", on)


def red(t: str, on: bool) -> str:
    return color(t, "31", on)


def yellow(t: str, on: bool) -> str:
    return color(t, "33", on)


def bold(t: str, on: bool) -> str:
    return color(t, "1", on)


def cyan(t: str, on: bool) -> str:
    return color(t, "36", on)


def signed_color(val: float, text: str, on: bool) -> str:
    if val > 0:
        return green(text, on)
    if val < 0:
        return red(text, on)
    return text


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class PositionRow:
    coin: str
    side: str  # LONG / SHORT
    size: float
    entry_px: Optional[float]
    mark_px: Optional[float]
    u_pnl: float
    roe_pct: float
    leverage: int
    margin_mode: str  # cross / isolated
    margin_used: float
    position_value: float
    liquidation_px: Optional[float]
    dist_to_liq_pct: Optional[float]
    funding_all_time: float
    funding_since_open: float
    open_time_ms: Optional[int] = None


@dataclass
class OrderRow:
    coin: str
    side: str
    size: float
    limit_px: float
    order_type: str
    tif: str
    reduce_only: bool
    oid: int
    timestamp: int
    trigger_px: Optional[float] = None
    is_trigger: bool = False
    is_tpsl: bool = False


@dataclass
class HistOrderRow:
    coin: str
    side: str
    size: float
    orig_sz: float
    limit_px: float
    status: str
    order_type: str
    timestamp: int
    status_timestamp: int
    oid: int


@dataclass
class CoinPnl:
    coin: str
    closed_pnl: float
    volume: float
    fees: float
    n_fills: int
    realized_net: float  # closedPnl - fees (approx)


@dataclass
class EquityPeriod:
    period: str
    account_value_start: Optional[float]
    account_value_end: Optional[float]
    pnl: Optional[float]
    volume: Optional[float]
    history: List[Tuple[int, float]] = field(default_factory=list)  # (ts_ms, accountValue)
    pnl_history: List[Tuple[int, float]] = field(default_factory=list)


@dataclass
class RiskSnapshot:
    account_value: float
    withdrawable: float
    margin_used: float
    margin_used_pct: float
    free_margin_pct: float
    total_notional: float
    leverage_effective: float  # notional / equity
    total_u_pnl: float
    min_dist_to_liq_pct: Optional[float]
    min_dist_coin: Optional[str]
    risk_level: str  # LOW / MEDIUM / HIGH / CRITICAL / FLAT
    risk_score: int  # 0-100 (higher = riskier)
    notes: List[str] = field(default_factory=list)


@dataclass
class AccountSnapshot:
    address: str
    network: str
    account_value: float
    withdrawable: float
    margin_used: float
    margin_used_pct: float
    cross_account_value: float
    total_notional: float
    total_raw_usd: float
    total_u_pnl: float
    n_cross: int
    n_isolated: int
    positions: List[PositionRow]
    open_orders: List[OrderRow]
    historical_orders: List[HistOrderRow]
    equity: Dict[str, EquityPeriod]
    coin_pnl: List[CoinPnl]
    sub_accounts: List[Dict[str, Any]]
    vault_equities: List[Dict[str, Any]]
    risk: RiskSnapshot
    spot_balances: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def mark_prices_from_meta(info: Info) -> Dict[str, float]:
    """coin -> markPx from metaAndAssetCtxs + all_mids fallback."""
    marks: Dict[str, float] = {}
    try:
        meta, ctxs = info.meta_and_asset_ctxs()
        for asset_info, ctx in zip(meta["universe"], ctxs):
            name = asset_info["name"]
            px = ctx.get("markPx") or ctx.get("midPx") or ctx.get("oraclePx")
            if px is not None:
                marks[name] = f(px)
    except Exception:
        pass
    try:
        mids = info.all_mids()
        for coin, mid in mids.items():
            marks.setdefault(coin, f(mid))
    except Exception:
        pass
    return marks


def max_leverage_map(info: Info) -> Dict[str, int]:
    out: Dict[str, int] = {}
    try:
        meta = info.meta()
        for a in meta["universe"]:
            if "maxLeverage" in a:
                out[a["name"]] = int(a["maxLeverage"])
    except Exception:
        pass
    return out


def dist_to_liq_pct(size: float, mark: Optional[float], liq: Optional[float]) -> Optional[float]:
    """% price move to liquidation. Positive = buffer remaining."""
    if mark is None or liq is None or mark <= 0:
        return None
    if size > 0:  # long: liquidated if price falls to liq
        return (mark - liq) / mark * 100.0
    if size < 0:  # short: liquidated if price rises to liq
        return (liq - mark) / mark * 100.0
    return None


def estimate_position_open_times(fills: List[Dict[str, Any]], open_sizes: Dict[str, float]) -> Dict[str, int]:
    """Walk fills newest→oldest, undo until size hits ~0 → open timestamp per coin."""
    remaining = {c: s for c, s in open_sizes.items() if abs(s) > 1e-12}
    open_times: Dict[str, int] = {}
    # newest first
    sorted_fills = sorted(fills, key=lambda x: x.get("time", 0), reverse=True)
    for fill in sorted_fills:
        coin = fill.get("coin")
        if coin not in remaining:
            continue
        sz = f(fill.get("sz"))
        side = fill.get("side")
        # Buy increases szi, sell decreases
        signed = sz if side == SIDE_BUY else -sz
        # Undo this fill
        remaining[coin] -= signed
        open_times[coin] = int(fill.get("time", 0))
        if abs(remaining[coin]) < 1e-8:
            del remaining[coin]
        if not remaining:
            break
    return open_times


def aggregate_funding(
    funding_rows: List[Dict[str, Any]], open_times: Dict[str, int]
) -> Tuple[Dict[str, float], Dict[str, float]]:
    all_time: Dict[str, float] = defaultdict(float)
    since_open: Dict[str, float] = defaultdict(float)
    for row in funding_rows:
        delta = row.get("delta") or {}
        if delta.get("type") and delta.get("type") != "funding":
            continue
        coin = delta.get("coin")
        if not coin:
            continue
        usdc = f(delta.get("usdc"))
        all_time[coin] += usdc
        t = int(row.get("time", 0))
        ot = open_times.get(coin)
        if ot is None or t >= ot:
            since_open[coin] += usdc
    return dict(all_time), dict(since_open)


def pnl_by_coin(fills: List[Dict[str, Any]]) -> List[CoinPnl]:
    agg: Dict[str, Dict[str, float]] = defaultdict(lambda: {"closed": 0.0, "vol": 0.0, "fees": 0.0, "n": 0})
    for fill in fills:
        coin = fill.get("coin") or "?"
        a = agg[coin]
        a["closed"] += f(fill.get("closedPnl"))
        a["vol"] += f(fill.get("px")) * f(fill.get("sz"))
        a["fees"] += f(fill.get("fee"))
        a["n"] += 1
    rows = [
        CoinPnl(
            coin=c,
            closed_pnl=v["closed"],
            volume=v["vol"],
            fees=v["fees"],
            n_fills=int(v["n"]),
            realized_net=v["closed"] - v["fees"],
        )
        for c, v in agg.items()
    ]
    rows.sort(key=lambda r: abs(r.realized_net), reverse=True)
    return rows


def parse_portfolio(raw: Any) -> Dict[str, EquityPeriod]:
    periods: Dict[str, EquityPeriod] = {}
    if not isinstance(raw, list):
        return periods
    for item in raw:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            continue
        name, data = item
        if not isinstance(data, dict):
            continue
        av_hist = [(int(t), f(v)) for t, v in data.get("accountValueHistory") or []]
        pnl_hist = [(int(t), f(v)) for t, v in data.get("pnlHistory") or []]
        periods[str(name)] = EquityPeriod(
            period=str(name),
            account_value_start=av_hist[0][1] if av_hist else None,
            account_value_end=av_hist[-1][1] if av_hist else None,
            pnl=pnl_hist[-1][1] if pnl_hist else None,
            volume=f(data["vlm"]) if data.get("vlm") is not None else None,
            history=av_hist,
            pnl_history=pnl_hist,
        )
    return periods


def compute_risk(
    account_value: float,
    withdrawable: float,
    margin_used: float,
    total_notional: float,
    total_u_pnl: float,
    positions: List[PositionRow],
) -> RiskSnapshot:
    notes: List[str] = []
    if account_value <= 0:
        return RiskSnapshot(
            account_value=account_value,
            withdrawable=withdrawable,
            margin_used=margin_used,
            margin_used_pct=0.0,
            free_margin_pct=0.0,
            total_notional=total_notional,
            leverage_effective=0.0,
            total_u_pnl=total_u_pnl,
            min_dist_to_liq_pct=None,
            min_dist_coin=None,
            risk_level="FLAT",
            risk_score=0,
            notes=["No equity on this account."],
        )

    margin_used_pct = margin_used / account_value * 100.0
    free_margin_pct = withdrawable / account_value * 100.0
    lev = total_notional / account_value if account_value else 0.0

    with_dist = [(p.coin, p.dist_to_liq_pct) for p in positions if p.dist_to_liq_pct is not None]
    min_dist = None
    min_coin = None
    if with_dist:
        min_coin, min_dist = min(with_dist, key=lambda x: x[1] if x[1] is not None else 1e18)

    score = 0
    # Margin utilization (0-40)
    score += min(40, int(margin_used_pct * 0.45))
    # Effective leverage (0-25)
    score += min(25, int(lev * 3))
    # Distance to liquidation (0-35)
    if min_dist is None:
        if positions:
            notes.append("Some positions lack liquidationPx (common on light cross exposure).")
        score += 5
    elif min_dist < 0:
        score += 35
        notes.append(f"{min_coin}: already past computed liq buffer ({min_dist:.2f}%).")
    elif min_dist < 5:
        score += 35
        notes.append(f"{min_coin}: critically close to liquidation ({min_dist:.2f}%).")
    elif min_dist < 10:
        score += 28
        notes.append(f"{min_coin}: very close to liquidation ({min_dist:.2f}%).")
    elif min_dist < 20:
        score += 18
    elif min_dist < 40:
        score += 10
    else:
        score += 2

    if free_margin_pct < 10:
        notes.append(f"Low free margin ({free_margin_pct:.1f}% withdrawable).")
        score = min(100, score + 8)
    if total_u_pnl < 0 and abs(total_u_pnl) > account_value * 0.15:
        notes.append(f"Large unrealized loss ({total_u_pnl:+.2f} ≈ {abs(total_u_pnl)/account_value*100:.1f}% of equity).")

    score = max(0, min(100, score))
    if not positions:
        level = "FLAT"
        score = 0
    elif score >= 75 or (min_dist is not None and min_dist < 5) or margin_used_pct >= 90:
        level = "CRITICAL"
    elif score >= 55 or (min_dist is not None and min_dist < 15) or margin_used_pct >= 70:
        level = "HIGH"
    elif score >= 35 or (min_dist is not None and min_dist < 30) or margin_used_pct >= 50:
        level = "MEDIUM"
    else:
        level = "LOW"

    if min_dist is not None and min_coin:
        notes.insert(0, f"Closest to liq: {min_coin} at {min_dist:.2f}% price move.")
    notes.append(f"Effective leverage {lev:.2f}x · margin used {margin_used_pct:.1f}% · free {free_margin_pct:.1f}%.")

    return RiskSnapshot(
        account_value=account_value,
        withdrawable=withdrawable,
        margin_used=margin_used,
        margin_used_pct=margin_used_pct,
        free_margin_pct=free_margin_pct,
        total_notional=total_notional,
        leverage_effective=lev,
        total_u_pnl=total_u_pnl,
        min_dist_to_liq_pct=min_dist,
        min_dist_coin=min_coin,
        risk_level=level,
        risk_score=score,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def load_address_from_config() -> Optional[str]:
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if not os.path.exists(config_path):
        return None
    with open(config_path) as fh:
        cfg = json.load(fh)
    addr = (cfg.get("account_address") or "").strip()
    if addr:
        return addr
    # Derive from secret if present (optional dependency path)
    secret = (cfg.get("secret_key") or "").strip()
    if secret:
        try:
            import eth_account

            return eth_account.Account.from_key(secret).address
        except Exception:
            return None
    return None


def fetch_snapshot(info: Info, address: str, network: str, funding_lookback_days: float = 365.0) -> AccountSnapshot:
    user_state = info.user_state(address)
    marks = mark_prices_from_meta(info)

    margin = user_state.get("marginSummary") or {}
    cross = user_state.get("crossMarginSummary") or {}
    account_value = f(margin.get("accountValue"))
    withdrawable = f(user_state.get("withdrawable"))
    margin_used = f(margin.get("totalMarginUsed"))
    total_notional = f(margin.get("totalNtlPos"))
    total_raw = f(margin.get("totalRawUsd"))
    margin_used_pct = (margin_used / account_value * 100.0) if account_value else 0.0

    raw_positions = user_state.get("assetPositions") or []
    open_sizes: Dict[str, float] = {}
    position_basics: List[Dict[str, Any]] = []
    n_cross = n_isolated = 0
    total_u_pnl = 0.0
    has_cum_funding = False

    for ap in raw_positions:
        pos = ap.get("position") or {}
        szi = f(pos.get("szi"))
        if abs(szi) < 1e-12:
            continue
        coin = pos["coin"]
        open_sizes[coin] = szi
        entry = f(pos.get("entryPx")) if pos.get("entryPx") is not None else None
        mark = marks.get(coin)
        if mark is None and abs(szi) > 0:
            pv = f(pos.get("positionValue"))
            if pv:
                mark = pv / abs(szi)
        u_pnl = f(pos.get("unrealizedPnl"))
        total_u_pnl += u_pnl
        lev_info = pos.get("leverage") or {}
        mode = lev_info.get("type") or "cross"
        if mode == "isolated":
            n_isolated += 1
        else:
            n_cross += 1
        liq = f(pos.get("liquidationPx")) if pos.get("liquidationPx") is not None else None
        # Prefer native cumFunding on clearinghouse position (allTime / sinceOpen)
        cum = pos.get("cumFunding") or {}
        fund_all = fund_open = None
        if cum:
            has_cum_funding = True
            # API: positive cumFunding often means paid by user (cost). Keep raw sign from API.
            fund_all = f(cum.get("allTime"))
            fund_open = f(cum.get("sinceOpen"))
        position_basics.append(
            {
                "coin": coin,
                "szi": szi,
                "entry": entry,
                "mark": mark,
                "u_pnl": u_pnl,
                "roe": f(pos.get("returnOnEquity")) * 100.0,  # API is fraction
                "leverage": int(lev_info.get("value") or 0),
                "mode": mode,
                "margin_used": f(pos.get("marginUsed")),
                "position_value": f(pos.get("positionValue")),
                "liq": liq,
                "dist": dist_to_liq_pct(szi, mark, liq),
                "fund_all": fund_all,
                "fund_open": fund_open,
            }
        )

    # Fills (recent ~2000) for realized PnL breakdown + open-time estimation fallback
    fills: List[Dict[str, Any]] = []
    try:
        fills = info.user_fills(address) or []
    except Exception:
        fills = []

    open_times = estimate_position_open_times(fills, open_sizes)

    # Fallback funding aggregation only when cumFunding missing on positions
    fund_all_map: Dict[str, float] = {}
    fund_open_map: Dict[str, float] = {}
    if not has_cum_funding and open_sizes:
        funding_rows: List[Dict[str, Any]] = []
        try:
            funding_rows = info.user_funding_history(address, startTime=ms_ago(funding_lookback_days)) or []
        except Exception:
            funding_rows = []
        fund_all_map, fund_open_map = aggregate_funding(funding_rows, open_times)

    positions: List[PositionRow] = []
    for b in position_basics:
        coin = b["coin"]
        fa = b["fund_all"] if b["fund_all"] is not None else fund_all_map.get(coin, 0.0)
        fo = b["fund_open"] if b["fund_open"] is not None else fund_open_map.get(coin, 0.0)
        positions.append(
            PositionRow(
                coin=coin,
                side="LONG" if b["szi"] > 0 else "SHORT",
                size=abs(b["szi"]),
                entry_px=b["entry"],
                mark_px=b["mark"],
                u_pnl=b["u_pnl"],
                roe_pct=b["roe"],
                leverage=b["leverage"],
                margin_mode=b["mode"],
                margin_used=b["margin_used"],
                position_value=b["position_value"],
                liquidation_px=b["liq"],
                dist_to_liq_pct=b["dist"],
                funding_all_time=fa,
                funding_since_open=fo,
                open_time_ms=open_times.get(coin),
            )
        )
    positions.sort(key=lambda p: abs(p.position_value), reverse=True)

    # Orders
    open_orders: List[OrderRow] = []
    try:
        for o in info.frontend_open_orders(address) or []:
            open_orders.append(
                OrderRow(
                    coin=o.get("coin", "?"),
                    side="BUY" if o.get("side") == SIDE_BUY else "SELL",
                    size=f(o.get("sz")),
                    limit_px=f(o.get("limitPx")),
                    order_type=o.get("orderType") or "Limit",
                    tif=o.get("tif") or "",
                    reduce_only=bool(o.get("reduceOnly")),
                    oid=int(o.get("oid") or 0),
                    timestamp=int(o.get("timestamp") or 0),
                    trigger_px=f(o["triggerPx"]) if o.get("triggerPx") not in (None, "") else None,
                    is_trigger=bool(o.get("isTrigger")),
                    is_tpsl=bool(o.get("isPositionTpsl")),
                )
            )
    except Exception:
        pass
    open_orders.sort(key=lambda o: o.timestamp, reverse=True)

    historical_orders: List[HistOrderRow] = []
    try:
        for item in info.historical_orders(address) or []:
            o = item.get("order") or item
            historical_orders.append(
                HistOrderRow(
                    coin=o.get("coin", "?"),
                    side="BUY" if o.get("side") == SIDE_BUY else "SELL",
                    size=f(o.get("sz")),
                    orig_sz=f(o.get("origSz") or o.get("sz")),
                    limit_px=f(o.get("limitPx")),
                    status=str(item.get("status") or o.get("status") or ""),
                    order_type=o.get("orderType") or "",
                    timestamp=int(o.get("timestamp") or 0),
                    status_timestamp=int(item.get("statusTimestamp") or o.get("timestamp") or 0),
                    oid=int(o.get("oid") or 0),
                )
            )
    except Exception:
        pass
    historical_orders.sort(key=lambda o: o.status_timestamp, reverse=True)

    # Portfolio / equity
    equity: Dict[str, EquityPeriod] = {}
    try:
        equity = parse_portfolio(info.portfolio(address))
    except Exception:
        pass

    coin_pnl = pnl_by_coin(fills)

    sub_accounts: List[Dict[str, Any]] = []
    try:
        raw_subs = info.query_sub_accounts(address)
        if isinstance(raw_subs, list):
            sub_accounts = raw_subs
        elif raw_subs:
            sub_accounts = [raw_subs]
    except Exception:
        pass

    vault_equities: List[Dict[str, Any]] = []
    try:
        vault_equities = info.user_vault_equities(address) or []
    except Exception:
        pass

    spot_balances: List[Dict[str, Any]] = []
    try:
        spot = info.spot_user_state(address) or {}
        for b in spot.get("balances") or []:
            if f(b.get("total")) != 0:
                spot_balances.append(b)
    except Exception:
        pass

    risk = compute_risk(account_value, withdrawable, margin_used, total_notional, total_u_pnl, positions)

    return AccountSnapshot(
        address=address,
        network=network,
        account_value=account_value,
        withdrawable=withdrawable,
        margin_used=margin_used,
        margin_used_pct=margin_used_pct,
        cross_account_value=f(cross.get("accountValue")),
        total_notional=total_notional,
        total_raw_usd=total_raw,
        total_u_pnl=total_u_pnl,
        n_cross=n_cross,
        n_isolated=n_isolated,
        positions=positions,
        open_orders=open_orders,
        historical_orders=historical_orders,
        equity=equity,
        coin_pnl=coin_pnl,
        sub_accounts=sub_accounts,
        vault_equities=vault_equities,
        risk=risk,
        spot_balances=spot_balances,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def section(title: str, on: bool) -> None:
    print()
    print(bold(f"══ {title} ══", on))


def ascii_sparkline(values: List[float], width: int = 48) -> str:
    if not values:
        return ""
    chars = "▁▂▃▄▅▆▇█"
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values
    lo, hi = min(sampled), max(sampled)
    span = hi - lo if hi != lo else 1.0
    return "".join(chars[min(7, int((v - lo) / span * 7))] for v in sampled)


def risk_bar(score: int, width: int = 24, on: bool = True) -> str:
    filled = int(round(score / 100 * width))
    bar = "█" * filled + "░" * (width - filled)
    if score >= 75:
        return red(bar, on)
    if score >= 55:
        return yellow(bar, on)
    if score >= 35:
        return yellow(bar, on)
    return green(bar, on)


def level_color(level: str, on: bool) -> str:
    return {
        "LOW": green(level, on),
        "MEDIUM": yellow(level, on),
        "HIGH": red(level, on),
        "CRITICAL": bold(red(level, on), on),
        "FLAT": level,
    }.get(level, level)


def print_overview(s: AccountSnapshot, on: bool) -> None:
    section("Account Overview", on)
    print(f"  Address        {s.address}")
    print(f"  Network        {s.network}")
    print(f"  Account Value  {bold(fmt_usd(s.account_value), on)}")
    print(f"  Withdrawable   {fmt_usd(s.withdrawable)}")
    print(f"  Margin Used    {fmt_usd(s.margin_used)}  ({fmt_pct(s.margin_used_pct)})")
    modes = []
    if s.n_cross:
        modes.append(f"{s.n_cross} cross")
    if s.n_isolated:
        modes.append(f"{s.n_isolated} isolated")
    print(f"  Margin Mode    {', '.join(modes) if modes else '— (flat)'}")
    print(f"  Notional       {fmt_usd(s.total_notional)}  ·  eff. lev {s.risk.leverage_effective:.2f}x")
    up = signed_color(s.total_u_pnl, fmt_usd(s.total_u_pnl, signed=True), on)
    print(f"  Unrealized PnL {up}")
    if s.spot_balances:
        print(f"  Spot balances  {len(s.spot_balances)} non-zero token(s)")


def print_risk(s: AccountSnapshot, on: bool) -> None:
    section("Risk View", on)
    r = s.risk
    print(f"  Level          {level_color(r.risk_level, on)}   score {r.risk_score}/100")
    print(f"  Heat           [{risk_bar(r.risk_score, on=on)}]")
    print(f"  Margin used    {fmt_pct(r.margin_used_pct)} of equity")
    print(f"  Free margin    {fmt_pct(r.free_margin_pct)} withdrawable")
    print(f"  Eff. leverage  {r.leverage_effective:.2f}x")
    if r.min_dist_to_liq_pct is not None:
        d = r.min_dist_to_liq_pct
        coin = r.min_dist_coin or "?"
        colored = d
        txt = f"{coin}: {fmt_pct(d)} price move to liq"
        if d < 10:
            txt = red(txt, on)
        elif d < 25:
            txt = yellow(txt, on)
        else:
            txt = green(txt, on)
        print(f"  Closest liq    {txt}")
    else:
        print("  Closest liq    — (no liq prices / no positions)")

    if s.positions:
        print()
        ranked = sorted(
            s.positions,
            key=lambda p: p.dist_to_liq_pct if p.dist_to_liq_pct is not None else 1e18,
        )
        show = ranked[:15]
        print(f"  Positions closest to liquidation (top {len(show)} / {len(ranked)}):")
        print(f"  {'Coin':<10} {'Side':<6} {'Dist→Liq':>10} {'Liq Px':>14} {'Mark':>12} {'uPnL':>12} {'Risk':>8}")
        print("  " + "─" * 78)
        for p in show:
            dist = p.dist_to_liq_pct
            if dist is None:
                tag = "—"
                risk_tag = "n/a"
            elif dist < 5:
                tag = red(fmt_pct(dist), on)
                risk_tag = red("CRIT", on)
            elif dist < 15:
                tag = yellow(fmt_pct(dist), on)
                risk_tag = yellow("HIGH", on)
            elif dist < 30:
                tag = yellow(fmt_pct(dist), on)
                risk_tag = "MED"
            else:
                tag = green(fmt_pct(dist), on)
                risk_tag = green("OK", on)
            print(
                f"  {p.coin:<10} {p.side:<6} {tag:>10} {fmt_px(p.liquidation_px):>14} "
                f"{fmt_px(p.mark_px):>12} {signed_color(p.u_pnl, fmt_usd(p.u_pnl, signed=True), on):>12} {risk_tag:>8}"
            )

    print()
    for note in r.notes:
        print(f"  · {note}")


def print_positions(s: AccountSnapshot, on: bool, limit: int = 40) -> None:
    section(f"Positions ({len(s.positions)})", on)
    if not s.positions:
        print("  (none)")
        return
    shown = s.positions[:limit]
    if len(s.positions) > limit:
        print(f"  Showing top {limit} by notional (of {len(s.positions)}). Use --json for full list.")
    hdr = (
        f"  {'Coin':<8} {'Side':<6} {'Size':>12} {'Entry':>12} {'Mark':>12} "
        f"{'uPnL':>12} {'ROE':>8} {'Liq':>12} {'Dist%':>8} "
        f"{'Fund(open)':>11} {'Fund(all)':>11} {'Mode':>10}"
    )
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for p in shown:
        side_pad = f"{p.side:<6}"
        side_disp = green(side_pad, on) if p.side == "LONG" else red(side_pad, on)
        print(
            f"  {p.coin:<8} {side_disp} {p.size:>12.6g} {fmt_px(p.entry_px):>12} {fmt_px(p.mark_px):>12} "
            f"{signed_color(p.u_pnl, fmt_usd(p.u_pnl, signed=True), on):>12} "
            f"{signed_color(p.roe_pct, fmt_pct(p.roe_pct, signed=True), on):>8} "
            f"{fmt_px(p.liquidation_px):>12} {fmt_pct(p.dist_to_liq_pct):>8} "
            f"{signed_color(p.funding_since_open, fmt_usd(p.funding_since_open, signed=True), on):>11} "
            f"{signed_color(p.funding_all_time, fmt_usd(p.funding_all_time, signed=True), on):>11} "
            f"{p.margin_mode} {p.leverage}x"
        )
    print()
    print(
        f"  Total uPnL: {signed_color(s.total_u_pnl, fmt_usd(s.total_u_pnl, signed=True), on)}  ·  "
        f"Notional: {fmt_usd(s.total_notional)}"
    )
    print("  Funding: position.cumFunding allTime / sinceOpen (API raw sign).")


def print_orders(s: AccountSnapshot, on: bool, hist_limit: int = 25) -> None:
    section(f"Open Orders ({len(s.open_orders)})", on)
    if not s.open_orders:
        print("  (none)")
    else:
        print(f"  {'Coin':<10} {'Side':<5} {'Size':>12} {'Limit':>12} {'Type':<14} {'TIF':<6} {'Flags':<16} {'Time':<16}")
        print("  " + "─" * 96)
        for o in s.open_orders:
            flags = []
            if o.reduce_only:
                flags.append("RO")
            if o.is_tpsl:
                flags.append("TP/SL")
            if o.is_trigger:
                flags.append(f"trig@{fmt_px(o.trigger_px)}")
            side = green(o.side, on) if o.side == "BUY" else red(o.side, on)
            print(
                f"  {o.coin:<10} {side:<14} {o.size:>12.6g} {fmt_px(o.limit_px):>12} "
                f"{o.order_type:<14} {o.tif:<6} {','.join(flags) or '—':<16} {fmt_ts(o.timestamp):<16}"
            )

    shown = s.historical_orders[:hist_limit]
    section(f"Historical Orders (latest {len(shown)} / {len(s.historical_orders)})", on)
    if not shown:
        print("  (none)")
        return
    print(f"  {'Coin':<10} {'Side':<5} {'OrigSz':>10} {'Limit':>12} {'Status':<14} {'Type':<12} {'StatusTime':<16}")
    print("  " + "─" * 88)
    for o in shown:
        side = green(o.side, on) if o.side == "BUY" else red(o.side, on)
        print(
            f"  {o.coin:<10} {side:<14} {o.orig_sz:>10.6g} {fmt_px(o.limit_px):>12} "
            f"{o.status:<14} {o.order_type:<12} {fmt_ts(o.status_timestamp):<16}"
        )


def print_equity(s: AccountSnapshot, on: bool, period: str = "week") -> None:
    section("Equity Curve & Portfolio Periods", on)
    if not s.equity:
        print("  (portfolio endpoint returned no data)")
        return

    # Summary table for standard periods
    order = ["day", "week", "month", "allTime", "perpDay", "perpWeek", "perpMonth", "perpAllTime"]
    print(f"  {'Period':<14} {'Start AV':>14} {'End AV':>14} {'PnL':>14} {'Volume':>14}")
    print("  " + "─" * 72)
    for name in order:
        ep = s.equity.get(name)
        if not ep:
            continue
        pnl_s = "—" if ep.pnl is None else signed_color(ep.pnl, fmt_usd(ep.pnl, signed=True), on)
        print(
            f"  {name:<14} {fmt_usd(ep.account_value_start or 0):>14} "
            f"{fmt_usd(ep.account_value_end or 0):>14} {pnl_s:>14} "
            f"{fmt_usd(ep.volume or 0):>14}"
        )

    # Sparkline for requested period (fallback chain)
    candidates = [period]
    if not period.startswith("perp"):
        candidates.append("perp" + period[:1].upper() + period[1:] if period else period)
    candidates.extend(["week", "day", "month", "allTime", "perpWeek", "perpDay"])
    chosen = None
    lower_map = {k.lower(): v for k, v in s.equity.items()}
    for key in candidates:
        ep = s.equity.get(key) or lower_map.get(key.lower())
        if ep and ep.history:
            chosen = ep
            break
    if chosen is None and s.equity:
        chosen = next(iter(s.equity.values()))

    if chosen and chosen.history:
        vals = [v for _, v in chosen.history]
        spark = ascii_sparkline(vals)
        print()
        print(f"  Curve [{chosen.period}]  {spark}")
        print(
            f"  {fmt_ts(chosen.history[0][0])}  →  {fmt_ts(chosen.history[-1][0])}   "
            f"{fmt_usd(vals[0])} → {fmt_usd(vals[-1])}  "
            f"Δ {signed_color(vals[-1]-vals[0], fmt_usd(vals[-1]-vals[0], signed=True), on)}"
        )


def print_coin_pnl(s: AccountSnapshot, on: bool, limit: int = 20) -> None:
    section(f"Realized PnL by Coin (from recent fills, top {limit})", on)
    if not s.coin_pnl:
        print("  (no fills)")
        return
    print(f"  {'Coin':<12} {'Closed PnL':>12} {'Fees':>12} {'Net':>12} {'Volume':>14} {'Fills':>7}")
    print("  " + "─" * 72)
    for row in s.coin_pnl[:limit]:
        print(
            f"  {row.coin:<12} "
            f"{signed_color(row.closed_pnl, fmt_usd(row.closed_pnl, signed=True), on):>12} "
            f"{fmt_usd(row.fees):>12} "
            f"{signed_color(row.realized_net, fmt_usd(row.realized_net, signed=True), on):>12} "
            f"{fmt_usd(row.volume):>14} {row.n_fills:>7}"
        )
    tot_net = sum(r.realized_net for r in s.coin_pnl)
    tot_closed = sum(r.closed_pnl for r in s.coin_pnl)
    tot_fees = sum(r.fees for r in s.coin_pnl)
    print("  " + "─" * 72)
    print(
        f"  {'TOTAL':<12} "
        f"{signed_color(tot_closed, fmt_usd(tot_closed, signed=True), on):>12} "
        f"{fmt_usd(tot_fees):>12} "
        f"{signed_color(tot_net, fmt_usd(tot_net, signed=True), on):>12}"
    )
    print("  Note: userFills is capped (~2000 most recent); not full lifetime history.")


def print_subs_vaults(s: AccountSnapshot, on: bool) -> None:
    section(f"Subaccounts ({len(s.sub_accounts)})", on)
    if not s.sub_accounts:
        print("  (none)")
    else:
        for i, sub in enumerate(s.sub_accounts):
            # API shape can vary: name/subAccountUser/clearinghouseState
            name = sub.get("name") or sub.get("subAccountUser") or sub.get("address") or f"sub-{i}"
            user = sub.get("subAccountUser") or sub.get("address") or ""
            ch = sub.get("clearinghouseState") or sub.get("clearinghouse") or {}
            ms = ch.get("marginSummary") or {}
            av = f(ms.get("accountValue")) if ms else f(sub.get("accountValue"))
            print(f"  · {name}  {user}  AV={fmt_usd(av)}")

    section(f"Vault Equities ({len(s.vault_equities)})", on)
    if not s.vault_equities:
        print("  (none)")
        return
    print(f"  {'Vault':<44} {'Equity':>14} {'Locked until':<20}")
    print("  " + "─" * 80)
    total = 0.0
    for v in s.vault_equities:
        addr = v.get("vaultAddress") or v.get("vault") or "?"
        eq = f(v.get("equity"))
        total += eq
        lock = v.get("lockedUntilTimestamp")
        lock_s = fmt_ts(int(lock)) if lock else "—"
        print(f"  {addr:<44} {fmt_usd(eq):>14} {lock_s:<20}")
    print(f"  {'TOTAL':<44} {fmt_usd(total):>14}")


def render(
    s: AccountSnapshot,
    sections: Optional[List[str]],
    period: str,
    color_on: bool,
    hist_limit: int,
    pos_limit: int = 40,
) -> None:
    all_sections = ["overview", "risk", "positions", "orders", "equity", "pnl", "subs"]
    wanted = set(sections) if sections else set(all_sections)

    print()
    print(bold(cyan("Hyperliquid Account Dashboard", color_on), color_on))
    print(cyan(f"{s.address}  ·  {s.network}", color_on))

    if "overview" in wanted:
        print_overview(s, color_on)
    if "risk" in wanted:
        print_risk(s, color_on)
    if "positions" in wanted:
        print_positions(s, color_on, limit=pos_limit)
    if "orders" in wanted:
        print_orders(s, color_on, hist_limit=hist_limit)
    if "equity" in wanted:
        print_equity(s, color_on, period=period)
    if "pnl" in wanted:
        print_coin_pnl(s, color_on)
    if "subs" in wanted:
        print_subs_vaults(s, color_on)
    print()


def snapshot_to_jsonable(s: AccountSnapshot) -> Dict[str, Any]:
    return {
        "address": s.address,
        "network": s.network,
        "account_value": s.account_value,
        "withdrawable": s.withdrawable,
        "margin_used": s.margin_used,
        "margin_used_pct": s.margin_used_pct,
        "total_notional": s.total_notional,
        "total_u_pnl": s.total_u_pnl,
        "n_cross": s.n_cross,
        "n_isolated": s.n_isolated,
        "positions": [asdict(p) for p in s.positions],
        "open_orders": [asdict(o) for o in s.open_orders],
        "historical_orders": [asdict(o) for o in s.historical_orders[:100]],
        "equity": {
            k: {
                "period": v.period,
                "account_value_start": v.account_value_start,
                "account_value_end": v.account_value_end,
                "pnl": v.pnl,
                "volume": v.volume,
                "history": v.history,
                "pnl_history": v.pnl_history,
            }
            for k, v in s.equity.items()
        },
        "coin_pnl": [asdict(c) for c in s.coin_pnl],
        "sub_accounts": s.sub_accounts,
        "vault_equities": s.vault_equities,
        "spot_balances": s.spot_balances,
        "risk": asdict(s.risk),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Hyperliquid account dashboard (read-only)")
    p.add_argument("--address", "-a", help="Account address (0x...). Defaults to examples/config.json")
    p.add_argument("--testnet", action="store_true", help="Use testnet API")
    p.add_argument("--mainnet", action="store_true", help="Use mainnet API (default)")
    p.add_argument(
        "--period",
        default="week",
        help="Equity curve period: day|week|month|allTime|perpDay|perpWeek|perpMonth|perpAllTime",
    )
    p.add_argument(
        "--section",
        "-s",
        action="append",
        choices=["overview", "risk", "positions", "orders", "equity", "pnl", "subs"],
        help="Only show these sections (repeatable). Default: all.",
    )
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    p.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    p.add_argument("--hist-limit", type=int, default=25, help="Max historical orders to print")
    p.add_argument("--pos-limit", type=int, default=40, help="Max positions to print in table")
    p.add_argument(
        "--funding-days",
        type=float,
        default=365.0,
        help="Lookback (days) for userFunding fallback when cumFunding absent",
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    address = args.address or load_address_from_config()
    if not address:
        print(
            "Error: provide --address 0x... or set account_address in examples/config.json",
            file=sys.stderr,
        )
        return 1

    base_url = constants.TESTNET_API_URL if args.testnet else constants.MAINNET_API_URL
    network = "testnet" if args.testnet else "mainnet"
    color_on = (not args.no_color) and sys.stdout.isatty() and not args.json

    info = Info(base_url, skip_ws=True)
    try:
        snap = fetch_snapshot(info, address, network, funding_lookback_days=args.funding_days)
    except Exception as exc:
        print(f"Failed to fetch account data: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(snapshot_to_jsonable(snap), indent=2))
    else:
        render(
            snap,
            args.section,
            period=args.period,
            color_on=color_on,
            hist_limit=args.hist_limit,
            pos_limit=args.pos_limit,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
