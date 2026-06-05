[CmdletBinding()]
param(
    [string]$ServerName = "bloomberg",
    [int]$ProbeTimeoutSec = 20,
    [string]$UvPath,
    [switch]$SkipPackageInstall,
    [switch]$SkipEnvVars,
    [switch]$SkipClaudeDesktop,
    [switch]$RegisterClaudeCode,
    [switch]$SkipCodex,
    [switch]$SkipProjectMcpJson,
    [switch]$SkipConnectionTest
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Convert-ToForwardSlash {
    param([string]$Path)
    return $Path.Replace("\", "/")
}

function ConvertTo-TomlString {
    param([string]$Value)
    return ($Value | ConvertTo-Json -Compress)
}

function Invoke-CommandChecked {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    & $FilePath @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "Command failed with exit code ${exitCode}: $FilePath $($Arguments -join ' ')"
    }
    return $exitCode
}

function Get-BootstrapPython {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $candidate = & $pyLauncher.Source -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $candidate) {
            return [string]$candidate
        }
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        return $pythonCmd.Source
    }

    throw "No Python executable found to install uv. Install Python 3.11+ or install uv manually, then retry."
}

function Ensure-Pip {
    param([string]$Python)

    & $Python -m pip --version *> $null
    if ($LASTEXITCODE -eq 0) {
        return
    }

    Write-Host "pip not found for $Python; trying ensurepip..."
    [void](Invoke-CommandChecked $Python @("-m", "ensurepip", "--upgrade"))
}

function Find-Uv {
    if ($UvPath) {
        if (-not (Test-Path -LiteralPath $UvPath)) {
            throw "UvPath was provided but does not exist: $UvPath"
        }
        return (Resolve-Path -LiteralPath $UvPath).Path
    }

    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCmd) {
        return $uvCmd.Source
    }

    $known = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:APPDATA "Python\Python313\Scripts\uv.exe"),
        (Join-Path $env:APPDATA "Python\Python312\Scripts\uv.exe"),
        (Join-Path $env:APPDATA "Python\Python311\Scripts\uv.exe")
    )
    foreach ($candidate in $known) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return $null
}

function Ensure-Uv {
    $uv = Find-Uv
    if ($uv) {
        return $uv
    }

    if ($SkipPackageInstall) {
        throw "uv was not found and -SkipPackageInstall was specified. Install uv or provide -UvPath."
    }

    Write-Step "Installing uv for the current user"
    $bootstrapPython = Get-BootstrapPython
    Write-Host "Bootstrap Python: $bootstrapPython"
    Ensure-Pip $bootstrapPython
    [void](Invoke-CommandChecked $bootstrapPython @("-m", "pip", "install", "--user", "--upgrade", "uv"))

    $uv = Find-Uv
    if (-not $uv) {
        throw "uv installed but uv.exe was not found. Add the Python user Scripts directory to PATH or provide -UvPath."
    }
    return $uv
}

function Sync-ProjectEnvironment {
    param(
        [string]$Uv,
        [string]$RepoRoot
    )

    Write-Step "Syncing project environment with uv"
    [void](Invoke-CommandChecked $Uv @("sync", "--project", $RepoRoot))
}

function New-McpServerConfig {
    param(
        [string]$Uv,
        [string]$Launcher,
        [string]$RepoRoot
    )

    return [ordered]@{
        command = (Convert-ToForwardSlash $Uv)
        args = @(
            "run",
            "--project",
            (Convert-ToForwardSlash $RepoRoot),
            "python",
            (Convert-ToForwardSlash $Launcher)
        )
        env = [ordered]@{
            BLOOMBERG_MCP_HOME = (Convert-ToForwardSlash $RepoRoot)
            PYTHONUTF8 = "1"
        }
    }
}

function Write-JsonConfigServer {
    param(
        [string]$ConfigPath,
        [string]$Name,
        [hashtable]$ServerConfig
    )

    $configDir = Split-Path -Parent $ConfigPath
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null

    if (Test-Path -LiteralPath $ConfigPath) {
        $raw = Get-Content -LiteralPath $ConfigPath -Raw
        if ($raw.Trim()) {
            try {
                $config = $raw | ConvertFrom-Json
            } catch {
                $backup = "$ConfigPath.bak.$(Get-Date -Format yyyyMMddHHmmss)"
                Copy-Item -LiteralPath $ConfigPath -Destination $backup
                Write-Warning "Could not parse $ConfigPath. Backed it up to $backup and starting fresh."
                $config = [pscustomobject]@{}
            }
        } else {
            $config = [pscustomobject]@{}
        }
    } else {
        $config = [pscustomobject]@{}
    }

    if (-not $config.PSObject.Properties["mcpServers"]) {
        Add-Member -InputObject $config -MemberType NoteProperty -Name "mcpServers" -Value ([pscustomobject]@{})
    }

    $mcpServers = $config.mcpServers
    if ($mcpServers.PSObject.Properties[$Name]) {
        $mcpServers.PSObject.Properties.Remove($Name)
    }
    Add-Member -InputObject $mcpServers -MemberType NoteProperty -Name $Name -Value ([pscustomobject]$ServerConfig)

    $config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
}

function Remove-TomlTable {
    param(
        [string]$Text,
        [string]$TableName
    )
    $escaped = [regex]::Escape($TableName)
    return [regex]::Replace($Text, "(?ms)^\[$escaped\]\r?\n.*?(?=^\[|\z)", "")
}

function Write-CodexConfig {
    param(
        [string]$Name,
        [hashtable]$ServerConfig
    )

    $codexHome = $env:CODEX_HOME
    if (-not $codexHome) {
        $codexHome = Join-Path $HOME ".codex"
    }
    $configPath = Join-Path $codexHome "config.toml"
    New-Item -ItemType Directory -Force -Path $codexHome | Out-Null

    $text = ""
    if (Test-Path -LiteralPath $configPath) {
        $text = Get-Content -LiteralPath $configPath -Raw
    }

    $text = Remove-TomlTable $text "mcp_servers.$Name.env"
    $text = Remove-TomlTable $text "mcp_servers.$Name"
    $text = $text.TrimEnd()

    $command = ConvertTo-TomlString ([string]$ServerConfig.command)
    $argStrings = @($ServerConfig.args | ForEach-Object { ConvertTo-TomlString ([string]$_) })
    $args = $argStrings -join ", "
    $envLines = @()
    foreach ($key in $ServerConfig.env.Keys) {
        $envLines += "$key = $(ConvertTo-TomlString ([string]$ServerConfig.env[$key]))"
    }

    $block = @"

[mcp_servers.$Name]
command = $command
args = [$args]

[mcp_servers.$Name.env]
$($envLines -join "`n")
"@

    ($text + $block + "`n") | Set-Content -LiteralPath $configPath -Encoding UTF8
    return $configPath
}

function Register-ClaudeCode {
    param(
        [string]$Name,
        $ServerConfig
    )

    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claude) {
        Write-Warning "Claude Code CLI not found on PATH; skipped 'claude mcp add'."
        return $false
    }

    # Build the argument list explicitly instead of passing inline JSON.
    # On Windows PowerShell the embedded double quotes in compressed JSON are
    # mangled when handed to the native claude executable, which then rejects it
    # with "Invalid configuration: : Invalid input". Using --env flags plus a
    # "--" command separator avoids any quoting round-trip.
    & $claude.Source mcp remove -s user $Name 2>$null | Out-Null

    $cliArgs = @("mcp", "add", "--scope", "user", $Name)
    foreach ($key in $ServerConfig.env.Keys) {
        $cliArgs += "--env"
        $cliArgs += ("{0}={1}" -f $key, $ServerConfig.env[$key])
    }
    $cliArgs += "--"
    $cliArgs += [string]$ServerConfig.command
    foreach ($arg in $ServerConfig.args) {
        $cliArgs += [string]$arg
    }

    & $claude.Source @cliArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Claude Code CLI registration failed; Claude Desktop and project configs may still be usable."
        return $false
    }
    return $true
}

function Test-BloombergConnection {
    param(
        [string]$Uv,
        [string]$RepoRoot,
        [int]$TimeoutSec
    )

    $serverDir = Convert-ToForwardSlash (Join-Path $RepoRoot "server")
    $probeScript = @"
import json
import sys
sys.path.insert(0, r'''$serverDir''')
from utils import check_bloomberg_status
status = check_bloomberg_status(deep_check=True, probe_timeout=$TimeoutSec)
print(json.dumps(status))
raise SystemExit(0 if status.get('api_connected') else 2)
"@

    $output = & $Uv run --project $RepoRoot python -c $probeScript 2>&1
    $exitCode = $LASTEXITCODE
    if ($output) {
        foreach ($line in $output) {
            Write-Host $line
        }
    }
    return $exitCode -eq 0
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$launcher = Join-Path $repoRoot "launcher.py"
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "launcher.py not found at $launcher"
}

Write-Step "Selecting uv runtime"
$uv = Ensure-Uv
Write-Host "uv: $uv"

if (-not $SkipPackageInstall) {
    Sync-ProjectEnvironment $uv $repoRoot
}

if (-not $SkipEnvVars) {
    Write-Step "Persisting user environment variables"
    [Environment]::SetEnvironmentVariable("BLOOMBERG_MCP_HOME", (Convert-ToForwardSlash $repoRoot), "User")
    $env:BLOOMBERG_MCP_HOME = Convert-ToForwardSlash $repoRoot
}

$serverConfig = New-McpServerConfig $uv $launcher $repoRoot

if (-not $SkipProjectMcpJson) {
    Write-Step "Writing project .mcp.json"
    $projectMcp = Join-Path $repoRoot ".mcp.json"
    $projectConfig = [ordered]@{ mcpServers = [ordered]@{ $ServerName = $serverConfig } }
    $projectConfig | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $projectMcp -Encoding UTF8
    Write-Host "Project MCP config: $projectMcp"
}

if (-not $SkipClaudeDesktop) {
    Write-Step "Updating Claude Desktop config"
    $claudeDesktopConfig = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
    Write-JsonConfigServer $claudeDesktopConfig $ServerName $serverConfig
    Write-Host "Claude Desktop config: $claudeDesktopConfig"
}

if ($RegisterClaudeCode) {
    # Claude Code auto-discovers this server from the plugin's bundled .mcp.json
    # (it reads ${CLAUDE_PLUGIN_ROOT} and the self-healing launcher needs no setup),
    # so explicit registration is opt-in. Use -RegisterClaudeCode only for direct,
    # non-marketplace clones where there is no plugin to auto-discover.
    Write-Step "Registering Claude Code MCP server"
    [void](Register-ClaudeCode $ServerName $serverConfig)
}

if (-not $SkipCodex) {
    Write-Step "Updating Codex MCP config"
    $codexConfig = Write-CodexConfig $ServerName $serverConfig
    Write-Host "Codex config: $codexConfig"
}

if (-not $SkipConnectionTest) {
    Write-Step "Checking Bloomberg Terminal processes"
    $bbgProcesses = Get-Process -Name wintrv, bbcomm, bblauncher, bbg -ErrorAction SilentlyContinue
    if ($bbgProcesses) {
        Write-Host ("Bloomberg processes: " + (($bbgProcesses | Select-Object -ExpandProperty ProcessName -Unique) -join ", "))
    } else {
        Write-Warning "No Bloomberg Terminal processes found. Start Bloomberg Terminal and log in before using the MCP server."
    }

    Write-Step "Testing Bloomberg API connection"
    $connected = Test-BloombergConnection $uv $repoRoot $ProbeTimeoutSec
    if ($connected) {
        Write-Host "Bloomberg API connected."
    } else {
        Write-Warning "Bloomberg API probe did not connect within ${ProbeTimeoutSec}s. The MCP config was still written."
    }
}

Write-Step "Done"
Write-Host "MCP server '$ServerName' is configured."
Write-Host "Restart Claude Desktop, Claude Code, or Codex so the new MCP config is loaded."
