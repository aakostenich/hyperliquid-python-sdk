#!/usr/bin/env python3
"""Liquid-glass web UI for the Hyperliquid account dashboard.

  python examples/web_dashboard/server.py
  python examples/web_dashboard/server.py --port 8765 --testnet
  open http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(EXAMPLES))

from account_dashboard import (  # noqa: E402
    fetch_snapshot,
    load_address_from_config,
    snapshot_to_jsonable,
)
from hyperliquid.info import Info  # noqa: E402
from hyperliquid.utils import constants  # noqa: E402

# Simple in-memory cache: (address, network) -> (ts, payload)
_CACHE: Dict[Tuple[str, str], Tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 12.0  # seconds

STATE = {
    "base_url": constants.MAINNET_API_URL,
    "network": "mainnet",
    "default_address": "",
}


def _get_info() -> Info:
    # Fresh client per request keeps things simple / thread-safe enough
    return Info(STATE["base_url"], skip_ws=True)


def load_account(address: str, use_cache: bool = True) -> Dict[str, Any]:
    import time

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
    with _CACHE_LOCK:
        _CACHE[key] = (now, payload)
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "HLDashboard/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
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

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._file(STATIC / "index.html", "text/html; charset=utf-8")
            return

        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            # prevent path traversal
            target = (STATIC / rel).resolve()
            if not str(target).startswith(str(STATIC.resolve())):
                self.send_error(403)
                return
            self._file(target)
            return

        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "network": STATE["network"],
                    "default_address": STATE["default_address"],
                },
            )
            return

        if path == "/api/account":
            address = (qs.get("address") or [STATE["default_address"]])[0].strip()
            if not address:
                self._json(400, {"error": "Missing address. Pass ?address=0x... or set config.json"})
                return
            if not address.startswith("0x") or len(address) < 10:
                self._json(400, {"error": "Invalid address"})
                return
            refresh = (qs.get("refresh") or ["0"])[0] in ("1", "true", "yes")
            try:
                data = load_account(address, use_cache=not refresh)
                self._json(200, data)
            except Exception as exc:
                traceback.print_exc()
                self._json(500, {"error": str(exc)})
            return

        self.send_error(404, "Not found")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Hyperliquid liquid-glass dashboard server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--testnet", action="store_true")
    parser.add_argument("--address", default="", help="Default address pre-filled in the UI")
    args = parser.parse_args(argv)

    STATE["base_url"] = constants.TESTNET_API_URL if args.testnet else constants.MAINNET_API_URL
    STATE["network"] = "testnet" if args.testnet else "mainnet"
    STATE["default_address"] = args.address or (load_address_from_config() or "")

    if not STATIC.is_dir():
        print(f"Static dir missing: {STATIC}", file=sys.stderr)
        return 1

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"Hyperliquid liquid-glass dashboard")
    print(f"  network : {STATE['network']}")
    print(f"  default : {STATE['default_address'] or '(none)'}")
    print(f"  open    : {url}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
