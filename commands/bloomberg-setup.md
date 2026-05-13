---
name: bloomberg-setup
description: Install and configure the Bloomberg MCP server for Claude Desktop, Claude Code, and Codex. Detects Bloomberg Python, installs dependencies, writes MCP configs, persists environment variables, and verifies connectivity.
---

# Bloomberg MCP Server Setup

Run this from the plugin or repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1
```

If `${CLAUDE_PLUGIN_ROOT}` is available, use it explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}\scripts\setup-bloomberg-mcp.ps1"
```

## What The Script Does

1. Selects the best Python, preferring Bloomberg's built-in `C:\blp\bqnt\environments\bqnt-3\python.exe`.
2. Installs required packages: `fastmcp`, `pydantic`, and `psutil`.
3. Tries to install optional helper packages: `xbbg`, `polars-bloomberg`, and `polars`.
4. Persists user environment variables:
   - `BLOOMBERG_PYTHON`
   - `BLOOMBERG_MCP_HOME`
5. Writes project `.mcp.json`.
6. Updates Claude Desktop config at `%APPDATA%\Claude\claude_desktop_config.json`.
7. Registers Claude Code with `claude mcp add-json` when the Claude CLI is installed.
8. Updates Codex config at `%USERPROFILE%\.codex\config.toml` or `$CODEX_HOME\config.toml`.
9. Verifies Bloomberg Terminal/API connectivity with the server's bounded status probe.

## Useful Flags

```powershell
# Configure only Codex
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1 -SkipClaudeDesktop -SkipClaudeCode

# Configure only Claude Desktop / Claude Code
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1 -SkipCodex

# Use a specific Python
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1 -PythonPath "C:\blp\bqnt\environments\bqnt-3\python.exe"

# Write configs without installing packages
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1 -SkipPackageInstall
```

## If Setup Cannot Verify Bloomberg

Tell the user to:

1. Start Bloomberg Terminal with `wintrv`.
2. Log in fully.
3. Wait until `wintrv.exe` and `bbcomm.exe` are visible.
4. Re-run `/bloomberg-setup`.

## Completion Message

If setup succeeds, report:

> Bloomberg MCP is installed. Restart Claude Desktop, Claude Code, or Codex to reload the MCP configuration. Then test with `bloomberg_status` or ask for a simple Bloomberg quote such as IBM or AAPL.
