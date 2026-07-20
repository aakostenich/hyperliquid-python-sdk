"""PnL analytics and simple portfolio risk calculator."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def analyze_fills(fills: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not fills:
        return {
            "n_fills": 0,
            "realized_pnl": 0.0,
            "fees": 0.0,
            "net_pnl": 0.0,
            "volume": 0.0,
            "winrate": None,
            "n_winning": 0,
            "n_losing": 0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "best_trade": None,
            "worst_trade": None,
            "by_coin": [],
            "avg_hold_ms": None,
            "hold_samples": 0,
        }

    realized = 0.0
    fees = 0.0
    volume = 0.0
    wins: List[float] = []
    losses: List[float] = []
    best = None
    worst = None
    by_coin: Dict[str, Dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "fees": 0.0, "vol": 0.0, "n": 0})

    # crude hold time: pair open/close by coin via startPosition flips
    open_ts: Dict[str, int] = {}
    holds: List[int] = []

    sorted_fills = sorted(fills, key=lambda x: x.get("time") or 0)
    for fill in sorted_fills:
        coin = fill.get("coin") or "?"
        pnl = _f(fill.get("closedPnl"))
        fee = _f(fill.get("fee"))
        px = _f(fill.get("px"))
        sz = _f(fill.get("sz"))
        t = int(fill.get("time") or 0)
        realized += pnl
        fees += fee
        volume += px * sz
        c = by_coin[coin]
        c["pnl"] += pnl
        c["fees"] += fee
        c["vol"] += px * sz
        c["n"] += 1

        if pnl > 0:
            wins.append(pnl)
        elif pnl < 0:
            losses.append(pnl)

        if best is None or pnl > best["closed_pnl"]:
            best = {
                "coin": coin,
                "closed_pnl": pnl,
                "px": px,
                "sz": sz,
                "time": t,
                "dir": fill.get("dir"),
                "side": fill.get("side"),
            }
        if worst is None or pnl < worst["closed_pnl"]:
            worst = {
                "coin": coin,
                "closed_pnl": pnl,
                "px": px,
                "sz": sz,
                "time": t,
                "dir": fill.get("dir"),
                "side": fill.get("side"),
            }

        # hold estimation
        start = _f(fill.get("startPosition"))
        signed = sz if fill.get("side") == "B" else -sz
        end = start + signed
        if abs(start) < 1e-12 and abs(end) > 1e-12:
            open_ts[coin] = t
        elif abs(end) < 1e-12 and coin in open_ts:
            holds.append(max(0, t - open_ts[coin]))
            del open_ts[coin]

    n_closed = len(wins) + len(losses)
    winrate = (len(wins) / n_closed * 100.0) if n_closed else None

    coin_rows = [
        {
            "coin": k,
            "realized_pnl": v["pnl"],
            "fees": v["fees"],
            "net": v["pnl"] - v["fees"],
            "volume": v["vol"],
            "n_fills": int(v["n"]),
        }
        for k, v in by_coin.items()
    ]
    coin_rows.sort(key=lambda r: abs(r["net"]), reverse=True)

    return {
        "n_fills": len(fills),
        "realized_pnl": realized,
        "fees": fees,
        "net_pnl": realized - fees,
        "volume": volume,
        "winrate": winrate,
        "n_winning": len(wins),
        "n_losing": len(losses),
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "best_trade": best,
        "worst_trade": worst,
        "by_coin": coin_rows[:30],
        "avg_hold_ms": (sum(holds) / len(holds)) if holds else None,
        "hold_samples": len(holds),
    }


def risk_calculator(
    positions: List[Dict[str, Any]],
    account_value: float,
    price_move_pct: float,
) -> Dict[str, Any]:
    """What happens if every mark moves by price_move_pct (same direction for all).

    For shorts, a +X% mark move is adverse; for longs beneficial.
    """
    move = price_move_pct / 100.0
    impacts = []
    total = 0.0
    for p in positions:
        size = abs(_f(p.get("size") or p.get("szi")))
        side = p.get("side") or ("LONG" if _f(p.get("szi")) > 0 else "SHORT")
        mark = _f(p.get("mark_px") or p.get("mark"))
        if not mark or not size:
            continue
        # notional change ≈ size * mark * move * sign
        sign = 1.0 if side in ("LONG", "B") else -1.0
        # if szi present use it
        if p.get("szi") is not None:
            sign = 1.0 if _f(p.get("szi")) > 0 else -1.0
        pnl = sign * size * mark * move
        total += pnl
        new_mark = mark * (1 + move)
        liq = p.get("liquidation_px")
        dist = p.get("dist_to_liq_pct")
        new_dist = None
        if liq is not None and new_mark:
            liq_f = _f(liq)
            if sign > 0:
                new_dist = (new_mark - liq_f) / new_mark * 100.0
            else:
                new_dist = (liq_f - new_mark) / new_mark * 100.0
        impacts.append(
            {
                "coin": p.get("coin"),
                "side": side if isinstance(side, str) else ("LONG" if sign > 0 else "SHORT"),
                "mark": mark,
                "new_mark": new_mark,
                "pnl_impact": pnl,
                "dist_to_liq_pct": dist,
                "new_dist_to_liq_pct": new_dist,
                "position_value": size * mark,
            }
        )
    impacts.sort(key=lambda x: abs(x["pnl_impact"]), reverse=True)
    new_av = account_value + total
    return {
        "price_move_pct": price_move_pct,
        "total_pnl_impact": total,
        "account_value": account_value,
        "new_account_value": new_av,
        "pct_of_equity": (total / account_value * 100.0) if account_value else 0.0,
        "positions": impacts,
    }


def multi_wallet_compare(snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for s in snapshots:
        risk = s.get("risk") or {}
        out.append(
            {
                "address": s.get("address"),
                "account_value": s.get("account_value"),
                "withdrawable": s.get("withdrawable"),
                "margin_used_pct": s.get("margin_used_pct"),
                "total_u_pnl": s.get("total_u_pnl"),
                "total_notional": s.get("total_notional"),
                "n_positions": len(s.get("positions") or []),
                "risk_level": risk.get("risk_level"),
                "risk_score": risk.get("risk_score"),
                "leverage_effective": risk.get("leverage_effective"),
            }
        )
    return out
