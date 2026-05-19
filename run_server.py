"""Bloomberg MCP Server launcher.

Works with any Python 3.11+ environment. On Bloomberg Terminal machines, the
pre-installed bqnt-3 Python can run this launcher directly:

    C:\\blp\\bqnt\\environments\\bqnt-3\\python.exe run_server.py

The launcher checks for required packages and installs them automatically if
missing. xbbg is required for MCP data tools; BQL also keeps a bqnt-3
subprocess fallback when Bloomberg's in-process session is unhealthy.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

REQUIRED = [
    ("fastmcp", "fastmcp"),
    ("pydantic", "pydantic"),
    ("psutil", "psutil"),
    ("pandas", "pandas"),
    ("xbbg", "xbbg"),
]


def _is_installed(import_name: str) -> bool:
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


def bootstrap() -> None:
    """Install missing required packages into the current Python environment."""
    missing = [pkg for pkg, imp in REQUIRED if not _is_installed(imp)]
    if not missing:
        return

    # Suppress pip output to avoid corrupting MCP stdio transport.
    print(f"Installing missing packages: {', '.join(missing)}", file=sys.stderr)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", *missing],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    print("Done.", file=sys.stderr)
    importlib.invalidate_caches()


def main() -> None:
    bootstrap()

    server_py = Path(__file__).parent / "server" / "server.py"
    if not server_py.exists():
        print(f"ERROR: server.py not found at {server_py}", file=sys.stderr)
        sys.exit(1)

    sys.path.insert(0, str(server_py.parent))

    import runpy

    runpy.run_path(str(server_py), run_name="__main__")


if __name__ == "__main__":
    main()
