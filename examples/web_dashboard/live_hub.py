"""Server-side Hyperliquid WebSocket hub + SSE fanout."""

from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set

from hyperliquid.info import Info


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


class LiveHub:
    """Maintains one HL websocket and fans events to SSE subscribers."""

    def __init__(self, base_url: str, whale_usd: float = 75_000.0):
        self.base_url = base_url
        self.whale_usd = whale_usd
        self._lock = threading.RLock()
        self._info: Optional[Info] = None
        self._started = False

        self.mids: Dict[str, str] = {}
        self.book: Dict[str, Any] = {}  # coin -> l2
        self.trades: Deque[Dict[str, Any]] = deque(maxlen=200)
        self.whales: Deque[Dict[str, Any]] = deque(maxlen=100)
        self.candles: Deque[Dict[str, Any]] = deque(maxlen=300)
        self.user_fills: Deque[Dict[str, Any]] = deque(maxlen=100)

        self._coin: Optional[str] = None
        self._interval: str = "15m"
        self._user: Optional[str] = None
        self._sub_ids: Dict[str, int] = {}

        self._clients: Set[queue.Queue] = set()
        self._clients_lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._info = Info(self.base_url, skip_ws=False)
            self._started = True
            # allMids always on
            sid = self._info.subscribe({"type": "allMids"}, self._on_mids)
            self._sub_ids["allMids"] = sid
            # default coin
            self.set_coin("BTC")

    def stop(self) -> None:
        with self._lock:
            if self._info:
                try:
                    self._info.disconnect_websocket()
                except Exception:
                    pass
            self._info = None
            self._started = False

    def add_client(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._clients_lock:
            self._clients.add(q)
        # snapshot
        self._push_one(
            q,
            {
                "type": "snapshot",
                "mids": dict(list(self.mids.items())[:80]),
                "coin": self._coin,
                "book": self.book.get(self._coin or ""),
                "trades": list(self.trades)[-40:],
                "whales": list(self.whales)[-30:],
                "candles": list(self.candles)[-120:],
            },
        )
        return q

    def remove_client(self, q: queue.Queue) -> None:
        with self._clients_lock:
            self._clients.discard(q)

    def _broadcast(self, msg: Dict[str, Any]) -> None:
        data = msg
        with self._clients_lock:
            dead = []
            for q in self._clients:
                try:
                    q.put_nowait(data)
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        q.put_nowait(data)
                    except queue.Full:
                        dead.append(q)
            for q in dead:
                self._clients.discard(q)

    def _push_one(self, q: queue.Queue, msg: Dict[str, Any]) -> None:
        try:
            q.put_nowait(msg)
        except queue.Full:
            pass

    def _on_mids(self, ws_msg: Any) -> None:
        try:
            mids = ws_msg.get("data", {}).get("mids") or {}
            with self._lock:
                self.mids.update(mids)
            # thin broadcast of subset / ticker pulse
            self._broadcast({"type": "mids", "mids": mids, "ts": int(time.time() * 1000)})
        except Exception:
            pass

    def _on_book(self, ws_msg: Any) -> None:
        try:
            data = ws_msg.get("data") or {}
            coin = data.get("coin")
            with self._lock:
                self.book[coin] = data
            self._broadcast({"type": "l2Book", "data": data})
        except Exception:
            pass

    def _on_trades(self, ws_msg: Any) -> None:
        try:
            trades = ws_msg.get("data") or []
            out = []
            whales = []
            for t in trades:
                px = _f(t.get("px"))
                sz = _f(t.get("sz"))
                ntl = px * sz
                row = {
                    "coin": t.get("coin"),
                    "side": "BUY" if t.get("side") == "B" else "SELL",
                    "px": px,
                    "sz": sz,
                    "notional": ntl,
                    "time": t.get("time"),
                    "hash": t.get("hash"),
                    "whale": ntl >= self.whale_usd,
                }
                out.append(row)
                if row["whale"]:
                    whales.append(row)
            with self._lock:
                self.trades.extend(out)
                self.whales.extend(whales)
            if out:
                self._broadcast({"type": "trades", "trades": out})
            if whales:
                self._broadcast({"type": "whales", "whales": whales})
        except Exception:
            pass

    def _on_candle(self, ws_msg: Any) -> None:
        try:
            data = ws_msg.get("data") or {}
            # candle fields: t,T,s,i,o,c,h,l,v,n
            row = {
                "t": data.get("t"),
                "T": data.get("T"),
                "s": data.get("s"),
                "i": data.get("i"),
                "o": _f(data.get("o")),
                "c": _f(data.get("c")),
                "h": _f(data.get("h")),
                "l": _f(data.get("l")),
                "v": _f(data.get("v")),
                "n": data.get("n"),
            }
            with self._lock:
                # replace last if same t
                if self.candles and self.candles[-1].get("t") == row.get("t"):
                    self.candles[-1] = row
                else:
                    self.candles.append(row)
            self._broadcast({"type": "candle", "candle": row})
        except Exception:
            pass

    def _on_user_fills(self, ws_msg: Any) -> None:
        try:
            data = ws_msg.get("data") or {}
            fills = data.get("fills") or []
            with self._lock:
                self.user_fills.extend(fills)
            self._broadcast({"type": "userFills", "fills": fills, "isSnapshot": data.get("isSnapshot")})
        except Exception:
            pass

    def _unsub(self, key: str, sub: Dict[str, Any]) -> None:
        if not self._info:
            return
        sid = self._sub_ids.pop(key, None)
        if sid is not None:
            try:
                self._info.unsubscribe(sub, sid)  # type: ignore[arg-type]
            except Exception:
                pass

    def set_coin(self, coin: str, interval: str = "15m") -> None:
        coin = coin.strip()
        if not coin:
            return
        with self._lock:
            if not self._info:
                return
            # unsubscribe previous coin streams
            if self._coin:
                prev = self._coin
                self._unsub(f"l2:{prev}", {"type": "l2Book", "coin": prev})
                self._unsub(f"tr:{prev}", {"type": "trades", "coin": prev})
                self._unsub(f"cd:{prev}:{self._interval}", {"type": "candle", "coin": prev, "interval": self._interval})

            self._coin = coin
            self._interval = interval
            self.book.clear()
            self.trades.clear()
            self.candles.clear()

            self._sub_ids[f"l2:{coin}"] = self._info.subscribe({"type": "l2Book", "coin": coin}, self._on_book)
            self._sub_ids[f"tr:{coin}"] = self._info.subscribe({"type": "trades", "coin": coin}, self._on_trades)
            self._sub_ids[f"cd:{coin}:{interval}"] = self._info.subscribe(
                {"type": "candle", "coin": coin, "interval": interval}, self._on_candle
            )

            # seed candles via REST
            try:
                end = int(time.time() * 1000)
                start = end - 12 * 3600 * 1000
                snap = self._info.candles_snapshot(coin, interval, start, end) or []
                for c in snap[-120:]:
                    self.candles.append(
                        {
                            "t": c.get("t"),
                            "T": c.get("T"),
                            "s": c.get("s"),
                            "i": c.get("i"),
                            "o": _f(c.get("o")),
                            "c": _f(c.get("c")),
                            "h": _f(c.get("h")),
                            "l": _f(c.get("l")),
                            "v": _f(c.get("v")),
                            "n": c.get("n"),
                        }
                    )
            except Exception:
                pass

            # seed book
            try:
                book = self._info.l2_snapshot(coin)
                self.book[coin] = book
            except Exception:
                pass

        self._broadcast(
            {
                "type": "coin",
                "coin": coin,
                "interval": interval,
                "book": self.book.get(coin),
                "candles": list(self.candles),
            }
        )

    def set_user(self, user: Optional[str]) -> None:
        with self._lock:
            if not self._info:
                return
            if self._user:
                self._unsub(f"uf:{self._user}", {"type": "userFills", "user": self._user})
            self._user = user
            self.user_fills.clear()
            if user:
                self._sub_ids[f"uf:{user}"] = self._info.subscribe(
                    {"type": "userFills", "user": user}, self._on_user_fills
                )
        self._broadcast({"type": "user", "user": user})

    def status(self) -> Dict[str, Any]:
        return {
            "started": self._started,
            "coin": self._coin,
            "interval": self._interval,
            "user": self._user,
            "n_mids": len(self.mids),
            "n_trades": len(self.trades),
            "n_whales": len(self.whales),
            "clients": len(self._clients),
            "whale_usd": self.whale_usd,
        }


def format_sse(event: Dict[str, Any]) -> bytes:
    payload = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
    return f"data: {payload}\n\n".encode("utf-8")
