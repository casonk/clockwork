"""Per-ticker position and orders for the Tradility detail view.

Orders the brokers report (Schwab, Binance.US) are read from Tradility's read-only export
(``CLOCKWORK_TRADILITY_ORDERS_EXPORT``, default the sibling tradility checkout's
``exports/tradility-orders.json``) and shown beside the hand-entered ones, marked by source and
never editable here. They do not feed the fallback average entry, which would be wrong over the
export's limited look-back.

The average entry price comes from the finance pipeline's holdings export (total cost basis over
total quantity, summed across accounts that report one); a ticker with no cost basis there falls
back to the average price of its recorded filled buys. Order history and open orders have no
export to read, so they are recorded here by hand in ``config/tradility-orders.json`` (personal
financial data: gitignored, ``CLOCKWORK_TRADILITY_ORDERS_FILE`` moves it).
"""

from __future__ import annotations

import datetime
import json
import math
import os
import re
import secrets
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, current_app, jsonify, request

SIDES = ("buy", "sell")
KINDS = ("market", "limit", "stop", "stop_limit")
STATUSES = ("open", "filled", "cancelled")
TICKER = re.compile(r"[A-Z0-9][A-Z0-9.^=-]{0,19}")
MAX_ORDERS_PER_TICKER = 500
MAX_NOTE = 120
_LOCK = threading.Lock()

bp = Blueprint("tradility_orders", __name__)


def default_file() -> Path:
    base = Path(__file__).resolve().parent.parent
    return Path(
        os.environ.get("CLOCKWORK_TRADILITY_ORDERS_FILE", base / "config" / "tradility-orders.json")
    )


def holdings_file() -> Path:
    """The holdings export; app.py configures the path once and registers a getter for it."""
    return Path(current_app.config["TRADILITY_HOLDINGS_FILE_FN"]())


def export_file() -> Path:
    base = Path(__file__).resolve().parent.parent
    return Path(
        os.environ.get(
            "CLOCKWORK_TRADILITY_ORDERS_EXPORT",
            base.parent / "tradility" / "exports" / "tradility-orders.json",
        )
    )


def load_export(path: Path | None = None) -> dict[str, Any]:
    """Tradility's broker-order export, or an empty one when it is missing or unreadable."""
    try:
        raw = json.loads((path or export_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def synced_for(ticker: str, export: dict[str, Any]) -> list[dict[str, Any]]:
    """The export's orders for *ticker*; crypto is `BTC-USD` here and may be `BTC` there."""
    found: list[dict[str, Any]] = []
    for key, rows in (export.get("orders") or {}).items():
        if _holding_matches(ticker, key) and isinstance(rows, list):
            found.extend(r for r in rows if isinstance(r, dict) and r.get("source"))
    return found


def load(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    path = path or default_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    orders = raw.get("orders") if isinstance(raw, dict) else None
    return orders if isinstance(orders, dict) else {}


def save(orders: dict[str, list[dict[str, Any]]], path: Path | None = None) -> None:
    path = path or default_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tradility-orders-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "orders": orders}, handle, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _number(value: Any, what: str, *, positive: bool = True) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be a number") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{what} must be a positive number")
    return number


def clean_order(raw: Any) -> dict[str, Any]:
    """A validated order from request data; ValueError names the first problem."""
    if not isinstance(raw, dict):
        raise ValueError("an order is an object")
    side, kind, status = raw.get("side"), raw.get("kind", "limit"), raw.get("status", "open")
    if side not in SIDES:
        raise ValueError("side must be buy or sell")
    if kind not in KINDS:
        raise ValueError("kind must be market, limit, stop or stop_limit")
    if status not in STATUSES:
        raise ValueError("status must be open, filled or cancelled")
    order: dict[str, Any] = {
        "side": side,
        "kind": kind,
        "status": status,
        "quantity": _number(raw.get("quantity"), "quantity"),
    }
    price = raw.get("price")
    if price in (None, ""):
        if kind != "market" or status == "filled":
            raise ValueError("price is required (only an open or cancelled market order has none)")
        order["price"] = None
    else:
        order["price"] = _number(price, "price")
    date = str(raw.get("date") or datetime.date.today().isoformat())
    try:
        datetime.date.fromisoformat(date)
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc
    order["date"] = date
    note = str(raw.get("note") or "").strip()
    if len(note) > MAX_NOTE or any(ord(c) < 32 for c in note):
        raise ValueError(f"note must be plain text of at most {MAX_NOTE} characters")
    order["note"] = note
    return order


def _holding_matches(ticker: str, holding_ticker: str) -> bool:
    """Crypto is `BTC` in the holdings and `BTC-USD` in the analysis."""
    held = str(holding_ticker).strip().upper()
    return held == ticker or f"{held}-USD" == ticker or held == ticker.removesuffix("-USD")


def position_from_holdings(ticker: str, holdings: list[Any]) -> dict[str, Any] | None:
    """Quantity and average entry for *ticker*, or None when it is not held."""
    quantity = cost = 0.0
    held = priced = False
    for holding in holdings:
        if not isinstance(holding, dict) or not _holding_matches(ticker, holding.get("ticker", "")):
            continue
        try:
            qty = float(holding.get("total_quantity"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(qty) or qty <= 0:
            continue
        held = True
        quantity += qty
        try:
            basis = float(holding.get("total_cost_basis"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(basis) and basis > 0:
            priced = True
            cost += basis
    if not held:
        return None
    # A partial cost basis would understate the average, so use it only when every lot has one.
    complete = priced and all(
        _basis_known(h) for h in holdings if _holding_matches(ticker, h.get("ticker", ""))
    )
    return {
        "quantity": quantity,
        "avg_entry": cost / quantity if complete and quantity else None,
        "source": "holdings",
    }


def _basis_known(holding: dict[str, Any]) -> bool:
    try:
        return (
            math.isfinite(float(holding.get("total_cost_basis")))
            and float(holding.get("total_cost_basis")) > 0
        )
    except (TypeError, ValueError):
        return False


def entry_from_orders(orders: list[dict[str, Any]]) -> float | None:
    """The average price of the recorded filled buys (quantity weighted)."""
    buys = [o for o in orders if o.get("side") == "buy" and o.get("status") == "filled"]
    quantity = sum(o["quantity"] for o in buys)
    if not quantity:
        return None
    return sum(o["quantity"] * o["price"] for o in buys) / quantity


def summary(
    ticker: str,
    orders: dict[str, list[dict[str, Any]]],
    holdings: list[Any],
    export: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mine = orders.get(ticker, [])
    synced = synced_for(ticker, export or {})
    position = position_from_holdings(ticker, holdings) or {
        "quantity": None,
        "avg_entry": None,
        "source": None,
    }
    if position["avg_entry"] is None:
        fallback = entry_from_orders(mine)
        if fallback is not None:
            position = {**position, "avg_entry": fallback, "source": "orders"}
    shown = [{**o, "source": "manual"} for o in mine] + synced
    sources = (export or {}).get("sources") or {}
    return {
        "ok": True,
        "ticker": ticker,
        "position": position,
        "orders": sorted(
            sorted(shown, key=lambda o: str(o.get("date", "")), reverse=True),
            key=lambda o: o["status"] != "open",
        ),  # open orders first, then history newest first
        "synced": {
            "generated_at": (export or {}).get("generated_at"),
            "sources": {name: bool(info.get("ok")) for name, info in sources.items()},
        },
    }


def _holdings_list() -> list[Any]:
    try:
        payload = json.loads(holdings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    held = payload.get("holdings") if isinstance(payload, dict) else None
    return held if isinstance(held, list) else []


def _ticker(value: str) -> str:
    ticker = value.strip().upper()
    if not TICKER.fullmatch(ticker):
        abort(404)
    return ticker


@bp.get("/api/tradility-orders/<path:ticker>")
def get_orders(ticker: str):
    ticker = _ticker(ticker)
    with _LOCK:
        return jsonify(summary(ticker, load(), _holdings_list(), load_export()))


@bp.post("/api/tradility-orders/<path:ticker>")
def add_order(ticker: str):
    ticker = _ticker(ticker)
    try:
        order = clean_order(request.get_json(silent=True))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    with _LOCK:
        orders = load()
        mine = orders.setdefault(ticker, [])
        if len(mine) >= MAX_ORDERS_PER_TICKER:
            return jsonify({"ok": False, "error": "too many orders for this ticker"}), 400
        mine.append({"id": secrets.token_hex(6), **order})
        save(orders)
        return jsonify(summary(ticker, orders, _holdings_list(), load_export())), 201


@bp.put("/api/tradility-orders/<ticker>/<order_id>")
def update_order(ticker: str, order_id: str):
    ticker = _ticker(ticker)
    try:
        order = clean_order(request.get_json(silent=True))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    with _LOCK:
        orders = load()
        for index, existing in enumerate(orders.get(ticker, [])):
            if existing.get("id") == order_id:
                orders[ticker][index] = {"id": order_id, **order}
                save(orders)
                return jsonify(summary(ticker, orders, _holdings_list(), load_export()))
    return jsonify({"ok": False, "error": "order not found"}), 404


@bp.delete("/api/tradility-orders/<ticker>/<order_id>")
def delete_order(ticker: str, order_id: str):
    ticker = _ticker(ticker)
    with _LOCK:
        orders = load()
        mine = orders.get(ticker, [])
        kept = [o for o in mine if o.get("id") != order_id]
        if len(kept) == len(mine):
            return jsonify({"ok": False, "error": "order not found"}), 404
        if kept:
            orders[ticker] = kept
        else:
            orders.pop(ticker)
        save(orders)
        return jsonify(summary(ticker, orders, _holdings_list(), load_export()))


# -- placing orders -----------------------------------------------------------------------------
# The web app never holds broker credentials or decides what may be traded. It forwards each step to
# Tradility's local trader service (127.0.0.1 only), which applies the owner-only policy, validates
# the order with the broker without placing it, and sends it only after the typed confirmation.

TRADER_ACTIONS = ("preview", "confirm", "cancel")


def trader_url() -> str:
    return os.environ.get("CLOCKWORK_TRADILITY_TRADER_URL", "http://127.0.0.1:8768").rstrip("/")


def call_trader(
    method: str, path: str, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    """One request to the trader service; (503, ...) when it is not running."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{trader_url()}{path}",
        data=data,
        method=method,
        headers={"X-Tradility-Trader": "1", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except ValueError:
            return exc.code, {"ok": False, "error": f"trader service error ({exc.code})"}
    except (urllib.error.URLError, OSError, ValueError):
        return 503, {"ok": False, "error": "the trader service is not running"}


@bp.get("/api/tradility-trade/status")
def trade_status():
    """Whether trading is available here, and the limits the policy sets (never any credential)."""
    code, payload = call_trader("GET", "/status")
    if code != 200 or not payload.get("ok"):
        return jsonify({"ok": True, "available": False, "enabled": False})
    return jsonify({**payload, "available": True})


@bp.post("/api/tradility-trade/<action>")
def trade_step(action: str):
    if action not in TRADER_ACTIONS:
        abort(404)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "send a JSON object"}), 400
    code, payload = call_trader("POST", f"/{action}", body)
    return jsonify(payload), code
