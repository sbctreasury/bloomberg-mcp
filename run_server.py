"""
Bloomberg MCP Server launcher — auto-bootstraps dependencies and runs the server.

Works with any Python 3.11+ environment. On Bloomberg Terminal machines,
use the pre-installed bqnt-3 Python directly:

    C:\\blp\\bqnt\\environments\\bqnt-3\\python.exe run_server.py

The launcher checks for required packages (fastmcp, pydantic) and installs
them automatically if missing. Optional packages (xbbg, polars-bloomberg)
are skipped — the server's 3-tier fallback handles BQL without them.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

# Packages required to run the server (package-name, import-name)
REQUIRED = [
    ("fastmcp", "fastmcp"),
    ("pydantic", "pydantic"),
    ("psutil", "psutil"),
]

# Optional — nice to have but server works without them
OPTIONAL = [
    ("pandas", "pandas"),
    ("xbbg", "xbbg"),
    ("polars-bloomberg", "polars_bloomberg"),
]


def _is_installed(import_name: str) -> bool:
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


def bootstrap() -> None:
    """Install missing required packages into the current Python environment."""
    missing = [
        pkg for pkg, imp in REQUIRED if not _is_installed(imp)
    ]
    if not missing:
        return

    # Suppress pip output to avoid corrupting MCP stdio transport
    print(f"Installing missing packages: {', '.join(missing)}", file=sys.stderr)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", *missing],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    print("Done.", file=sys.stderr)

    # Reload so the imports work in this process
    importlib.invalidate_caches()


def main() -> None:
    bootstrap()

    server_py = Path(__file__).parent / "server" / "server.py"
    if not server_py.exists():
        print(f"ERROR: server.py not found at {server_py}", file=sys.stderr)
        sys.exit(1)

    # Add server/ to path so local imports (bloomberg_client, etc.) work
    sys.path.insert(0, str(server_py.parent))

    # Import and run the server in-process (preserves stdio transport)
    import runpy
    runpy.run_path(str(server_py), run_name="__main__")


if __name__ == "__main__":
    main()
