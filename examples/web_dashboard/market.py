"""Market overview, funding heatmap, predicted fundings."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from hyperliquid.info import Info

_CACHE: Dict[str, Tuple[float, Any]] = {}
_TTL_MARKET = 8.0
_TTL_PRED = 30.0


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _cache_get(key: str, ttl: float) -> Optional[Any]:
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def _cache_set(key: str, val: Any) -> None:
    _CACHE[key] = (time.time(), val)


def build_markets(info: Info) -> Dict[str, Any]:
    cached = _cache_get("markets", _TTL_MARKET)
    if cached is not None:
        return cached

    meta, ctxs = info.meta_and_asset_ctxs()
    mids = {}
    try:
        mids = info.all_mids() or {}
    except Exception:
        pass

    rows: List[Dict[str, Any]] = []
    for asset, ctx in zip(meta.get("universe") or [], ctxs or []):
        name = asset.get("name") or "?"
        mark = _f(ctx.get("markPx") or ctx.get("midPx") or mids.get(name))
        prev = _f(ctx.get("prevDayPx"))
        chg = ((mark - prev) / prev * 100.0) if prev else 0.0
        funding = _f(ctx.get("funding"))
        # funding is hourly rate on HL; annualize ~ funding * 24 * 365
        funding_apr = funding * 24 * 365 * 100.0
        oi = _f(ctx.get("openInterest"))
        oi_usd = oi * mark if mark else 0.0
        vol = _f(ctx.get("dayNtlVlm"))
        rows.append(
            {
                "coin": name,
                "mark": mark,
                "mid": _f(ctx.get("midPx") or mids.get(name) or mark),
                "oracle": _f(ctx.get("oraclePx")),
                "prev_day": prev,
                "change_24h_pct": chg,
                "funding": funding,
                "funding_pct": funding * 100.0,  # per hour %
                "funding_apr_pct": funding_apr,
                "premium": _f(ctx.get("premium")),
                "open_interest": oi,
                "open_interest_usd": oi_usd,
                "volume_24h": vol,
                "day_base_vlm": _f(ctx.get("dayBaseVlm")),
                "max_leverage": asset.get("maxLeverage"),
                "only_isolated": bool(asset.get("onlyIsolated")),
            }
        )

    # OI growth proxy: not available historically in one call — leave null
    # Sort helpers precomputed ranks
    by_funding = sorted(rows, key=lambda r: r["funding"], reverse=True)
    by_funding_low = sorted(rows, key=lambda r: r["funding"])
    by_vol = sorted(rows, key=lambda r: r["volume_24h"], reverse=True)
    by_oi = sorted(rows, key=lambda r: r["open_interest_usd"], reverse=True)
    by_chg = sorted(rows, key=lambda r: r["change_24h_pct"], reverse=True)

    # Funding opportunities: high |funding| with decent OI/vol
    opps = []
    for r in rows:
        if r["volume_24h"] < 50_000 and r["open_interest_usd"] < 100_000:
            continue
        fr = r["funding"]
        if abs(fr) < 1e-6:
            continue
        # Positive funding → longs pay shorts → short is funding-long idea
        side = "SHORT" if fr > 0 else "LONG"
        opps.append(
            {
                "coin": r["coin"],
                "side": side,
                "funding_pct": r["funding_pct"],
                "funding_apr_pct": r["funding_apr_pct"],
                "mark": r["mark"],
                "volume_24h": r["volume_24h"],
                "open_interest_usd": r["open_interest_usd"],
                "change_24h_pct": r["change_24h_pct"],
                "thesis": (
                    f"Funding {r['funding_pct']:+.4f}%/h — "
                    + ("longs pay shorts → collect by shorting" if fr > 0 else "shorts pay longs → collect by longing")
                ),
                "score": abs(fr) * (1 + min(r["volume_24h"], 5e7) / 5e7),
            }
        )
    opps.sort(key=lambda x: x["score"], reverse=True)

    # Heatmap: top by |funding| for display grid
    heat = sorted(rows, key=lambda r: abs(r["funding"]), reverse=True)[:64]

    payload = {
        "fetched_at": int(time.time() * 1000),
        "count": len(rows),
        "markets": rows,
        "top_funding_long": [  # highest positive funding (shorts receive)
            {
                "coin": r["coin"],
                "funding_pct": r["funding_pct"],
                "funding_apr_pct": r["funding_apr_pct"],
                "volume_24h": r["volume_24h"],
                "open_interest_usd": r["open_interest_usd"],
            }
            for r in by_funding[:15]
            if r["funding"] > 0
        ],
        "top_funding_short": [  # most negative (longs receive)
            {
                "coin": r["coin"],
                "funding_pct": r["funding_pct"],
                "funding_apr_pct": r["funding_apr_pct"],
                "volume_24h": r["volume_24h"],
                "open_interest_usd": r["open_interest_usd"],
            }
            for r in by_funding_low[:15]
            if r["funding"] < 0
        ],
        "top_volume": [
            {"coin": r["coin"], "volume_24h": r["volume_24h"], "change_24h_pct": r["change_24h_pct"], "mark": r["mark"]}
            for r in by_vol[:15]
        ],
        "top_oi": [
            {
                "coin": r["coin"],
                "open_interest_usd": r["open_interest_usd"],
                "funding_pct": r["funding_pct"],
                "mark": r["mark"],
            }
            for r in by_oi[:15]
        ],
        "top_gainers": [
            {"coin": r["coin"], "change_24h_pct": r["change_24h_pct"], "mark": r["mark"], "volume_24h": r["volume_24h"]}
            for r in by_chg[:10]
        ],
        "top_losers": [
            {"coin": r["coin"], "change_24h_pct": r["change_24h_pct"], "mark": r["mark"], "volume_24h": r["volume_24h"]}
            for r in sorted(rows, key=lambda r: r["change_24h_pct"])[:10]
        ],
        "opportunities": opps[:20],
        "heatmap": [
            {
                "coin": r["coin"],
                "funding_pct": r["funding_pct"],
                "volume_24h": r["volume_24h"],
                "change_24h_pct": r["change_24h_pct"],
            }
            for r in heat
        ],
    }
    _cache_set("markets", payload)
    return payload


def predicted_fundings(info: Info) -> Dict[str, Any]:
    cached = _cache_get("predicted", _TTL_PRED)
    if cached is not None:
        return cached
    raw = info.post("/info", {"type": "predictedFundings"})
    # shape: [[coin, [[venue, {fundingRate, nextFundingTime, fundingIntervalHours}], ...]], ...]
    rows = []
    if isinstance(raw, list):
        for item in raw:
            if not (isinstance(item, (list, tuple)) and len(item) >= 2):
                continue
            coin, venues = item[0], item[1]
            parsed = []
            hl = None
            for v in venues or []:
                if not (isinstance(v, (list, tuple)) and len(v) >= 2):
                    continue
                name, data = v[0], v[1] or {}
                rate = _f(data.get("fundingRate"))
                entry = {
                    "venue": name,
                    "funding_rate": rate,
                    "funding_pct": rate * 100.0,
                    "next_funding_time": data.get("nextFundingTime"),
                    "interval_hours": data.get("fundingIntervalHours"),
                }
                parsed.append(entry)
                if name == "HlPerp":
                    hl = entry
            # arb: HL vs others
            arb = []
            if hl:
                for p in parsed:
                    if p["venue"] == "HlPerp":
                        continue
                    diff = hl["funding_rate"] - p["funding_rate"]
                    arb.append(
                        {
                            "vs": p["venue"],
                            "hl_minus_other": diff,
                            "hl_minus_other_pct": diff * 100.0,
                        }
                    )
            rows.append({"coin": coin, "venues": parsed, "hl": hl, "arb": arb})

    payload = {"fetched_at": int(time.time() * 1000), "predicted": rows, "count": len(rows)}
    _cache_set("predicted", payload)
    return payload


def funding_history_coin(info: Info, coin: str, days: float = 7.0) -> List[Dict[str, Any]]:
    start = int((time.time() - days * 86400) * 1000)
    try:
        hist = info.funding_history(coin, startTime=start) or []
    except Exception:
        return []
    out = []
    for h in hist:
        out.append(
            {
                "time": h.get("time"),
                "funding_rate": _f(h.get("fundingRate")),
                "funding_pct": _f(h.get("fundingRate")) * 100.0,
                "premium": _f(h.get("premium")),
                "coin": h.get("coin") or coin,
            }
        )
    return out


def vault_details(info: Info, vault_address: str) -> Any:
    return info.post("/info", {"type": "vaultDetails", "vaultAddress": vault_address})
