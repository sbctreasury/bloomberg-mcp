# Bloomberg MCP Server

A self-documenting [Model Context Protocol](https://modelcontextprotocol.io/) server for Bloomberg Terminal data access. Works with any MCP client — Claude Code, Cursor, VS Code, LM Studio, or custom agents — without requiring a separate skill file or prompt injection.

## Features

- **12 tools** — status/reset, BDP, BDH, BDIB, BQL, bond analytics, screening, field search, reference docs, examples
- **17 BQL reference files** — comprehensive syntax documentation served as MCP resources
- **27 verified test queries** — covering equity, fixed income, credit, CDS, returns, curves, and funds
- **xbbg-first execution** - BDP, BDH, BDIB, BQL, screening, field search, and bond analytics use xbbg with stable pandas/wide output
- **BQL fallback** - bqnt-3 subprocess remains available when an in-process BQL session is unhealthy
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

- Finds or installs `uv` for the current Windows user
- Runs `uv sync` against the repo lockfile to create/update the project `.venv`
- Uses Bloomberg's built-in `C:\blp\bqnt\environments\bqnt-3\python.exe` only as the BQL fallback runtime
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

#### Option A: Setup script (recommended)

The setup script installs `uv` if needed, syncs the project environment, and writes MCP configs with resolved user-specific paths.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1
```

#### Option B: uv MCP config

Use this shape if writing a config manually:

```bash
claude mcp add bloomberg -- uv run --project /path/to/bloomberg-mcp python /path/to/bloomberg-mcp/launcher.py
```

Or add to `.mcp.json`:

```json
{
  "mcpServers": {
    "bloomberg": {
      "command": "uv",
      "args": ["run", "--project", "C:/path/to/bloomberg-mcp", "python", "C:/path/to/bloomberg-mcp/launcher.py"],
      "env": {
        "BLOOMBERG_PYTHON": "C:/blp/bqnt/environments/bqnt-3/python.exe",
        "BLOOMBERG_MCP_HOME": "C:/path/to/bloomberg-mcp",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

**Cursor / VS Code / LM Studio JSON shape:**

```json
{
  "bloomberg": {
    "command": "uv",
    "args": ["run", "--project", "C:/path/to/bloomberg-mcp", "python", "C:/path/to/bloomberg-mcp/launcher.py"],
    "env": {
      "BLOOMBERG_PYTHON": "C:/blp/bqnt/environments/bqnt-3/python.exe",
      "BLOOMBERG_MCP_HOME": "C:/path/to/bloomberg-mcp",
      "PYTHONUTF8": "1"
    }
  }
}
```

> **Tip:** The setup script resolves the full `uv.exe` path automatically. If writing config by hand, find it with `where uv`.

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
  "args": ["C:/path/to/bloomberg-mcp/launcher.py"]
}
```

## Tools

| Tool | Description |
|------|-------------|
| `bloomberg_status` | Check terminal connectivity, xbbg backend state, BQL fallback availability, and circuit breaker state |
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
├── launcher.py                # Public MCP entrypoint / dependency bootstrapper
├── server/
│   ├── server.py              # Internal FastMCP implementation (12 tools + resources)
│   ├── bloomberg_client.py    # Unified xbbg data access with BQL subprocess fallback
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
