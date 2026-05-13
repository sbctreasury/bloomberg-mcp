# Bloomberg MCP Server

A self-documenting [Model Context Protocol](https://modelcontextprotocol.io/) server for Bloomberg Terminal data access. Works with any MCP client — Claude Code, Cursor, VS Code, LM Studio, or custom agents — without requiring a separate skill file or prompt injection.

## Features

- **12 tools** — status/reset, BDP, BDH, BDIB, BQL, bond analytics, screening, field search, reference docs, examples
- **17 BQL reference files** — comprehensive syntax documentation served as MCP resources
- **27 verified test queries** — covering equity, fixed income, credit, CDS, returns, curves, and funds
- **3-tier BQL execution** — polars-bloomberg → xbbg → bqnt-3 subprocess (automatic fallback)
- **Self-documenting** — BQL syntax rules embedded in tool descriptions; no external skill file needed

## Prerequisites

- **Bloomberg Terminal** — must be running and logged in
- **Python 3.11+** — Bloomberg's bqnt-3 Python works out of the box (see below)

## Installation

### Agent-friendly setup

On a Bloomberg Terminal workstation, Claude or Codex can install and configure the MCP with:

```powershell
git clone https://github.com/sbctreasury/bloomberg-mcp.git
cd bloomberg-mcp
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1
```

The setup script:

- Uses Bloomberg's built-in `C:\blp\bqnt\environments\bqnt-3\python.exe` when available
- Installs required Python packages (`fastmcp`, `pydantic`, `psutil`) and best-effort optional helpers (`xbbg`, `polars-bloomberg`, `polars`)
- Persists `BLOOMBERG_PYTHON` and `BLOOMBERG_MCP_HOME` user environment variables
- Writes a project `.mcp.json`
- Updates Claude Desktop at `%APPDATA%\Claude\claude_desktop_config.json`
- Registers Claude Code with `claude mcp add-json` when the Claude CLI is present
- Updates Codex at `%USERPROFILE%\.codex\config.toml` or `$CODEX_HOME\config.toml`
- Verifies Bloomberg Terminal/API connectivity with a bounded probe

Restart Claude Desktop, Claude Code, or Codex after setup so the client reloads MCP configuration.

### Manual setup

#### 1. Clone the repo

```bash
git clone https://github.com/sbctreasury/bloomberg-mcp.git
```

#### 2. Configure your MCP client

#### Option A: Bloomberg Terminal Python (recommended — no extra installs)

Every Bloomberg Terminal machine has Python pre-installed at `C:\blp\bqnt\environments\bqnt-3\python.exe`. The `run_server.py` launcher auto-installs missing packages (fastmcp, pydantic) on first run.

**Claude Code:**

```bash
claude mcp add bloomberg -- "C:/blp/bqnt/environments/bqnt-3/python.exe" "C:/path/to/bloomberg-mcp/run_server.py"
```

Or add to `.mcp.json`:

```json
{
  "mcpServers": {
    "bloomberg": {
      "command": "C:/blp/bqnt/environments/bqnt-3/python.exe",
      "args": ["C:/path/to/bloomberg-mcp/run_server.py"]
    }
  }
}
```

**Cursor / VS Code / LM Studio:**

```json
{
  "bloomberg": {
    "command": "C:/blp/bqnt/environments/bqnt-3/python.exe",
    "args": ["C:/path/to/bloomberg-mcp/run_server.py"]
  }
}
```

> **Note:** bqnt-3 already has `bql`, `blpapi`, and `pandas` installed. The launcher adds `fastmcp`, `pydantic`, and `psutil` automatically. BQL queries use the bqnt-3 subprocess backend, so xbbg and polars-bloomberg are helpful but not required for BQL.

#### Option B: uv (full dependency management)

If you have [uv](https://docs.astral.sh/uv/) installed, it auto-installs all dependencies (including the optional xbbg and polars-bloomberg for faster BQL execution):

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

> **Tip:** Find your `uv` path with `where uv` (Windows) or `which uv` (Mac/Linux).

#### Option C: Manual install

```bash
cd bloomberg-mcp
pip install -r server/requirements.txt
pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
```

Then configure with `python` directly:

```json
{
  "command": "python",
  "args": ["C:/path/to/bloomberg-mcp/server/server.py"]
}
```

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
├── pyproject.toml             # uv project config (auto-installs deps)
├── server/
│   ├── server.py              # FastMCP server (12 tools + resources)
│   ├── bloomberg_client.py    # Unified data access (3-tier BQL fallback)
│   ├── bql_builder.py         # BQL validation + template builder
│   ├── bql_subprocess.py      # bqnt-3 subprocess execution
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
