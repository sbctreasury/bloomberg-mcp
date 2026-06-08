"""
Bloomberg MCP Server — unified, self-documenting FastMCP implementation.

Uses xbbg as the single in-process Bloomberg backend for BDP, BDH, BDIB,
BQL, BDS, BSRCH, bond analytics, screening, and field search. xbbg runs BQL
in-process via blpapi — no separate BQNT environment required.

Exposes 12 tools + MCP resources for BQL reference documentation.
Any MCP client (Claude Code, Cursor, VS Code, custom agents) can use
this server without a separate skill file — it's fully self-documenting.

Run through the repository launcher:
    python launcher.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator

# Ensure local modules are importable
sys.path.insert(0, str(Path(__file__).parent))

from fastmcp import FastMCP, Context

from bloomberg_client import BloombergClient, BloombergUnavailable
from bql_builder import build_bql_from_intent, validate_bql
from utils import check_bloomberg_status

logger = logging.getLogger("bloomberg_mcp")
logging.basicConfig(level=logging.INFO)

# Default per-tool timeouts. Tools accept an override.
DEFAULT_TIMEOUT_SEC = {
    "status": 12,   # process check is instant; deep probe gets its own bound
    "bdp": 25,
    "bdh": 45,
    "bdib": 30,
    "bql": 60,
    "bond_info": 30,
    "screen": 60,
    "field_search": 15,
}


async def _run_bounded(
    fn: Callable[..., Any],
    *args,
    timeout: float,
    label: str,
    on_timeout: Callable[[], None] | None = None,
) -> Any:
    """Run a blocking Bloomberg call with a hard timeout.

    Returns the function result on success, or a structured error dict on
    timeout, breaker-open, or any underlying exception. Never raises.

    Bloomberg's APIs can block inside native/session code that Python cannot
    interrupt. Run each call on a short-lived daemon thread so an MCP request
    can return a timeout response without waiting for the stuck worker.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()

    def _complete(result: Any = None, exc: Exception | None = None) -> None:
        if future.done():
            return
        if exc is not None:
            future.set_exception(exc)
        else:
            future.set_result(result)

    def _worker() -> None:
        try:
            result = fn(*args)
        except Exception as exc:
            try:
                loop.call_soon_threadsafe(_complete, None, exc)
            except RuntimeError:
                pass
        else:
            try:
                loop.call_soon_threadsafe(_complete, result, None)
            except RuntimeError:
                pass

    thread = threading.Thread(
        target=_worker,
        name=f"bloomberg-mcp-{label}",
        daemon=True,
    )
    thread.start()

    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("%s timed out after %.1fs", label, timeout)
        timeout_text = f"{timeout:g}s"
        if on_timeout is not None:
            try:
                on_timeout()
            except Exception:
                logger.exception("Timeout handler failed for %s", label)
        return _error_response(
            f"{label} exceeded {timeout_text} timeout",
            "timeout_error",
            "Bloomberg Terminal may be unresponsive. Try bloomberg_status, "
            "then bloomberg_reset if processes are running but calls hang.",
        )
    except BloombergUnavailable as exc:
        return _error_response(str(exc), "circuit_open")
    except Exception as exc:
        return _error_response(str(exc), "bloomberg_error", "Check BQL syntax and Bloomberg connectivity")

# Paths
SERVER_DIR = Path(__file__).parent
PROJECT_DIR = SERVER_DIR.parent
REFERENCES_DIR = PROJECT_DIR / "references"
TESTS_DIR = PROJECT_DIR / "tests"


# ======================================================================
# Reference documentation index
# ======================================================================

# Maps domain names to reference file paths (relative to references/)
REFERENCE_MAP: dict[str, list[str]] = {
    "equity": ["equity/market.md", "equity/fundamental.md"],
    "fixed-income": ["fixed-income/bonds.md", "fixed-income/universe-screening.md"],
    "bonds": ["fixed-income/bonds.md"],
    "portfolio": ["fixed-income/portfolio.md"],
    "structured": ["fixed-income/structured.md"],
    "credit": ["credit/ratings.md", "credit/cds.md", "credit/issuance.md"],
    "ratings": ["credit/ratings.md"],
    "cds": ["credit/cds.md"],
    "issuance": ["credit/issuance.md"],
    "returns": ["returns/returns.md"],
    "curves": ["curves/curves.md"],
    "funds": ["funds/funds.md"],
    "functions": ["functions/functions.md"],
    "securitized": ["securitized/cmbs-analytics.md", "securitized/trades-spreads.md"],
    "field-search": ["field-search.md"],
}

# Domain examples — verified against live Bloomberg
BQL_EXAMPLES: dict[str, list[str]] = {
    "equity": [
        "get(px_last, pe_ratio, cur_mkt_cap) for('AAPL US Equity')",
        "get(name, px_last, pe_ratio) for(top(members('INDU Index'), 10, cur_mkt_cap))",
        "get(px_last(dates=range(-30D,0D))) for('AAPL US Equity')",
        "get(is_eps(fa_period_type=A, fa_period_offset=range(-4,2))) for('AAPL US Equity')",
        "get(groupavg(pe_ratio, gics_sector_name)) for(members('SPX Index'))",
        "get(name, px_last, pe_ratio, cur_mkt_cap/1B) for(filter(members('SPX Index'), pe_ratio<20 and cur_mkt_cap>10B))",
    ],
    "fixed-income": [
        "get(name, cpn, maturity, yield(yield_type=YTW), duration(duration_type=modified), spread(spread_type=OAS)) for('EH469710 Corp')",
        "get(name, cpn, maturity) for(bonds('AAPL US Equity'))",
        "get(name, yield(yield_type=YTW), rating(source=SP)) for(top(bondsuniv(Active), 10, cpn))",
        "get(name, cpn, maturity, yield(yield_type=YTW), duration(duration_type=modified), rating(source=SP)) for(filter(bondsuniv(Active), crncy == 'USD' and duration(duration_type=modified) < 5 and rating(source=SP).source_scale <= 10))",
    ],
    "credit": [
        "get(rating(source=SP)) for('EH469710 Corp')",
        "get(rating(source=MOODY)) for('EH469710 Corp')",
        "get(rating(source=BBG)) for('EH469710 Corp')",
    ],
    "cds": [
        "get(cds_spread) for(cds('JPM US Equity', tenor=5Y))",
        "get(cds_spread(dates=range(-30D,0D))) for(cds('JPM US Equity', tenor=5Y))",
    ],
    "curves": [
        "get(id) for(curveMembers(['YCGT0025 Index']))",
        "get(id().tenor, rate(side=Mid).value) for(curveMembers(['YCGT0025 Index'], tenors='5Y'))",
    ],
    "funds": [
        "get(count(group(id, fund_typ))) for(fundsUniv(['Primary','Active']))",
    ],
    "returns": [
        "get(total_return(calc_interval=range(-1M,0D))) for('AAPL US Equity')",
        "get(return_series(calc_interval=range(-1M,0D), per=W)) for('AAPL US Equity')",
    ],
}


# ======================================================================
# Lifespan — shared BloombergClient instance
# ======================================================================

@asynccontextmanager
async def lifespan(server: FastMCP):
    """Create a shared BloombergClient for the server lifetime."""
    client = BloombergClient()
    yield {"bloomberg": client}


mcp = FastMCP(
    "Bloomberg",
    instructions=(
        "Bloomberg Terminal data access via xbbg: BDP, BDH, BDIB, BQL queries, "
        "bond analytics, screening, field search, and comprehensive "
        "BQL reference documentation. "
        "For charting: write a Python script using matplotlib/plotly to "
        "visualize data returned by the tools, or render charts inline "
        "if the client supports it. "
        "Use bloomberg_bql_reference to look up "
        "correct syntax before writing BQL queries. "
        "CRITICAL BQL SYNTAX RULES: "
        "1) Use yield_type=YTW (not type=ytw), duration_type=modified, spread_type=OAS. "
        "2) bondsuniv must be lowercase: bondsuniv(Active). "
        "3) Rating filters use .source_scale numeric: rating(source=SP).source_scale <= 4 "
        "(1=AAA, 2=AA+, 3=AA, 4=AA-, 5=A+, 6=A, 7=A-, 8=BBB+, 9=BBB, 10=BBB-). "
        "4) Tickers in for() must be in brackets: for(['AAPL US Equity']). "
        "5) Use == for comparisons (not =)."
    ),
    lifespan=lifespan,
)


# ======================================================================
# MCP Resources — BQL Reference Documentation
# ======================================================================

@mcp.resource("bloomberg://references/index")
def get_reference_index() -> str:
    """BQL reference navigation index — read this first to find the right reference."""
    index_path = REFERENCES_DIR / "_tree-index.md"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return "Reference index not found."


@mcp.resource("bloomberg://references/{domain}/{filename}")
def get_reference_file(domain: str, filename: str) -> str:
    """Read a specific BQL reference file by domain and filename."""
    file_path = REFERENCES_DIR / domain / filename
    if file_path.exists() and file_path.suffix == ".md":
        return file_path.read_text(encoding="utf-8")
    return f"Reference file not found: {domain}/{filename}"


@mcp.resource("bloomberg://references/{filename}")
def get_top_level_reference(filename: str) -> str:
    """Read a top-level reference file (field-search.md, _tree-index.md)."""
    file_path = REFERENCES_DIR / filename
    if file_path.exists() and file_path.suffix == ".md":
        return file_path.read_text(encoding="utf-8")
    return f"Reference file not found: {filename}"


@mcp.resource("bloomberg://tests/{filename}")
def get_test_query(filename: str) -> str:
    """Read a verified BQL test query file."""
    file_path = TESTS_DIR / filename
    if file_path.exists() and file_path.suffix == ".bql":
        return file_path.read_text(encoding="utf-8")
    return f"Test file not found: {filename}"


# ======================================================================
# Helpers
# ======================================================================

def _error_response(error: str, error_type: str = "error", suggestion: str | None = None) -> dict[str, Any]:
    resp: dict[str, Any] = {"error": error, "type": error_type}
    if suggestion:
        resp["suggestion"] = suggestion
    return resp


def _get_client(ctx: Context) -> BloombergClient:
    return ctx.lifespan_context["bloomberg"]


# ======================================================================
# Pydantic input models
# ======================================================================

class BdpInput(BaseModel):
    securities: list[str] = Field(..., min_length=1, description="Bloomberg tickers, e.g. ['AAPL US Equity']")
    fields: list[str] = Field(..., min_length=1, description="Bloomberg field mnemonics, e.g. ['PX_LAST']")
    overrides: dict[str, str] | None = Field(None, description="Optional field overrides")
    timeout: int = Field(DEFAULT_TIMEOUT_SEC["bdp"], ge=3, le=120, description="Hard timeout in seconds")


class BdhInput(BaseModel):
    securities: list[str] = Field(..., min_length=1)
    fields: list[str] = Field(..., min_length=1)
    start_date: str = Field(..., description="Start date YYYY-MM-DD")
    end_date: str | None = Field(None, description="End date YYYY-MM-DD (default: today)")
    periodicity: str | None = Field(None, description="D, W, M, Q, Y")
    adjust: str | None = Field(None, description="Adjustment: all, split, etc.")
    timeout: int = Field(DEFAULT_TIMEOUT_SEC["bdh"], ge=3, le=180, description="Hard timeout in seconds")

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _validate_date(cls, v):
        if v is None:
            return v
        v = str(v).strip()
        if len(v) == 10 and v[4] == "-" and v[7] == "-":
            return v
        raise ValueError(f"Date must be YYYY-MM-DD, got: {v}")


class BdibInput(BaseModel):
    security: str = Field(..., description="Single Bloomberg ticker")
    date: str = Field(..., description="Trading date YYYY-MM-DD")
    interval: int = Field(5, ge=1, le=1440, description="Bar interval in minutes")
    session: str = Field("allday", description="Session: allday, day, am, pm, etc.")
    timeout: int = Field(DEFAULT_TIMEOUT_SEC["bdib"], ge=3, le=180, description="Hard timeout in seconds")


class BqlInput(BaseModel):
    query: str = Field(..., min_length=5, description="BQL query string")
    validate_first: bool = Field(True, description="Validate syntax before executing")
    timeout: int = Field(DEFAULT_TIMEOUT_SEC["bql"], ge=5, le=300, description="Timeout in seconds")


class BqlBuildInput(BaseModel):
    intent: str = Field(..., description="Natural-language description of desired query")
    universe_type: str | None = Field(None, description="equity, bond, index, or loan")


class BondInfoInput(BaseModel):
    securities: list[str] = Field(..., min_length=1)
    include_risk: bool = Field(True, description="Include duration, convexity, DV01")
    include_spreads: bool = Field(True, description="Include OAS, Z-spread, ASW")
    timeout: int = Field(DEFAULT_TIMEOUT_SEC["bond_info"], ge=3, le=120, description="Hard timeout in seconds")


class ScreenInput(BaseModel):
    screen_name: str | None = Field(None, description="Saved Bloomberg screen name")
    bql_filter: str | None = Field(None, description="Ad-hoc BQL filter expression")
    fields: list[str] = Field(default_factory=lambda: ["name", "px_last"], description="Fields to retrieve")
    max_results: int = Field(100, ge=1, le=5000)
    timeout: int = Field(DEFAULT_TIMEOUT_SEC["screen"], ge=3, le=180, description="Hard timeout in seconds")


class FieldSearchInput(BaseModel):
    query: str = Field(..., min_length=2, description="Search term for field mnemonics")
    max_results: int = Field(20, ge=1, le=100)
    timeout: int = Field(DEFAULT_TIMEOUT_SEC["field_search"], ge=3, le=60, description="Hard timeout in seconds")



# ======================================================================
# Tool 1: bloomberg_status
# ======================================================================

@mcp.tool()
async def bloomberg_status(
    deep_check: bool = True,
    probe_timeout: float = 20.0,
    warm_xbbg: bool = True,
    ctx: Context = None,
) -> dict[str, Any]:
    """Check Bloomberg Terminal connectivity and API status.

    Returns process list, terminal_running flag, api_connected flag, xbbg
    backend state, BQL fallback availability, and circuit-breaker state.

    By default this runs a bounded API probe and warms the xbbg data backend
    with a tiny IBM PX_LAST BDP call. Pass ``deep_check=False`` and
    ``warm_xbbg=False`` for a fast process-only check.
    """
    try:
        probe_timeout = float(probe_timeout)
    except (TypeError, ValueError):
        return _error_response("probe_timeout must be numeric", "validation_error")
    probe_timeout = max(0.5, min(probe_timeout, 30.0))

    on_timeout = None
    if ctx is not None:
        try:
            client = _get_client(ctx)
            on_timeout = lambda: client.record_timeout("bloomberg_status")
        except Exception:
            on_timeout = None

    # The status call itself must never hang. Total budget = probe_timeout + 3s
    # for the synchronous process scan.
    total_budget = (probe_timeout + 3.0) if deep_check else 3.0
    status = await _run_bounded(
        check_bloomberg_status,
        deep_check,
        probe_timeout,
        timeout=total_budget,
        label="bloomberg_status",
        on_timeout=on_timeout,
    )

    # If the bounded runner returned an error envelope, surface it as-is.
    if isinstance(status, dict) and "error" in status and "type" in status:
        return status

    if ctx is not None:
        try:
            client = _get_client(ctx)
            status["backend"] = client.backend_state()
            status["breaker"] = client.breaker_state()

            # api_connected is derived from the in-process xbbg warmup — the
            # same session real queries use — instead of a cold child-process
            # blpapi probe. Run it whenever any check is requested.
            if (warm_xbbg or deep_check) and status.get("terminal_running"):
                warmup = await _run_bounded(
                    client.warmup,
                    timeout=probe_timeout,
                    label="bloomberg_xbbg_warmup",
                    on_timeout=lambda: client.record_timeout("bloomberg_xbbg_warmup"),
                )
                if isinstance(warmup, dict) and "error" in warmup and "type" in warmup:
                    status["data_backend_ready"] = False
                    status["data_backend_error"] = warmup
                    status["api_connected"] = False
                    status["error"] = status.get("error") or "xbbg warmup probe failed"
                else:
                    status["data_backend_ready"] = True
                    status["data_backend_probe"] = warmup
                    status["api_connected"] = True
                status["breaker"] = client.breaker_state()
        except Exception:
            pass

    return status


# ======================================================================
# Tool 2: bloomberg_bdp
# ======================================================================

@mcp.tool()
async def bloomberg_bdp(
    securities: list[str],
    fields: list[str],
    overrides: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC["bdp"],
    ctx: Context = None,
) -> dict[str, Any]:
    """Fetch Bloomberg reference/snapshot data (BDP).

    Returns current values for specified fields across one or more securities.
    Hard timeout enforced; if the underlying Bloomberg call hangs, this tool
    returns a structured timeout_error within ``timeout`` seconds rather than
    blocking the MCP client.

    Example: securities=["AAPL US Equity"], fields=["PX_LAST", "Security_Name"]

    Common fields: PX_LAST, PX_BID, PX_ASK, PX_VOLUME, CUR_MKT_CAP, PE_RATIO,
    EV_TO_EBITDA, SALES_REV_TURN, EBITDA, IS_EPS, COUPON, MATURITY, YLD_YTM_MID,
    OAS_SPREAD_MID, DUR_ADJ_MID.

    Security formats: 'AAPL US Equity', 'SPX Index', 'EH469710 Corp',
    'US912810 Govt', '/cusip/912810TD8', '/isin/US912810TD80'.
    """
    try:
        inp = BdpInput(securities=securities, fields=fields, overrides=overrides, timeout=timeout)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    client = _get_client(ctx)
    return await _run_bounded(
        client.bdp, inp.securities, inp.fields, inp.overrides,
        timeout=inp.timeout, label="bloomberg_bdp",
        on_timeout=lambda: client.record_timeout("bloomberg_bdp"),
    )


# ======================================================================
# Tool 3: bloomberg_bdh
# ======================================================================

@mcp.tool()
async def bloomberg_bdh(
    securities: list[str],
    fields: list[str],
    start_date: str,
    end_date: str | None = None,
    periodicity: str | None = None,
    adjust: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC["bdh"],
    ctx: Context = None,
) -> dict[str, Any]:
    """Fetch Bloomberg historical time series data (BDH).

    Returns end-of-day values over a date range. Hard timeout enforced.
    Example: securities=["SPY US Equity"], fields=["PX_LAST"], start_date="2024-01-01"
    Periodicity options: D (daily), W (weekly), M (monthly), Q (quarterly), Y (yearly)
    """
    try:
        inp = BdhInput(securities=securities, fields=fields, start_date=start_date,
                       end_date=end_date, periodicity=periodicity, adjust=adjust, timeout=timeout)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    client = _get_client(ctx)
    return await _run_bounded(
        client.bdh, inp.securities, inp.fields, inp.start_date,
        inp.end_date, inp.periodicity, inp.adjust,
        timeout=inp.timeout, label="bloomberg_bdh",
        on_timeout=lambda: client.record_timeout("bloomberg_bdh"),
    )


# ======================================================================
# Tool 4: bloomberg_bdib
# ======================================================================

@mcp.tool()
async def bloomberg_bdib(
    security: str,
    date: str,
    interval: int = 5,
    session: str = "allday",
    timeout: int = DEFAULT_TIMEOUT_SEC["bdib"],
    ctx: Context = None,
) -> dict[str, Any]:
    """Fetch Bloomberg intraday bar data (BDIB).

    Returns OHLCV bars at specified minute intervals for a single trading day.
    Hard timeout enforced.
    Example: security="SPY US Equity", date="2024-01-15", interval=5
    """
    try:
        inp = BdibInput(security=security, date=date, interval=interval, session=session, timeout=timeout)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    client = _get_client(ctx)
    return await _run_bounded(
        client.bdib, inp.security, inp.date, inp.interval, inp.session,
        timeout=inp.timeout, label="bloomberg_bdib",
        on_timeout=lambda: client.record_timeout("bloomberg_bdib"),
    )


# ======================================================================
# Tool 5: bloomberg_bql
# ======================================================================

@mcp.tool()
async def bloomberg_bql(query: str, validate_first: bool = True, timeout: int = 60, ctx: Context = None) -> dict[str, Any]:
    """Execute a Bloomberg Query Language (BQL) query.

    BQL is Bloomberg's most powerful query interface. Supports screening,
    aggregation, time series, and cross-entity analysis.

    BEFORE writing a BQL query, use bloomberg_bql_reference to look up correct
    syntax for the domain you're querying. BQL has strict syntax rules.

    CRITICAL SYNTAX RULES:
    - yield(yield_type=YTW) — NOT yield(type=ytw)
    - duration(duration_type=modified) — NOT duration(type=modified)
    - spread(spread_type=OAS) — NOT spread(type=oas)
    - bondsuniv must be lowercase: bondsuniv(Active) — NOT bondsUniv
    - Rating filters use .source_scale numeric: rating(source=SP).source_scale <= 4
      Scale: 1=AAA, 2=AA+, 3=AA, 4=AA-, 5=A+, 6=A, 7=A-, 8=BBB+, 9=BBB, 10=BBB-
    - Tickers in for() must be in brackets: for(['AAPL US Equity'])
    - Use == for comparisons (not =)
    - Each let() variable must end with ;
    - Asset class suffix is required: Equity, Index, Corp, Govt, Comdty, Curncy, Mtge

    Example: query="get(px_last) for(['IBM US Equity'])"
    """
    try:
        inp = BqlInput(query=query, validate_first=validate_first, timeout=timeout)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    if inp.validate_first:
        validation = validate_bql(inp.query)
        if not validation["valid"]:
            return _error_response(
                "BQL syntax issues detected",
                "validation_error",
                suggestion="; ".join(validation["issues"]),
            )

    client = _get_client(ctx)
    # Give the outer wait_for a small grace period over the BQL query timeout.
    return await _run_bounded(
        client.bql, inp.query, inp.timeout,
        timeout=inp.timeout + 15, label="bloomberg_bql",
        on_timeout=lambda: client.record_timeout("bloomberg_bql"),
    )


# ======================================================================
# Tool 6: bloomberg_bql_build
# ======================================================================

@mcp.tool()
async def bloomberg_bql_build(intent: str, universe_type: str | None = None) -> dict[str, Any]:
    """Build a BQL query template from a natural-language description.

    Returns a template, defaults, syntax rules, and examples.
    Does NOT require Bloomberg Terminal — pure local logic.

    Example: intent="screen investment-grade USD bonds", universe_type="bond"
    """
    try:
        inp = BqlBuildInput(intent=intent, universe_type=universe_type)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    return build_bql_from_intent(inp.intent, inp.universe_type)


# ======================================================================
# Tool 7: bloomberg_bond_info
# ======================================================================

@mcp.tool()
async def bloomberg_bond_info(
    securities: list[str],
    include_risk: bool = True,
    include_spreads: bool = True,
    timeout: int = DEFAULT_TIMEOUT_SEC["bond_info"],
    ctx: Context = None,
) -> dict[str, Any]:
    """Fetch fixed income analytics for bonds.

    Returns coupon, maturity, price, yield, and optionally duration/convexity/DV01
    and OAS/Z-spread/ASW. Hard timeout enforced.

    Example: securities=["T 4.5 05/15/38 Govt"]
    Use /cusip/ or /isin/ prefixes: securities=["/cusip/912810TD8"]
    """
    try:
        inp = BondInfoInput(securities=securities, include_risk=include_risk,
                            include_spreads=include_spreads, timeout=timeout)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    client = _get_client(ctx)
    return await _run_bounded(
        client.bond_info, inp.securities, inp.include_risk, inp.include_spreads,
        timeout=inp.timeout, label="bloomberg_bond_info",
        on_timeout=lambda: client.record_timeout("bloomberg_bond_info"),
    )


# ======================================================================
# Tool 8: bloomberg_screen
# ======================================================================

@mcp.tool()
async def bloomberg_screen(
    screen_name: str | None = None,
    bql_filter: str | None = None,
    fields: list[str] | None = None,
    max_results: int = 100,
    timeout: int = DEFAULT_TIMEOUT_SEC["screen"],
    ctx: Context = None,
) -> dict[str, Any]:
    """Screen securities using a saved Bloomberg screen or ad-hoc BQL filter.

    Provide EITHER screen_name (for saved BEQS screens) OR bql_filter (ad-hoc).
    Hard timeout enforced.

    Example (saved): screen_name="MyInvestmentGradeScreen"
    Example (ad-hoc): bql_filter="crncy=='USD' and rtg_sp() in ['A+','A','A-']",
                      fields=["name", "yield()", "spread()"]
    """
    fields = fields or ["name", "px_last"]
    try:
        inp = ScreenInput(screen_name=screen_name, bql_filter=bql_filter,
                          fields=fields, max_results=max_results, timeout=timeout)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    if not inp.screen_name and not inp.bql_filter:
        return _error_response("Provide either screen_name or bql_filter", "validation_error")

    client = _get_client(ctx)
    if inp.screen_name:
        return await _run_bounded(
            client.screen_eqs, inp.screen_name,
            timeout=inp.timeout, label="bloomberg_screen(beqs)",
            on_timeout=lambda: client.record_timeout("bloomberg_screen(beqs)"),
        )
    return await _run_bounded(
        client.screen_bql, inp.bql_filter, inp.fields, inp.max_results,
        timeout=inp.timeout, label="bloomberg_screen(bql)",
        on_timeout=lambda: client.record_timeout("bloomberg_screen(bql)"),
    )


# ======================================================================
# Tool 9: bloomberg_field_search
# ======================================================================

@mcp.tool()
async def bloomberg_field_search(
    query: str,
    max_results: int = 20,
    timeout: int = DEFAULT_TIMEOUT_SEC["field_search"],
    ctx: Context = None,
) -> dict[str, Any]:
    """Search Bloomberg field mnemonics by keyword.

    Helps discover the correct field names for BDP/BDH/BQL queries.
    Hard timeout enforced.
    Example: query="yield to maturity"
    """
    try:
        inp = FieldSearchInput(query=query, max_results=max_results, timeout=timeout)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    client = _get_client(ctx)
    result = await _run_bounded(
        client.field_search, inp.query, inp.max_results,
        timeout=inp.timeout, label="bloomberg_field_search",
        on_timeout=lambda: client.record_timeout("bloomberg_field_search"),
    )
    # If error envelope, return as-is.
    if isinstance(result, dict) and "error" in result and "type" in result:
        return result
    return {"fields": result, "count": len(result) if isinstance(result, list) else 0, "query": inp.query}


# ======================================================================
# Tool 10: bloomberg_bql_reference
# ======================================================================

@mcp.tool()
async def bloomberg_bql_reference(domain: str) -> dict[str, Any]:
    """Get BQL reference documentation for a specific domain.

    ALWAYS call this before writing BQL queries to get correct syntax,
    field names, parameter names, and verified examples.

    Domains:
    - equity: Stock prices, volumes, market cap, EPS, PE, screening
    - fixed-income: Bond yield, spread, duration, DV01, universe screening
    - bonds: Bond analytics, YAS custom pricing (subset of fixed-income)
    - portfolio: PORT queries for holdings and weights
    - structured: FISP structured products, ABS, relative value
    - credit: Ratings, CDS, issuance (all three sub-domains)
    - ratings: Credit ratings from S&P, Moody's, Bloomberg
    - cds: Credit default swap spreads and indices
    - issuance: Market sizing, issuance trends
    - returns: Total return, cross-asset return series
    - curves: Sovereign, BVAL, HSA, issuer yield curves
    - funds: Fund screening, NAV, AUM, risk/return metrics
    - functions: All BQL functions (groupAvg, cumProd, rolling, filter, etc.)
    - securitized: Agency CMBS and TRACE trades
    - field-search: How to discover BQL field mnemonics
    """
    domain_lower = domain.lower().strip()
    ref_files = REFERENCE_MAP.get(domain_lower)

    if not ref_files:
        available = ", ".join(sorted(REFERENCE_MAP.keys()))
        return _error_response(
            f"Unknown domain '{domain}'. Available: {available}",
            "validation_error",
        )

    contents: list[dict[str, str]] = []
    for rel_path in ref_files:
        file_path = REFERENCES_DIR / rel_path
        if file_path.exists():
            contents.append({
                "file": rel_path,
                "content": file_path.read_text(encoding="utf-8"),
            })
        else:
            contents.append({
                "file": rel_path,
                "content": f"Reference file not found: {rel_path}",
            })

    return {
        "domain": domain_lower,
        "references": contents,
        "reference_count": len(contents),
    }


# ======================================================================
# Tool 11: bloomberg_bql_examples
# ======================================================================

@mcp.tool()
async def bloomberg_bql_examples(domain: str) -> dict[str, Any]:
    """Get verified BQL example queries for a specific domain.

    Use this to see correct, working BQL syntax before writing your own queries.
    All examples are verified against live Bloomberg.

    Domains: equity, fixed-income, credit, cds, curves, funds, returns
    """
    domain_lower = domain.lower().strip()
    examples = BQL_EXAMPLES.get(domain_lower)

    if not examples:
        available = ", ".join(sorted(BQL_EXAMPLES.keys()))
        return _error_response(
            f"Unknown domain '{domain}'. Available: {available}",
            "validation_error",
        )

    # Also include test queries for this domain if they exist
    test_queries: list[dict[str, str]] = []
    domain_prefixes = {
        "equity": ["01_", "02_", "03_", "04_", "05_", "06_"],
        "fixed-income": ["07_", "08_", "09_", "10_", "11_", "12_"],
        "credit": ["13_", "14_", "15_"],
        "cds": ["16_", "17_"],
        "returns": ["18_", "19_"],
        "curves": ["20_", "21_"],
        "funds": ["22_"],
    }
    prefixes = domain_prefixes.get(domain_lower, [])
    if TESTS_DIR.exists():
        for bql_file in sorted(TESTS_DIR.glob("*.bql")):
            if any(bql_file.name.startswith(p) for p in prefixes):
                content = bql_file.read_text(encoding="utf-8").strip()
                test_queries.append({"file": bql_file.name, "query": content})

    return {
        "domain": domain_lower,
        "examples": examples,
        "test_queries": test_queries,
        "syntax_rules": [
            "Tickers in for() must be wrapped in brackets: for(['TICKER'])",
            "Use == for comparisons (not =)",
            "Each let() variable must end with ;",
            "Ensure parentheses are balanced",
            "String values use single quotes: crncy=='USD'",
            "yield_type=YTW, duration_type=modified, spread_type=OAS (full param names)",
            "bondsuniv must be lowercase",
            "Rating filters use .source_scale numeric (1=AAA...10=BBB-), not string comparison",
            "Asset class suffix required: Equity, Index, Corp, Govt, Comdty, Curncy, Mtge",
        ],
    }


# ======================================================================
# Tool 12: bloomberg_reset
# ======================================================================

@mcp.tool()
async def bloomberg_reset(ctx: Context = None) -> dict[str, Any]:
    """Force-refresh the cached Bloomberg session and clear the circuit breaker.

    Call this when:
    - bloomberg_status reports terminal_running=True but data calls still fail
    - You've restarted the Bloomberg Terminal and want the MCP server to
      pick up the new session without restarting the server itself
    - The circuit breaker is open and you want to force a probe

    After reset, the next data call will lazily re-import xbbg and establish
    a fresh session.
    """
    if ctx is None:
        return _error_response("Context unavailable", "internal_error")
    try:
        client = _get_client(ctx)
        result = client.force_refresh()
        result["breaker_after"] = client.breaker_state()
        return result
    except Exception as exc:
        return _error_response(str(exc), "internal_error")


# ======================================================================
# Entrypoint
# ======================================================================

if __name__ == "__main__":
    mcp.run(show_banner=False)
