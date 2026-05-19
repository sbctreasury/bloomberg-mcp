[CmdletBinding()]
param(
    [string]$PythonPath,
    [string]$ServerName = "bloomberg",
    [int]$ProbeTimeoutSec = 20,
    [switch]$SkipPackageInstall,
    [switch]$SkipEnvVars,
    [switch]$SkipClaudeDesktop,
    [switch]$SkipClaudeCode,
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

function Test-PythonPackage {
    param(
        [string]$Python,
        [string]$Package
    )
    & $Python -c "import $Package" *> $null
    return $LASTEXITCODE -eq 0
}

function Get-BloombergPython {
    param([string]$RepoRoot)

    if ($PythonPath) {
        $candidate = (Resolve-Path -LiteralPath $PythonPath).Path
        return $candidate
    }

    $bqnt = "C:\blp\bqnt\environments\bqnt-3\python.exe"
    if (Test-Path -LiteralPath $bqnt) {
        return $bqnt
    }

    $detector = Join-Path $RepoRoot "scripts\detect-python.py"
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd -and (Test-Path -LiteralPath $detector)) {
        $raw = & $pythonCmd.Source $detector
        if ($LASTEXITCODE -eq 0 -and $raw) {
            $detected = $raw | ConvertFrom-Json
            if ($detected.python_path) {
                return [string]$detected.python_path
            }
        }
    }

    if ($pythonCmd) {
        return $pythonCmd.Source
    }

    throw "No Python executable found. Install Python 3.11+ or Bloomberg Terminal, then retry."
}

function Ensure-Pip {
    param([string]$Python)

    & $Python -m pip --version *> $null
    if ($LASTEXITCODE -eq 0) {
        return
    }

    Write-Host "pip not found for $Python; trying ensurepip..."
    Invoke-CommandChecked $Python @("-m", "ensurepip", "--upgrade")
}

function Install-Packages {
    param([string]$Python)

    Ensure-Pip $Python

    $corePackages = @(
        "fastmcp>=2.0.0",
        "pydantic>=2.0.0",
        "psutil>=5.9.0",
        "pandas>=2.0.0",
        "xbbg>=0.12.2,<2.0.0"
    )

    Write-Step "Installing required Python packages"
    Invoke-CommandChecked $Python (@("-m", "pip", "install", "--upgrade", "--quiet") + $corePackages)
}

function New-McpServerConfig {
    param(
        [string]$Python,
        [string]$Launcher,
        [string]$RepoRoot
    )

    return [ordered]@{
        command = (Convert-ToForwardSlash $Python)
        args = @((Convert-ToForwardSlash $Launcher))
        env = [ordered]@{
            BLOOMBERG_PYTHON = (Convert-ToForwardSlash $Python)
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
        [hashtable]$ServerConfig
    )

    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claude) {
        Write-Warning "Claude Code CLI not found on PATH; skipped 'claude mcp add-json'."
        return $false
    }

    $json = ($ServerConfig | ConvertTo-Json -Depth 20 -Compress)
    & $claude.Source mcp add-json --scope user $Name $json
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Claude Code CLI registration failed; Claude Desktop and project configs may still be usable."
        return $false
    }
    return $true
}

function Test-BloombergConnection {
    param(
        [string]$Python,
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

    $output = & $Python -c $probeScript 2>&1
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

Write-Step "Selecting Python"
$selectedPython = Get-BloombergPython $repoRoot
Write-Host "Python: $selectedPython"

$version = & $selectedPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
Write-Host "Python version: $version"

if (-not $SkipPackageInstall) {
    Install-Packages $selectedPython
}

if (-not $SkipEnvVars) {
    Write-Step "Persisting user environment variables"
    [Environment]::SetEnvironmentVariable("BLOOMBERG_PYTHON", (Convert-ToForwardSlash $selectedPython), "User")
    [Environment]::SetEnvironmentVariable("BLOOMBERG_MCP_HOME", (Convert-ToForwardSlash $repoRoot), "User")
    $env:BLOOMBERG_PYTHON = Convert-ToForwardSlash $selectedPython
    $env:BLOOMBERG_MCP_HOME = Convert-ToForwardSlash $repoRoot
}

$serverConfig = New-McpServerConfig $selectedPython $launcher $repoRoot

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

if (-not $SkipClaudeCode) {
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
    $connected = Test-BloombergConnection $selectedPython $repoRoot $ProbeTimeoutSec
    if ($connected) {
        Write-Host "Bloomberg API connected."
    } else {
        Write-Warning "Bloomberg API probe did not connect within ${ProbeTimeoutSec}s. The MCP config was still written."
    }
}

Write-Step "Done"
Write-Host "MCP server '$ServerName' is configured."
Write-Host "Restart Claude Desktop, Claude Code, or Codex so the new MCP config is loaded."
