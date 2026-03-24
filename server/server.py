"""
Bloomberg MCP Server — unified, self-documenting FastMCP implementation.

Merges three Bloomberg data access approaches into one MCP server:
- xbbg (BDP/BDH/BDIB/BQL via pandas)
- polars-bloomberg (BQL via Polars)
- bqnt-3 subprocess (zero-dependency BQL fallback)

Exposes 12 tools + MCP resources for BQL reference documentation.
Any MCP client (Claude Code, Cursor, VS Code, custom agents) can use
this server without a separate skill file — it's fully self-documenting.

Run:
    python server.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Ensure local modules are importable
sys.path.insert(0, str(Path(__file__).parent))

from fastmcp import FastMCP, Context

from bloomberg_client import BloombergClient
from bql_builder import build_bql_from_intent, validate_bql
from chart_engine import generate_chart
from utils import check_bloomberg_status

logger = logging.getLogger("bloomberg_mcp")
logging.basicConfig(level=logging.INFO)

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
        "Bloomberg Terminal data access — BDP, BDH, BDIB, BQL queries, "
        "bond analytics, screening, field search, charting, and comprehensive "
        "BQL reference documentation. Use bloomberg_bql_reference to look up "
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


class BdhInput(BaseModel):
    securities: list[str] = Field(..., min_length=1)
    fields: list[str] = Field(..., min_length=1)
    start_date: str = Field(..., description="Start date YYYY-MM-DD")
    end_date: str | None = Field(None, description="End date YYYY-MM-DD (default: today)")
    periodicity: str | None = Field(None, description="D, W, M, Q, Y")
    adjust: str | None = Field(None, description="Adjustment: all, split, etc.")

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


class BqlInput(BaseModel):
    query: str = Field(..., min_length=5, description="BQL query string")
    validate_first: bool = Field(True, description="Validate syntax before executing")
    timeout: int = Field(60, ge=5, le=300, description="Timeout in seconds")


class BqlBuildInput(BaseModel):
    intent: str = Field(..., description="Natural-language description of desired query")
    universe_type: str | None = Field(None, description="equity, bond, index, or loan")


class BondInfoInput(BaseModel):
    securities: list[str] = Field(..., min_length=1)
    include_risk: bool = Field(True, description="Include duration, convexity, DV01")
    include_spreads: bool = Field(True, description="Include OAS, Z-spread, ASW")


class ScreenInput(BaseModel):
    screen_name: str | None = Field(None, description="Saved Bloomberg screen name")
    bql_filter: str | None = Field(None, description="Ad-hoc BQL filter expression")
    fields: list[str] = Field(default_factory=lambda: ["name", "px_last"], description="Fields to retrieve")
    max_results: int = Field(100, ge=1, le=5000)


class FieldSearchInput(BaseModel):
    query: str = Field(..., min_length=2, description="Search term for field mnemonics")
    max_results: int = Field(20, ge=1, le=100)


class ChartInput(BaseModel):
    chart_type: str = Field(..., description="timeseries, bar, scatter, heatmap, multipanel, or facet")
    library: str = Field("matplotlib", description="matplotlib or altair")
    data_json: list[dict] = Field(..., min_length=1, description="List of row dicts (from bloomberg_bdp/bdh output .data)")
    title: str = Field("Bloomberg Data", description="Chart title")
    x_col: str | None = Field(None, description="X-axis column (auto-detected if omitted)")
    y_cols: list[str] | None = Field(None, description="Y-axis column(s) (auto-detected if omitted)")


# ======================================================================
# Tool 1: bloomberg_status
# ======================================================================

@mcp.tool()
async def bloomberg_status() -> dict[str, Any]:
    """Check Bloomberg Terminal connectivity and API status.

    Returns process list, terminal_running flag, api_connected flag,
    and available BQL execution backends (polars-bloomberg, xbbg, bqnt-3).
    No arguments required.
    """
    status = await asyncio.to_thread(check_bloomberg_status)

    # Also report BQL backend availability
    from bql_subprocess import is_available as bqnt3_available
    backends = []
    try:
        from polars_bloomberg import BQuery  # noqa: F401
        backends.append("polars-bloomberg")
    except ImportError:
        pass
    try:
        from xbbg import blp  # noqa: F401
        backends.append("xbbg")
    except ImportError:
        pass
    if bqnt3_available():
        backends.append("bqnt-3-subprocess")
    status["bql_backends"] = backends

    return status


# ======================================================================
# Tool 2: bloomberg_bdp
# ======================================================================

@mcp.tool()
async def bloomberg_bdp(securities: list[str], fields: list[str], overrides: dict[str, str] | None = None, ctx: Context = None) -> dict[str, Any]:
    """Fetch Bloomberg reference/snapshot data (BDP).

    Returns current values for specified fields across one or more securities.

    Example: securities=["AAPL US Equity"], fields=["PX_LAST", "Security_Name"]

    Common fields: PX_LAST, PX_BID, PX_ASK, PX_VOLUME, CUR_MKT_CAP, PE_RATIO,
    EV_TO_EBITDA, SALES_REV_TURN, EBITDA, IS_EPS, COUPON, MATURITY, YLD_YTM_MID,
    OAS_SPREAD_MID, DUR_ADJ_MID.

    Security formats: 'AAPL US Equity', 'SPX Index', 'EH469710 Corp',
    'US912810 Govt', '/cusip/912810TD8', '/isin/US912810TD80'.
    """
    try:
        inp = BdpInput(securities=securities, fields=fields, overrides=overrides)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    try:
        client = _get_client(ctx)
        return await asyncio.to_thread(client.bdp, inp.securities, inp.fields, inp.overrides)
    except Exception as e:
        return _error_response(str(e), "bloomberg_error", "Ensure Bloomberg Terminal is running")


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
    ctx=None,
) -> dict[str, Any]:
    """Fetch Bloomberg historical time series data (BDH).

    Returns end-of-day values over a date range.
    Example: securities=["SPY US Equity"], fields=["PX_LAST"], start_date="2024-01-01"
    Periodicity options: D (daily), W (weekly), M (monthly), Q (quarterly), Y (yearly)
    """
    try:
        inp = BdhInput(securities=securities, fields=fields, start_date=start_date, end_date=end_date, periodicity=periodicity, adjust=adjust)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    try:
        client = _get_client(ctx)
        return await asyncio.to_thread(client.bdh, inp.securities, inp.fields, inp.start_date, inp.end_date, inp.periodicity, inp.adjust)
    except Exception as e:
        return _error_response(str(e), "bloomberg_error", "Ensure Bloomberg Terminal is running")


# ======================================================================
# Tool 4: bloomberg_bdib
# ======================================================================

@mcp.tool()
async def bloomberg_bdib(
    security: str,
    date: str,
    interval: int = 5,
    session: str = "allday",
    ctx=None,
) -> dict[str, Any]:
    """Fetch Bloomberg intraday bar data (BDIB).

    Returns OHLCV bars at specified minute intervals for a single trading day.
    Example: security="SPY US Equity", date="2024-01-15", interval=5
    """
    try:
        inp = BdibInput(security=security, date=date, interval=interval, session=session)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    try:
        client = _get_client(ctx)
        return await asyncio.to_thread(client.bdib, inp.security, inp.date, inp.interval, inp.session)
    except Exception as e:
        return _error_response(str(e), "bloomberg_error", "Ensure Bloomberg Terminal is running")


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

    try:
        client = _get_client(ctx)
        return await asyncio.to_thread(client.bql, inp.query, inp.timeout)
    except Exception as e:
        return _error_response(str(e), "bloomberg_error", "Check BQL syntax and Bloomberg connectivity")


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
    ctx=None,
) -> dict[str, Any]:
    """Fetch fixed income analytics for bonds.

    Returns coupon, maturity, price, yield, and optionally duration/convexity/DV01
    and OAS/Z-spread/ASW.

    Example: securities=["T 4.5 05/15/38 Govt"]
    Use /cusip/ or /isin/ prefixes: securities=["/cusip/912810TD8"]
    """
    try:
        inp = BondInfoInput(securities=securities, include_risk=include_risk, include_spreads=include_spreads)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    try:
        client = _get_client(ctx)
        return await asyncio.to_thread(client.bond_info, inp.securities, inp.include_risk, inp.include_spreads)
    except Exception as e:
        return _error_response(str(e), "bloomberg_error", "Ensure Bloomberg Terminal is running")


# ======================================================================
# Tool 8: bloomberg_screen
# ======================================================================

@mcp.tool()
async def bloomberg_screen(
    screen_name: str | None = None,
    bql_filter: str | None = None,
    fields: list[str] | None = None,
    max_results: int = 100,
    ctx=None,
) -> dict[str, Any]:
    """Screen securities using a saved Bloomberg screen or ad-hoc BQL filter.

    Provide EITHER screen_name (for saved BEQS screens) OR bql_filter (ad-hoc).

    Example (saved): screen_name="MyInvestmentGradeScreen"
    Example (ad-hoc): bql_filter="crncy=='USD' and rtg_sp() in ['A+','A','A-']",
                      fields=["name", "yield()", "spread()"]
    """
    fields = fields or ["name", "px_last"]
    try:
        inp = ScreenInput(screen_name=screen_name, bql_filter=bql_filter, fields=fields, max_results=max_results)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    if not inp.screen_name and not inp.bql_filter:
        return _error_response("Provide either screen_name or bql_filter", "validation_error")

    try:
        client = _get_client(ctx)
        if inp.screen_name:
            return await asyncio.to_thread(client.screen_eqs, inp.screen_name)
        else:
            return await asyncio.to_thread(client.screen_bql, inp.bql_filter, inp.fields, inp.max_results)
    except Exception as e:
        return _error_response(str(e), "bloomberg_error", "Ensure Bloomberg Terminal is running")


# ======================================================================
# Tool 9: bloomberg_field_search
# ======================================================================

@mcp.tool()
async def bloomberg_field_search(query: str, max_results: int = 20, ctx: Context = None) -> dict[str, Any]:
    """Search Bloomberg field mnemonics by keyword.

    Helps discover the correct field names for BDP/BDH/BQL queries.
    Example: query="yield to maturity"
    """
    try:
        inp = FieldSearchInput(query=query, max_results=max_results)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    try:
        client = _get_client(ctx)
        results = await asyncio.to_thread(client.field_search, inp.query, inp.max_results)
        return {"fields": results, "count": len(results), "query": inp.query}
    except Exception as e:
        return _error_response(str(e), "bloomberg_error", "Ensure Bloomberg Terminal is running")


# ======================================================================
# Tool 10: bloomberg_chart
# ======================================================================

@mcp.tool()
async def bloomberg_chart(
    chart_type: str,
    library: str = "matplotlib",
    data_json: list[dict] = [],
    title: str = "Bloomberg Data",
    x_col: str | None = None,
    y_cols: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a professional chart from Bloomberg data.

    matplotlib chart types: timeseries, bar, scatter, heatmap, multipanel
    altair chart types: timeseries, bar, scatter, facet

    Pass the .data array from any bloomberg_bdp/bdh/bql result as data_json.
    x_col and y_cols are auto-detected if omitted.

    Returns the file path to the saved chart (PNG for matplotlib, HTML for altair).
    """
    try:
        inp = ChartInput(chart_type=chart_type, library=library, data_json=data_json, title=title, x_col=x_col, y_cols=y_cols)
    except Exception as e:
        return _error_response(str(e), "validation_error")

    try:
        return await asyncio.to_thread(
            generate_chart,
            inp.chart_type,
            inp.library,
            inp.data_json,
            inp.title,
            inp.x_col,
            inp.y_cols,
        )
    except Exception as e:
        return _error_response(str(e), "chart_error", "Check data format and chart type compatibility")


# ======================================================================
# Tool 11: bloomberg_bql_reference
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
# Tool 12: bloomberg_bql_examples
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
# Entrypoint
# ======================================================================

if __name__ == "__main__":
    mcp.run()
