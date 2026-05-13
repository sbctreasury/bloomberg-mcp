"""
Unified Bloomberg data access layer.

Wraps xbbg and polars-bloomberg behind a single ``BloombergClient`` class.
Falls back to bqnt-3 subprocess for BQL when neither library is available.
All methods return JSON-serializable dicts via ``utils.serialize_dataframe``.

Adds:
- Lightweight circuit breaker (trip after N consecutive failures, cooldown
  for ``COOLDOWN_SEC`` seconds, half-open probe on the next call after
  cooldown).
- ``force_refresh`` to drop cached session references after the breaker
  trips or on explicit reset.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

from utils import serialize_dataframe

logger = logging.getLogger("bloomberg_mcp")


class BloombergUnavailable(RuntimeError):
    """Raised when the circuit breaker is open."""


class BloombergClient:
    """Single shared instance created during server lifespan."""

    # Circuit breaker thresholds. Tunable via env if needed.
    FAILURE_THRESHOLD = 3
    COOLDOWN_SEC = 30.0

    def __init__(self) -> None:
        self._blp = None  # lazy xbbg.blp
        self._bquery_cls = None  # lazy polars_bloomberg.BQuery
        self._has_xbbg: bool | None = None
        self._has_polars_bbg: bool | None = None

        # Circuit breaker state.
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._open_until = 0.0  # monotonic seconds

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def _breaker_check(self) -> None:
        """Raise BloombergUnavailable if the circuit is open."""
        with self._lock:
            now = time.monotonic()
            if self._open_until > now:
                remaining = int(self._open_until - now)
                raise BloombergUnavailable(
                    f"Bloomberg circuit breaker open after "
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
                "Bloomberg circuit breaker tripped after %d failures; "
                "cooling down for %.0fs",
                self._consecutive_failures,
                self.COOLDOWN_SEC,
            )

    def record_timeout(self, operation: str = "Bloomberg call") -> None:
        """Record a caller-enforced timeout and drop cached session handles."""
        with self._lock:
            self._blp = None
            self._bquery_cls = None
            self._has_xbbg = None
            self._has_polars_bbg = None
            logger.warning("%s timed out; clearing cached Bloomberg handles", operation)
            self._record_failure_locked()

    def _call(self, fn, *args, **kwargs):
        """Run a Bloomberg call through the breaker."""
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
        """Drop cached library references and reset the breaker.

        Call this after the Bloomberg Terminal has been restarted or a
        long-running session has gone stale. The next data call will
        re-import xbbg / polars-bloomberg lazily.
        """
        with self._lock:
            self._blp = None
            self._bquery_cls = None
            self._has_xbbg = None
            self._has_polars_bbg = None
            self._consecutive_failures = 0
            self._open_until = 0.0
        return {
            "reset": True,
            "message": "Cached session references dropped; breaker cleared.",
        }

    def breaker_state(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            return {
                "consecutive_failures": self._consecutive_failures,
                "open": self._open_until > now,
                "open_remaining_sec": max(0, int(self._open_until - now)),
            }

    # ------------------------------------------------------------------
    # Lazy imports
    # ------------------------------------------------------------------

    @property
    def blp(self):
        if self._blp is None:
            from xbbg import blp

            self._blp = blp
        return self._blp

    @property
    def bquery_cls(self):
        if self._bquery_cls is None:
            from polars_bloomberg import BQuery

            self._bquery_cls = BQuery
        return self._bquery_cls

    def _check_xbbg(self) -> bool:
        if self._has_xbbg is None:
            try:
                from xbbg import blp  # noqa: F401
                self._has_xbbg = True
            except ImportError:
                self._has_xbbg = False
        return self._has_xbbg

    def _check_polars_bbg(self) -> bool:
        if self._has_polars_bbg is None:
            try:
                from polars_bloomberg import BQuery  # noqa: F401
                self._has_polars_bbg = True
            except ImportError:
                self._has_polars_bbg = False
        return self._has_polars_bbg

    # ------------------------------------------------------------------
    # BDP — Reference / Snapshot Data
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
            df = self.blp.bdp(tickers=securities, flds=fields, **kwargs)
            return serialize_dataframe(df)
        return self._call(_do)

    # ------------------------------------------------------------------
    # BDH — Historical Time Series
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
                **kwargs,
            )
            return serialize_dataframe(df)
        return self._call(_do)

    # ------------------------------------------------------------------
    # BDIB — Intraday Bars
    # ------------------------------------------------------------------

    def bdib(
        self,
        security: str,
        date: str,
        interval: int = 5,
        session: str = "allday",
    ) -> dict[str, Any]:
        def _do():
            df = self.blp.bdib(ticker=security, dt=date, interval=interval, session=session)
            return serialize_dataframe(df)
        return self._call(_do)

    # ------------------------------------------------------------------
    # BQL — Bloomberg Query Language (3-tier fallback)
    # ------------------------------------------------------------------

    def bql(self, query: str, timeout: int = 60) -> dict[str, Any]:
        """Execute a BQL query under the circuit breaker.

        Execution priority:
        1. polars-bloomberg (fastest, Polars DataFrames)
        2. xbbg (widely used, pandas DataFrames)
        3. bqnt-3 subprocess (zero-dependency fallback, has its own hard timeout)

        The breaker counts an overall failure only if no tier succeeds.
        Individual tier failures are still logged as warnings.
        """
        def _do():
            # Tier 1: polars-bloomberg
            if self._check_polars_bbg():
                try:
                    bq_cls = self.bquery_cls
                    with bq_cls() as bq:
                        results = bq.bql(query)
                        df = results.combine()
                        return serialize_dataframe(df)
                except ImportError:
                    self._has_polars_bbg = False
                except Exception as exc:
                    logger.warning("polars-bloomberg BQL failed (%s), trying next tier", exc)

            # Tier 2: xbbg
            if self._check_xbbg():
                try:
                    df = self.blp.bql(query)
                    return serialize_dataframe(df)
                except ImportError:
                    self._has_xbbg = False
                except Exception as exc:
                    logger.warning("xbbg BQL failed (%s), trying bqnt-3 subprocess", exc)

            # Tier 3: bqnt-3 subprocess (hard subprocess timeout, cannot hang)
            from bql_subprocess import execute_bql, is_available

            if not is_available():
                raise RuntimeError(
                    "No BQL execution backend available. Install polars-bloomberg or xbbg, "
                    "or ensure Bloomberg bqnt-3 Python is at the expected path."
                )
            return execute_bql(query, timeout=timeout)
        return self._call(_do)

    # ------------------------------------------------------------------
    # Bond Info — Fixed Income Analytics
    # ------------------------------------------------------------------

    def bond_info(
        self,
        securities: list[str],
        include_risk: bool = True,
        include_spreads: bool = True,
    ) -> dict[str, Any]:
        """Fetch bond reference data plus optional risk/spread analytics."""
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
            df = self.blp.bdp(tickers=securities, flds=base_fields)
            return serialize_dataframe(df)
        return self._call(_do)

    # ------------------------------------------------------------------
    # Screening
    # ------------------------------------------------------------------

    def screen_eqs(self, screen_name: str) -> dict[str, Any]:
        """Run a saved Bloomberg equity screen (BEQS)."""
        def _do():
            df = self.blp.beqs(screen=screen_name)
            return serialize_dataframe(df)
        return self._call(_do)

    def screen_bql(
        self,
        bql_filter: str,
        fields: list[str],
        max_results: int = 100,
    ) -> dict[str, Any]:
        """Ad-hoc screening via BQL filter expression."""
        fields_str = ", ".join(fields)
        query = f"get({fields_str}) for(filter(bondsUniv('active'), {bql_filter}))"
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
        """Search Bloomberg field mnemonics."""
        from utils import search_fields

        def _do():
            return search_fields(query, max_results=max_results)
        return self._call(_do)
