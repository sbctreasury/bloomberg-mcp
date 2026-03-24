# Bloomberg MCP Server

A self-documenting [Model Context Protocol](https://modelcontextprotocol.io/) server for Bloomberg Terminal data access. Works with any MCP client — Claude Code, Cursor, VS Code, or custom agents — without requiring a separate skill file or prompt injection.

## Features

- **12 tools** — BDP, BDH, BDIB, BQL, bond analytics, screening, field search, charting, reference docs, examples
- **17 BQL reference files** — comprehensive syntax documentation served as MCP resources
- **27 verified test queries** — covering equity, fixed income, credit, CDS, returns, curves, and funds
- **3-tier BQL execution** — polars-bloomberg → xbbg → bqnt-3 subprocess (automatic fallback)
- **Self-documenting** — BQL syntax rules embedded in tool descriptions; no external skill file needed

## Prerequisites

- **Bloomberg Terminal** — must be running and logged in
- **Python 3.12+**
- **uv** — for automatic dependency management (`pip install uv`)

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/damanijb/bloomberg-mcp.git
```

### 2. Configure your MCP client

The server uses `uv run` to automatically install all dependencies (including `blpapi` from Bloomberg's package index) on first launch. No manual `pip install` needed.

#### Claude Code

```bash
claude mcp add bloomberg -- uv run --project /path/to/bloomberg-mcp python /path/to/bloomberg-mcp/server/server.py
```

Or add to `.mcp.json`:

```json
{
  "mcpServers": {
    "bloomberg": {
      "command": "uv",
      "args": ["run", "--project", "C:/path/to/bloomberg-mcp", "python", "C:/path/to/bloomberg-mcp/server/server.py"]
    }
  }
}
```

#### Cursor / VS Code / LM Studio

Use full paths (these apps don't inherit your shell PATH):

```json
{
  "bloomberg": {
    "command": "C:/ProgramData/miniconda3/Scripts/uv.exe",
    "args": ["run", "--project", "C:/path/to/bloomberg-mcp", "python", "C:/path/to/bloomberg-mcp/server/server.py"]
  }
}
```

> **Tip:** If `uv` isn't on PATH, use the full path to the `uv` executable. Find it with `where uv` or `python -m uv` as a fallback.

#### Without uv (manual install)

```bash
cd bloomberg-mcp
pip install -r server/requirements.txt
# blpapi from Bloomberg's index:
pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
```

Then configure with `python` directly:

```json
{
  "command": "C:/ProgramData/miniconda3/python.exe",
  "args": ["C:/path/to/bloomberg-mcp/server/server.py"]
}

## Tools

| Tool | Description |
|------|-------------|
| `bloomberg_status` | Check terminal connectivity and available BQL backends |
| `bloomberg_bdp` | Reference/snapshot data (current values) |
| `bloomberg_bdh` | Historical time series |
| `bloomberg_bdib` | Intraday bar data |
| `bloomberg_bql` | Execute BQL queries (with syntax validation + error hints) |
| `bloomberg_bql_build` | Natural language → BQL query template |
| `bloomberg_bond_info` | Fixed income analytics (yield, duration, spreads) |
| `bloomberg_screen` | Saved or ad-hoc security screening |
| `bloomberg_field_search` | Discover Bloomberg field mnemonics |
| `bloomberg_chart` | Generate charts (matplotlib/altair) |
| `bloomberg_bql_reference` | Get BQL syntax docs for a domain |
| `bloomberg_bql_examples` | Get verified BQL examples + test queries |

## MCP Resources

The server exposes BQL reference documentation as MCP resources:

- `bloomberg://references/index` — navigation index
- `bloomberg://references/{domain}/{file}` — domain-specific reference
- `bloomberg://tests/{file}` — verified test queries

## BQL Reference Domains

| Domain | Coverage |
|--------|----------|
| `equity` | Prices, volumes, market cap, EPS, PE, screening |
| `fixed-income` | Bond yield, spread, duration, DV01, universe screening |
| `credit` | Ratings, CDS, issuance |
| `returns` | Total return, cross-asset return series |
| `curves` | Sovereign, BVAL, HSA, issuer curves |
| `funds` | NAV, AUM, risk/return, screening |
| `functions` | 50+ BQL functions (groupAvg, cumProd, rolling, etc.) |
| `securitized` | Agency CMBS, TRACE trades |

## Critical BQL Syntax Rules

These rules are embedded in tool descriptions so any MCP client learns them automatically:

| Wrong | Correct |
|-------|---------|
| `yield(type=ytw)` | `yield(yield_type=YTW)` |
| `duration(type=modified)` | `duration(duration_type=modified)` |
| `spread(type=oas)` | `spread(spread_type=OAS)` |
| `bondsUniv(...)` | `bondsuniv(Active)` (lowercase) |
| `rating(source=SP) >= 'AA-'` | `rating(source=SP).source_scale <= 4` |

## Project Structure

```
bloomberg-mcp/
├── server/
│   ├── server.py              # FastMCP server (12 tools + resources)
│   ├── bloomberg_client.py    # Unified data access (3-tier BQL fallback)
│   ├── bql_builder.py         # BQL validation + template builder
│   ├── bql_subprocess.py      # bqnt-3 subprocess execution
│   ├── chart_engine.py        # matplotlib + altair charting
│   ├── utils.py               # Helpers (status check, serialization)
│   └── requirements.txt
├── references/                # 17 BQL reference files
│   ├── _tree-index.md
│   ├── equity/
│   ├── fixed-income/
│   ├── credit/
│   ├── returns/
│   ├── curves/
│   ├── funds/
│   ├── functions/
│   └── securitized/
└── tests/                     # 27 verified .bql test queries
```

## License

MIT
