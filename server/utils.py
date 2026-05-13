"""
Utility helpers for the Bloomberg MCP server.

- Bloomberg Terminal connectivity check (fast process-only check by default,
  optional deep API probe with bounded timeout)
- Identifier conversion (CUSIP/ISIN to ticker)
- DataFrame serialization
- Field search wrapper
"""

from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
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


_BDP_PROBE_SCRIPT = r"""
import json
import sys

try:
    from xbbg import blp

    df = blp.bdp("IBM US Equity", "PX_LAST")
    ok = df is not None and not df.empty
    print(json.dumps({"ok": bool(ok), "error": None if ok else "BDP returned empty"}))
    sys.exit(0 if ok else 2)
except Exception as exc:
    print(json.dumps({"ok": False, "error": f"BDP probe failed: {exc}"}))
    sys.exit(1)
"""

_BQL_PROBE_SCRIPT = r"""
import json
import sys

try:
    import bql

    bq = bql.Service()
    response = bq.execute("get(px_last) for(['IBM US Equity'])")
    df = bql.combined_df(response)
    ok = df is not None and not df.empty
    print(json.dumps({"ok": bool(ok), "error": None if ok else "BQL probe returned empty"}))
    sys.exit(0 if ok else 2)
except Exception as exc:
    print(json.dumps({"ok": False, "error": f"BQL probe failed: {exc}"}))
    sys.exit(1)
"""


def _probe_bdp(timeout: float) -> dict[str, Any]:
    """Run a Bloomberg API probe in a child process with a hard timeout.

    Returns {"ok": True} on success, or {"ok": False, "error": str} on
    timeout or any underlying exception. Never raises.
    """
    timeout = max(0.5, float(timeout))
    python_path = sys.executable
    script = _BDP_PROBE_SCRIPT
    backend = "BDP"

    if importlib.util.find_spec("xbbg") is None:
        try:
            from bql_subprocess import _detect_bloomberg_python

            bqnt_python = _detect_bloomberg_python()
        except Exception:
            bqnt_python = None

        if bqnt_python:
            python_path = bqnt_python
            script = _BQL_PROBE_SCRIPT
            backend = "BQL"

    try:
        result = subprocess.run(
            [python_path, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{backend} probe exceeded {timeout:g}s timeout"}
    except Exception as exc:
        return {"ok": False, "error": f"{backend} probe failed to start: {exc}"}

    stdout = result.stdout.strip()
    if stdout:
        try:
            parsed = json.loads(stdout.splitlines()[-1])
            if isinstance(parsed, dict):
                return {
                    "ok": bool(parsed.get("ok")),
                    "error": parsed.get("error") or None,
                }
        except json.JSONDecodeError:
            pass

    stderr = result.stderr.strip()
    detail = stderr or stdout or f"probe exited with code {result.returncode}"
    return {"ok": False, "error": detail}


def check_bloomberg_status(deep_check: bool = False, probe_timeout: float = 10.0) -> dict[str, Any]:
    """Check Bloomberg Terminal status.

    By default (``deep_check=False``) returns process-only state and never
    issues a live API call. With ``deep_check=True`` runs a bounded API probe
    against IBM PX_LAST to confirm session health. The probe runs in a child
    process so a hung Bloomberg session can be killed at ``probe_timeout``.
    """
    status = check_processes()
    status["api_connected"] = False

    if not status["terminal_running"]:
        status["error"] = status.get("error") or "No Bloomberg Terminal processes detected"
        return status

    if not deep_check:
        # Process is up; mark api as unknown rather than asserting it is up.
        status["api_connected"] = None
        return status

    probe = _probe_bdp(probe_timeout)
    status["api_connected"] = probe["ok"]
    if not probe["ok"]:
        status["error"] = probe.get("error")
    return status


def cusip_to_ticker(cusip: str) -> str:
    """Convert a CUSIP identifier to a Bloomberg ticker via BDP."""
    from xbbg import blp

    df = blp.bdp(f"/cusip/{cusip}", "PARSEKYABLE_DES")
    if df is not None and not df.empty:
        return str(df.iloc[0, 0])
    return f"/cusip/{cusip}"


def isin_to_ticker(isin: str) -> str:
    """Convert an ISIN identifier to a Bloomberg ticker via BDP."""
    from xbbg import blp

    df = blp.bdp(f"/isin/{isin}", "PARSEKYABLE_DES")
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

    df = blp.fieldSearch(query)
    if df is None or df.empty:
        return []

    df = df.head(max_results)
    return serialize_dataframe(df)["data"]
