---
name: bloomberg-setup
description: Set up the Bloomberg MCP server for Claude Desktop, Claude Code, or Codex. Use when Bloomberg tools are unavailable, after first install, or when bloomberg_status reports errors.
version: 1.1.0
metadata:
  filePattern: "**/bloomberg*/**,**/mcp-bloomberg/**"
  bashPattern: "setup-bloomberg-mcp|bloomberg|fastmcp|claude_desktop_config|codex.*mcp"
---

# Bloomberg MCP Server Setup

Use the repository setup script rather than manually editing MCP config files.

## Primary Workflow

1. Confirm the repo is cloned on the Bloomberg Terminal workstation.
2. Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1
```

When running from an installed Claude plugin cache, use:

```powershell
powershell -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}\scripts\setup-bloomberg-mcp.ps1"
```

3. Restart the client the user wants to use: Claude Desktop, Claude Code, or Codex.
4. Verify with `bloomberg_status` or a simple BQL quote query.

## What The Script Handles

- Detects Bloomberg's built-in `bqnt-3` Python first.
- Installs required packages: `fastmcp`, `pydantic`, `psutil`, `pandas`, `xbbg`.
- Uses Bloomberg's bundled bqnt-3 Python as the BQL subprocess fallback.
- Persists `BLOOMBERG_PYTHON` and `BLOOMBERG_MCP_HOME`.
- Writes project `.mcp.json`.
- Updates Claude Desktop config at `%APPDATA%\Claude\claude_desktop_config.json`.
- Registers Claude Code with `claude mcp add-json` when available.
- Updates Codex config at `%USERPROFILE%\.codex\config.toml` or `$CODEX_HOME\config.toml`.
- Runs a bounded Bloomberg API probe.

## Useful Flags

```powershell
# Codex only
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1 -SkipClaudeDesktop -SkipClaudeCode

# Claude only
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1 -SkipCodex

# Specific Python
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1 -PythonPath "C:\blp\bqnt\environments\bqnt-3\python.exe"

# Config only
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1 -SkipPackageInstall
```

## Troubleshooting

- If no Bloomberg processes are found, ask the user to start Bloomberg Terminal with `wintrv`, log in, and retry.
- If package install fails, rerun with `-SkipPackageInstall`; BQL fallback can still work through Bloomberg's bundled `bql` package, but BDP/BDH/BDIB need `xbbg`.
- If Claude Desktop or Codex was open during setup, restart it so it reloads the MCP config.
- If Codex already has a stale Bloomberg server entry, rerun the script. It replaces `[mcp_servers.bloomberg]` and `[mcp_servers.bloomberg.env]`.
