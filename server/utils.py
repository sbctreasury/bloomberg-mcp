"""
Utility helpers for the Bloomberg MCP server.

- Bloomberg Terminal connectivity check (fast process-only check by default,
  optional deep API probe with bounded timeout)
- Identifier conversion (CUSIP/ISIN to ticker)
- DataFrame serialization
- Field search wrapper
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger("bloomberg_mcp")

BBG_PROCESS_NAMES = {"wintrv.exe", "bbcomm.exe", "bblauncher.exe", "bbg.exe"}


def check_processes() -> dict[str, Any]:
    """Process-only health check. Fast, never hangs.

    Returns terminal_running and the list of detected Bloomberg processes.
    Does NOT make any API call.
    """
    status: dict[str, Any] = {
        "terminal_running": False,
        "processes": [],
        "error": None,
    }
    try:
        import psutil

        for proc in psutil.process_iter(["name"]):
            name = (proc.info["name"] or "").lower()
            if name in BBG_PROCESS_NAMES:
                status["processes"].append(name)
        status["terminal_running"] = len(status["processes"]) > 0
    except ImportError:
        status["error"] = "psutil not installed"
    except Exception as exc:
        status["error"] = f"Process check failed: {exc}"
    return status


def check_bloomberg_status(deep_check: bool = False, probe_timeout: float = 10.0) -> dict[str, Any]:
    """Check Bloomberg Terminal status (process-only).

    API reachability is no longer probed here. The previous implementation
    spawned a *fresh child Python process* that imported xbbg and opened a cold
    ``blpapi`` session on every call — that cold handshake routinely exceeded
    20-30s even when the live, already-warm session served data instantly.

    ``api_connected`` is now derived by the caller from the in-process xbbg
    warmup (see ``bloomberg_status`` in server.py), which exercises the exact
    session real queries use. ``deep_check`` and ``probe_timeout`` are retained
    for backward compatibility but no longer spawn a separate probe.
    """
    status = check_processes()

    if not status["terminal_running"]:
        status["api_connected"] = False
        status["error"] = status.get("error") or "No Bloomberg Terminal processes detected"
        return status

    # Process is up. Reachability is confirmed in-process by the xbbg warmup,
    # so it is unknown at this layer.
    status["api_connected"] = None
    return status


def cusip_to_ticker(cusip: str) -> str:
    """Convert a CUSIP identifier to a Bloomberg ticker via BDP."""
    from xbbg import blp

    df = blp.bdp(f"/cusip/{cusip}", "PARSEKYABLE_DES", backend="pandas", format="wide")
    if df is not None and not df.empty:
        return str(df.iloc[0, 0])
    return f"/cusip/{cusip}"


def isin_to_ticker(isin: str) -> str:
    """Convert an ISIN identifier to a Bloomberg ticker via BDP."""
    from xbbg import blp

    df = blp.bdp(f"/isin/{isin}", "PARSEKYABLE_DES", backend="pandas", format="wide")
    if df is not None and not df.empty:
        return str(df.iloc[0, 0])
    return f"/isin/{isin}"


class _JSONEncoder(json.JSONEncoder):
    """Handle dates, datetimes, and other non-JSON-native types."""

    def default(self, o: Any) -> Any:
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        try:
            import numpy as np

            if isinstance(o, (np.integer,)):
                return int(o)
            if isinstance(o, (np.floating,)):
                return float(o)
            if isinstance(o, np.ndarray):
                return o.tolist()
        except ImportError:
            pass
        return super().default(o)


def serialize_dataframe(df: Any) -> dict[str, Any]:
    """Serialize a pandas or polars DataFrame to a JSON-ready dict."""
    try:
        import polars as pl

        if isinstance(df, pl.DataFrame):
            records = df.to_dicts()
            clean = json.loads(json.dumps(records, cls=_JSONEncoder))
            return {
                "data": clean,
                "columns": df.columns,
                "shape": list(df.shape),
            }
    except ImportError:
        pass

    try:
        import pandas as pd

        if isinstance(df, pd.DataFrame):
            if isinstance(df.columns, pd.MultiIndex):
                df = df.copy()
                df.columns = [
                    "|".join(str(c) for c in col).strip("|")
                    for col in df.columns.values
                ]

            if df.index.name or not isinstance(df.index, pd.RangeIndex):
                df = df.reset_index()

            records = json.loads(
                df.to_json(orient="records", date_format="iso", default_handler=str)
            )
            return {
                "data": records,
                "columns": list(df.columns),
                "shape": list(df.shape),
            }
    except ImportError:
        pass

    return {"data": [], "columns": [], "shape": [0, 0], "error": "Unsupported type"}


def search_fields(query: str, max_results: int = 20) -> list[dict[str, str]]:
    """Search Bloomberg field mnemonics via ``blp.fieldSearch``."""
    from xbbg import blp

    if hasattr(blp, "bfld"):
        df = blp.bfld(search_spec=query, backend="pandas")
    else:
        df = blp.fieldSearch(query, backend="pandas", format="wide")
    if df is None or df.empty:
        return []

    df = df.head(max_results)
    return serialize_dataframe(df)["data"]
