"""BQL execution via Bloomberg bqnt-3 subprocess.

Fallback execution path when xbbg/polars-bloomberg are not installed.
Spawns Bloomberg's bundled Python interpreter to run BQL queries.
Works on every Bloomberg terminal with zero additional dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("bloomberg_mcp")

# Bloomberg bqnt-3 Python — standard path on every terminal
DEFAULT_BLOOMBERG_PYTHON = r"C:\blp\bqnt\environments\bqnt-3\python.exe"
BLOOMBERG_PYTHON = os.environ.get("BLOOMBERG_PYTHON", DEFAULT_BLOOMBERG_PYTHON)

# Inline worker script executed by the bqnt-3 subprocess
_WORKER_SCRIPT = r'''
import sys, os, json, warnings
_sd = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if os.path.abspath(p) != _sd]
warnings.filterwarnings("ignore", category=PendingDeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

if sys.platform == "win32":
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

import bql
import pandas as pd

def run_query(query):
    bq = bql.Service()
    response = bq.execute(query)
    df = bql.combined_df(response)
    if isinstance(df.index, pd.MultiIndex) or df.index.name:
        df = df.reset_index()
    # Collapse per-field expansion rows
    if "ID" in df.columns and "DATE" in df.columns and len(df) > 0:
        n_ids = df["ID"].nunique()
        has_nat = df["DATE"].isna().any()
        n_dates = df["DATE"].nunique()
        if has_nat and n_dates <= 2 and len(df) > n_ids:
            data_cols = [c for c in df.columns if c != "ID"]
            collapsed = df.groupby("ID", sort=False)[data_cols].first().reset_index()
            if len(collapsed) < len(df):
                df = collapsed
    elif "ID" in df.columns and len(df) > 0:
        data_cols = [c for c in df.columns if c != "ID"]
        collapsed = df.groupby("ID", sort=False)[data_cols].first().reset_index()
        if len(collapsed) < len(df):
            df = collapsed
    return json.loads(df.to_json(orient="records", date_format="iso", default_handler=str))

query = sys.argv[1]
try:
    results = run_query(query)
    print(json.dumps({"data": results, "count": len(results)}))
except Exception as e:
    error_msg = str(e)
    hints = []
    if "YIELD" in error_msg.upper():
        hints.append("Use yield_type=YTW (not type=ytw)")
    if "DURATION" in error_msg.upper():
        hints.append("Use duration_type=modified (not type=modified)")
    if "SPREAD" in error_msg.upper() and "type" in error_msg.lower():
        hints.append("Use spread_type=OAS (not type=oas)")
    if "BONDSUNIV" in error_msg.upper():
        hints.append("bondsuniv must be lowercase: bondsuniv(Active)")
    if "rating" in error_msg.lower() and "String Vectors" in error_msg:
        hints.append("Use rating(source=SP).source_scale <= 4, not >= 'AA-'")
    print(json.dumps({"error": error_msg, "hints": hints}))
    sys.exit(1)
'''


def _detect_bloomberg_python() -> str | None:
    """Return the bqnt-3 Python path if it exists."""
    path = BLOOMBERG_PYTHON
    if Path(path).exists():
        return path
    # Try alternate locations
    for alt in [
        r"C:\blp\bqnt\environments\bqnt-3\python.exe",
        r"C:\Bloomberg\bqnt\environments\bqnt-3\python.exe",
    ]:
        if Path(alt).exists():
            return alt
    return None


def is_available() -> bool:
    """Check if bqnt-3 subprocess execution is available."""
    return _detect_bloomberg_python() is not None


def execute_bql(query: str, timeout: int = 60) -> dict[str, Any]:
    """Execute a BQL query via bqnt-3 subprocess.

    Returns a dict with 'data' (list of records) and 'count',
    or raises an exception with error hints on failure.
    """
    python_path = _detect_bloomberg_python()
    if not python_path:
        raise RuntimeError(
            f"Bloomberg bqnt-3 Python not found at {BLOOMBERG_PYTHON}. "
            "Ensure Bloomberg Terminal is installed."
        )

    # Write the worker script to a temp file
    worker_file = Path(tempfile.gettempdir()) / "bloomberg_mcp_bql_worker.py"
    worker_file.write_text(_WORKER_SCRIPT, encoding="utf-8")

    try:
        result = subprocess.run(
            [python_path, str(worker_file), query],
            capture_output=True,
            text=True,
            timeout=timeout + 10,
            encoding="utf-8",
            errors="replace",
        )

        stdout = result.stdout.strip()
        if not stdout:
            stderr = result.stderr.strip()
            raise RuntimeError(f"bqnt-3 returned no output. stderr: {stderr}")

        # Parse the last JSON line (skip any warnings)
        lines = stdout.split("\n")
        json_line = lines[-1]
        parsed = json.loads(json_line)

        if "error" in parsed:
            hints = parsed.get("hints", [])
            hint_str = ""
            if hints:
                hint_str = "\n" + "\n".join(f"HINT: {h}" for h in hints)
            raise RuntimeError(f"BQL Error: {parsed['error']}{hint_str}")

        return {
            "data": parsed.get("data", []),
            "columns": list(parsed["data"][0].keys()) if parsed.get("data") else [],
            "shape": [parsed.get("count", 0), len(parsed["data"][0]) if parsed.get("data") else 0],
        }

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"BQL query timed out after {timeout}s")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse bqnt-3 output: {e}")
