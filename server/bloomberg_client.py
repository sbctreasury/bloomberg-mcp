"""Unified Bloomberg data access layer.

Uses xbbg as the single in-process Bloomberg backend for BDP, BDH, BDIB, BQL,
BDS, BSRCH, screening, field search, and bond analytics. xbbg (>=0.12.2) runs
BQL in-process via blpapi, so no separate BQNT environment is required.

All methods return JSON-serializable dicts via ``utils.serialize_dataframe``.

Adds:
- Lightweight circuit breaker (trip after N consecutive failures, cooldown
  for ``COOLDOWN_SEC`` seconds, half-open probe on the next call after
  cooldown).
- ``force_refresh`` to drop cached session references after the breaker
  trips or on explicit reset.
- Backend diagnostics so clients can see the exact xbbg version/options in use.
"""

from __future__ import annotations

import importlib.metadata
import logging
import threading
import time
from datetime import datetime
from typing import Any

from utils import serialize_dataframe

logger = logging.getLogger("bloomberg_mcp")


class BloombergUnavailable(RuntimeError):
    """Raised when Bloomberg is unavailable or the circuit breaker is open."""


class BloombergClient:
    """Single shared Bloomberg client instance created during server lifespan."""

    FAILURE_THRESHOLD = 3
    COOLDOWN_SEC = 30.0

    # xbbg 1.x changes its defaults. Keep the MCP JSON shape stable.
    XBBG_KWARGS = {"backend": "pandas", "format": "wide"}

    def __init__(self) -> None:
        self._blp = None
        self._has_xbbg: bool | None = None

        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._open_until = 0.0

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def _breaker_check(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._open_until > now:
                remaining = int(self._open_until - now)
                raise BloombergUnavailable(
                    "Bloomberg circuit breaker open after "
                    f"{self._consecutive_failures} consecutive failures. "
                    f"Retrying in {remaining}s. Use bloomberg_reset to force a probe."
                )

    def _record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._open_until = 0.0

    def _record_failure(self) -> None:
        with self._lock:
            self._record_failure_locked()

    def _record_failure_locked(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.FAILURE_THRESHOLD:
            self._open_until = time.monotonic() + self.COOLDOWN_SEC
            logger.warning(
                "Bloomberg circuit breaker tripped after %d failures; cooling down for %.0fs",
                self._consecutive_failures,
                self.COOLDOWN_SEC,
            )

    def record_timeout(self, operation: str = "Bloomberg call") -> None:
        """Record a caller-enforced timeout and drop cached session handles."""
        with self._lock:
            self._blp = None
            self._has_xbbg = None
            logger.warning("%s timed out; clearing cached Bloomberg handles", operation)
            self._record_failure_locked()

    def _call(self, fn, *args, **kwargs):
        self._breaker_check()
        try:
            result = fn(*args, **kwargs)
            self._record_success()
            return result
        except BloombergUnavailable:
            raise
        except Exception:
            self._record_failure()
            raise

    def force_refresh(self) -> dict[str, Any]:
        """Drop cached xbbg references and reset the breaker."""
        with self._lock:
            self._blp = None
            self._has_xbbg = None
            self._consecutive_failures = 0
            self._open_until = 0.0
        return {
            "reset": True,
            "message": "Cached xbbg session references dropped; breaker cleared.",
        }

    def breaker_state(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            return {
                "consecutive_failures": self._consecutive_failures,
                "open": self._open_until > now,
                "open_remaining_sec": max(0, int(self._open_until - now)),
            }

    def backend_state(self) -> dict[str, Any]:
        """Return the Bloomberg backend configuration currently in use."""
        try:
            xbbg_version = importlib.metadata.version("xbbg")
        except importlib.metadata.PackageNotFoundError:
            xbbg_version = None

        return {
            "primary_backend": "xbbg",
            "xbbg_available": self._check_xbbg(),
            "xbbg_version": xbbg_version,
            "xbbg_options": dict(self.XBBG_KWARGS),
        }

    def warmup(self) -> dict[str, Any]:
        """Prime the xbbg data path with a small BDP call.

        Running a tiny xbbg query here keeps the first user-facing quote request
        from paying the lazy import/session startup cost.
        """
        result = self.bdp(["IBM US Equity"], ["PX_LAST"])
        return {
            "ready": True,
            "backend": "xbbg",
            "probe": result,
        }

    # ------------------------------------------------------------------
    # Lazy xbbg import
    # ------------------------------------------------------------------

    @property
    def blp(self):
        if self._blp is None:
            try:
                from xbbg import blp
            except ImportError as exc:
                raise BloombergUnavailable(
                    "xbbg is required for Bloomberg MCP data calls. "
                    "Install dependencies with `uv sync` or "
                    "`pip install -r server/requirements.txt`."
                ) from exc
            self._blp = blp
        return self._blp

    def _check_xbbg(self) -> bool:
        if self._has_xbbg is None:
            try:
                from xbbg import blp  # noqa: F401
                self._has_xbbg = True
            except ImportError:
                self._has_xbbg = False
        return self._has_xbbg

    def _xbbg_kwargs(self, **kwargs: Any) -> dict[str, Any]:
        merged = dict(self.XBBG_KWARGS)
        merged.update(kwargs)
        return merged

    # ------------------------------------------------------------------
    # BDP - Reference / Snapshot Data
    # ------------------------------------------------------------------

    def bdp(
        self,
        securities: list[str],
        fields: list[str],
        overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        def _do():
            kwargs: dict[str, Any] = {}
            if overrides:
                kwargs.update(overrides)
            df = self.blp.bdp(
                tickers=securities,
                flds=fields,
                **self._xbbg_kwargs(**kwargs),
            )
            return serialize_dataframe(df)

        return self._call(_do)

    # ------------------------------------------------------------------
    # BDH - Historical Time Series
    # ------------------------------------------------------------------

    def bdh(
        self,
        securities: list[str],
        fields: list[str],
        start_date: str,
        end_date: str | None = None,
        periodicity: str | None = None,
        adjust: str | None = None,
    ) -> dict[str, Any]:
        def _do():
            _end = end_date or datetime.now().strftime("%Y-%m-%d")
            kwargs: dict[str, Any] = {}
            if periodicity:
                kwargs["Per"] = periodicity
            if adjust:
                kwargs["adjust"] = adjust
            df = self.blp.bdh(
                tickers=securities,
                flds=fields,
                start_date=start_date,
                end_date=_end,
                **self._xbbg_kwargs(**kwargs),
            )
            return serialize_dataframe(df)

        return self._call(_do)

    # ------------------------------------------------------------------
    # BDIB - Intraday Bars
    # ------------------------------------------------------------------

    def bdib(
        self,
        security: str,
        date: str,
        interval: int = 5,
        session: str = "allday",
    ) -> dict[str, Any]:
        def _do():
            df = self.blp.bdib(
                ticker=security,
                dt=date,
                interval=interval,
                session=session,
                **self._xbbg_kwargs(),
            )
            return serialize_dataframe(df)

        return self._call(_do)

    # ------------------------------------------------------------------
    # BQL - Bloomberg Query Language
    # ------------------------------------------------------------------

    def bql(self, query: str, timeout: int = 60) -> dict[str, Any]:
        """Execute a BQL query in-process via xbbg (blpapi BQL service)."""

        def _do():
            df = self.blp.bql(query, **self._xbbg_kwargs())
            return serialize_dataframe(df)

        return self._call(_do)

    # ------------------------------------------------------------------
    # Bond Info - Fixed Income Analytics
    # ------------------------------------------------------------------

    def bond_info(
        self,
        securities: list[str],
        include_risk: bool = True,
        include_spreads: bool = True,
    ) -> dict[str, Any]:
        def _do():
            base_fields = [
                "SECURITY_NAME",
                "COUPON",
                "MATURITY",
                "ISSUE_DT",
                "CRNCY",
                "AMT_OUTSTANDING",
                "PX_LAST",
                "YLD_YTM_MID",
            ]
            if include_risk:
                base_fields += ["DUR_ADJ_MID", "DUR_ADJ_OAS_MID", "CONVEXITY_MID", "DV01"]
            if include_spreads:
                base_fields += ["OAS_SPREAD_MID", "Z_SPRD_MID", "ASSET_SWAP_SPD_MID"]
            df = self.blp.bdp(
                tickers=securities,
                flds=base_fields,
                **self._xbbg_kwargs(),
            )
            return serialize_dataframe(df)

        return self._call(_do)

    # ------------------------------------------------------------------
    # Screening
    # ------------------------------------------------------------------

    def screen_eqs(self, screen_name: str) -> dict[str, Any]:
        def _do():
            df = self.blp.beqs(screen=screen_name, **self._xbbg_kwargs())
            return serialize_dataframe(df)

        return self._call(_do)

    def screen_bql(
        self,
        bql_filter: str,
        fields: list[str],
        max_results: int = 100,
    ) -> dict[str, Any]:
        fields_str = ", ".join(fields)
        query = f"get({fields_str}) for(filter(bondsuniv(Active), {bql_filter}))"
        result = self.bql(query)
        if result.get("data") and len(result["data"]) > max_results:
            result["data"] = result["data"][:max_results]
            result["shape"][0] = max_results
            result["truncated"] = True
        return result

    # ------------------------------------------------------------------
    # Field Search
    # ------------------------------------------------------------------

    def field_search(self, query: str, max_results: int = 20) -> list[dict[str, str]]:
        from utils import search_fields

        def _do():
            return search_fields(query, max_results=max_results)

        return self._call(_do)
