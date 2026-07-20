#!/usr/bin/env python3
"""Hyperliquid terminal dashboard — portfolio, markets, live WS, analytics.

  python examples/web_dashboard/server.py
  python examples/web_dashboard/server.py --port 8765 --testnet
  open http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import queue
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(EXAMPLES))
sys.path.insert(0, str(HERE))

from account_dashboard import (  # noqa: E402
    fetch_snapshot,
    load_address_from_config,
    snapshot_to_jsonable,
)
from analytics import analyze_fills, multi_wallet_compare, risk_calculator  # noqa: E402
from hyperliquid.info import Info  # noqa: E402
from hyperliquid.utils import constants  # noqa: E402
from live_hub import LiveHub, format_sse  # noqa: E402
from market import (  # noqa: E402
    build_markets,
    funding_history_coin,
    predicted_fundings,
    vault_details,
)

_CACHE: Dict[Tuple[str, str], Tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 12.0

STATE: Dict[str, Any] = {
    "base_url": constants.MAINNET_API_URL,
    "network": "mainnet",
    "default_address": "",
    "hub": None,  # type: Optional[LiveHub]
}


def _get_info(skip_ws: bool = True) -> Info:
    return Info(STATE["base_url"], skip_ws=skip_ws)


def load_account(address: str, use_cache: bool = True) -> Dict[str, Any]:
    key = (address.lower(), STATE["network"])
    now = time.time()
    if use_cache:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit and now - hit[0] < _CACHE_TTL:
                return hit[1]

    info = _get_info()
    snap = fetch_snapshot(info, address, STATE["network"])
    payload = snapshot_to_jsonable(snap)
    payload["fetched_at"] = int(now * 1000)
    # attach fills analytics cheaply from fills already fetched inside dashboard — re-fetch fills for analytics
    try:
        fills = info.user_fills(address) or []
        payload["analytics"] = analyze_fills(fills)
        payload["fills_raw_n"] = len(fills)
    except Exception:
        payload["analytics"] = analyze_fills([])
    try:
        payload["user_fees"] = info.user_fees(address)
    except Exception:
        payload["user_fees"] = None

    with _CACHE_LOCK:
        _CACHE[key] = (now, payload)
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "HLDashboard/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, content_type: Optional[str] = None) -> None:
        if not path.is_file():
            self.send_error(404, "Not found")
            return
        data = path.read_bytes()
        ctype = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._json(400, {"error": "invalid JSON"})
            return

        if parsed.path == "/api/live/coin":
            coin = (body.get("coin") or "BTC").strip()
            interval = (body.get("interval") or "15m").strip()
            hub: LiveHub = STATE["hub"]
            hub.set_coin(coin, interval)
            self._json(200, {"ok": True, "coin": coin, "interval": interval})
            return

        if parsed.path == "/api/live/user":
            user = (body.get("user") or "").strip() or None
            hub = STATE["hub"]
            hub.set_user(user)
            self._json(200, {"ok": True, "user": user})
            return

        if parsed.path == "/api/risk":
            try:
                move = float(body.get("price_move_pct") or 0)
                address = (body.get("address") or "").strip()
                if not address:
                    self._json(400, {"error": "address required"})
                    return
                acc = load_account(address, use_cache=True)
                result = risk_calculator(acc.get("positions") or [], float(acc.get("account_value") or 0), move)
                self._json(200, result)
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return

        if parsed.path == "/api/compare":
            addresses: List[str] = body.get("addresses") or []
            addresses = [a.strip() for a in addresses if a and a.strip()][:6]
            snaps = []
            for a in addresses:
                try:
                    snaps.append(load_account(a, use_cache=True))
                except Exception as exc:
                    snaps.append({"address": a, "error": str(exc)})
            self._json(200, {"wallets": multi_wallet_compare([s for s in snaps if "error" not in s]), "raw": snaps})
            return

        self._json(404, {"error": "not found"})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._file(STATIC / "index.html", "text/html; charset=utf-8")
            return

        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            target = (STATIC / rel).resolve()
            if not str(target).startswith(str(STATIC.resolve())):
                self.send_error(403)
                return
            self._file(target)
            return

        if path == "/api/health":
            hub: LiveHub = STATE["hub"]
            self._json(
                200,
                {
                    "ok": True,
                    "network": STATE["network"],
                    "default_address": STATE["default_address"],
                    "live": hub.status() if hub else None,
                },
            )
            return

        if path == "/api/account":
            address = (qs.get("address") or [STATE["default_address"]])[0].strip()
            if not address:
                self._json(400, {"error": "Missing address. Pass ?address=0x..."})
                return
            refresh = (qs.get("refresh") or ["0"])[0] in ("1", "true", "yes")
            try:
                self._json(200, load_account(address, use_cache=not refresh))
            except Exception as exc:
                traceback.print_exc()
                self._json(500, {"error": str(exc)})
            return

        if path == "/api/market":
            try:
                info = _get_info()
                self._json(200, build_markets(info))
            except Exception as exc:
                traceback.print_exc()
                self._json(500, {"error": str(exc)})
            return

        if path == "/api/predicted-fundings":
            try:
                self._json(200, predicted_fundings(_get_info()))
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return

        if path == "/api/funding-history":
            coin = (qs.get("coin") or ["BTC"])[0]
            days = float((qs.get("days") or ["7"])[0])
            try:
                hist = funding_history_coin(_get_info(), coin, days=days)
                self._json(200, {"coin": coin, "history": hist})
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return

        if path == "/api/vault":
            vault = (qs.get("address") or [""])[0].strip()
            if not vault:
                self._json(400, {"error": "vault address required"})
                return
            try:
                self._json(200, vault_details(_get_info(), vault))
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return

        if path == "/api/candles":
            coin = (qs.get("coin") or ["BTC"])[0]
            interval = (qs.get("interval") or ["15m"])[0]
            try:
                info = _get_info()
                end = int(time.time() * 1000)
                start = end - 24 * 3600 * 1000
                candles = info.candles_snapshot(coin, interval, start, end)
                self._json(200, {"coin": coin, "interval": interval, "candles": candles})
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return

        # ── SSE live stream ──────────────────────────────────────────────
        if path == "/api/stream":
            hub: LiveHub = STATE["hub"]
            q = hub.add_client()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._cors()
            self.end_headers()
            try:
                # hello
                self.wfile.write(format_sse({"type": "hello", "live": hub.status()}))
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15.0)
                        self.wfile.write(format_sse(msg))
                        self.wfile.flush()
                    except queue.Empty:
                        # keepalive comment
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                hub.remove_client(q)
            return

        self.send_error(404, "Not found")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Hyperliquid liquid-glass terminal")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--testnet", action="store_true")
    parser.add_argument("--address", default="", help="Default address pre-filled in the UI")
    parser.add_argument("--whale-usd", type=float, default=75_000.0, help="Whale trade threshold (USD)")
    args = parser.parse_args(argv)

    STATE["base_url"] = constants.TESTNET_API_URL if args.testnet else constants.MAINNET_API_URL
    STATE["network"] = "testnet" if args.testnet else "mainnet"
    STATE["default_address"] = args.address or (load_address_from_config() or "")

    if not STATIC.is_dir():
        print(f"Static dir missing: {STATIC}", file=sys.stderr)
        return 1

    hub = LiveHub(STATE["base_url"], whale_usd=args.whale_usd)
    STATE["hub"] = hub
    print("Starting live WebSocket hub…")
    try:
        hub.start()
        print("  live hub ready:", hub.status())
    except Exception as exc:
        print(f"  live hub failed to start (REST still works): {exc}", file=sys.stderr)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print("Hyperliquid liquid-glass terminal")
    print(f"  network : {STATE['network']}")
    print(f"  default : {STATE['default_address'] or '(none)'}")
    print(f"  open    : {url}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        try:
            hub.stop()
        except Exception:
            pass
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
