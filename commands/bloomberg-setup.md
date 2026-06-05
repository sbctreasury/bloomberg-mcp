---
name: bloomberg-setup
description: Install and configure the Bloomberg MCP server for Claude Desktop and Codex. Finds or installs uv, syncs the project environment, writes MCP configs, persists environment variables, and verifies connectivity. Claude Code auto-discovers the plugin and needs no setup.
---

# Bloomberg MCP Server Setup

> **Claude Code (marketplace install) needs no setup.** The plugin ships a
> bundled `.mcp.json` that Claude Code auto-discovers, and the launcher
> self-heals its environment with `uv sync` on first run. As long as `uv` is
> installed and Bloomberg Terminal is running, it just works. Run this script to
> configure **Claude Desktop / Codex** (which are not plugin-aware), to install
> `uv` if it is missing, or — for a direct, non-marketplace clone — pass
> `-RegisterClaudeCode` to register with Claude Code explicitly.

Run this from the plugin or repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1
```

If `${CLAUDE_PLUGIN_ROOT}` is available, use it explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}\scripts\setup-bloomberg-mcp.ps1"
```

## What The Script Does

1. Finds `uv`, or installs it for the current user if needed.
2. Runs `uv sync` against the repo lockfile to create/update the project `.venv`.
3. Persists the `BLOOMBERG_MCP_HOME` user environment variable.
4. Writes project `.mcp.json`.
5. Updates Claude Desktop config at `%APPDATA%\Claude\claude_desktop_config.json`.
6. Updates Codex config at `%USERPROFILE%\.codex\config.toml` or `$CODEX_HOME\config.toml`.
7. Verifies Bloomberg Terminal/API connectivity with the server's bounded status probe.

Claude Code is **not** registered by default — it auto-discovers the plugin.
Pass `-RegisterClaudeCode` to register it explicitly (e.g. a direct clone).

## Useful Flags

```powershell
# Configure only Codex
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1 -SkipClaudeDesktop

# Configure only Claude Desktop
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1 -SkipCodex

# Also register with Claude Code (direct, non-marketplace clones)
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1 -RegisterClaudeCode

# Use a specific uv executable
powershell -ExecutionPolicy Bypass -File .\scripts\setup-bloomberg-mcp.ps1 -UvPath "$env:USERPROFILE\.local\bin\uv.exe"

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
