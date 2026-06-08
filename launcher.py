"""Bloomberg MCP launcher.

Works with any Python 3.11+ environment that can reach a running Bloomberg
Terminal. On startup the launcher verifies its dependencies are importable and,
if any are missing, sets up the project environment automatically — there is no
manual `/bloomberg-setup` step required. It prefers `uv sync` (which honors the
lockfile and the Bloomberg package index for blpapi) and falls back to pip,
bootstrapping pip via ensurepip when the active venv lacks it.

xbbg provides all data tools (BDP/BDH/BDIB/BDS/BSRCH) and runs BQL in-process
via blpapi — no separate BQNT environment.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

# (import_name, pip_spec). pip_spec carries the SAME version constraints as
# pyproject.toml so the pip fallback can never resolve an incompatible version
# (e.g. xbbg 1.x dropped format="wide"; an unconstrained `pip install xbbg`
# would grab a breaking major). blpapi has no pip_spec — it ships from
# Bloomberg's package index, not PyPI, so it can only be installed via
# `uv sync` against pyproject.toml.
REQUIRED = [
    ("fastmcp", "fastmcp>=2.0.0"),
    ("pydantic", "pydantic>=2.0.0"),
    ("psutil", "psutil>=5.9.0"),
    ("pandas", "pandas>=2.0,<3"),
    ("xbbg", "xbbg[pandas]>=1.2.6,<2"),
    ("blpapi", None),
]


def _is_installed(import_name: str) -> bool:
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


def _project_root() -> Path:
    return Path(os.environ.get("BLOOMBERG_MCP_HOME") or Path(__file__).parent)


def _uv_sync(project_root: Path) -> bool:
    """Sync the project venv with uv. Returns True on success.

    This is the preferred installer: uv created the venv (so it has no pip),
    and uv sync resolves blpapi from the Bloomberg index declared in
    pyproject.toml. stderr is captured so it cannot corrupt the MCP stdio
    transport.
    """
    uv = shutil.which("uv") or os.environ.get("UV_PATH")
    if not uv:
        return False
    try:
        print("Bloomberg MCP: environment not set up — syncing with uv...", file=sys.stderr)
        subprocess.check_call(
            [uv, "sync", "--project", str(project_root)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return True
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"Bloomberg MCP: uv sync failed ({exc}); falling back to pip.", file=sys.stderr)
        return False


def _pip_install(packages: list[str]) -> None:
    """Install packages with pip, bootstrapping pip via ensurepip if missing."""
    if not _is_installed("pip"):
        try:
            subprocess.check_call(
                [sys.executable, "-m", "ensurepip", "--upgrade"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            importlib.invalidate_caches()
        except (subprocess.CalledProcessError, OSError):
            pass
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", *packages],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def bootstrap() -> None:
    """Ensure required packages are importable, installing them if missing."""
    missing = [(imp, spec) for imp, spec in REQUIRED if not _is_installed(imp)]
    if not missing:
        return

    names = [imp for imp, _ in missing]
    print(f"Bloomberg MCP: missing packages: {', '.join(names)}", file=sys.stderr)

    # Preferred path: uv sync installs everything from pyproject.toml — including
    # blpapi from the Bloomberg index — into the project's .venv.
    if _uv_sync(_project_root()):
        importlib.invalidate_caches()
    else:
        # Fallback for non-uv environments: pip-install what PyPI can provide,
        # using the pinned specs so the wrong major can't slip in.
        pip_pkgs = [spec for _imp, spec in missing if spec]
        if pip_pkgs:
            _pip_install(pip_pkgs)
            importlib.invalidate_caches()

    still_missing = [imp for imp, _ in REQUIRED if not _is_installed(imp)]
    if still_missing:
        print(
            "ERROR: Bloomberg MCP could not install: "
            f"{', '.join(still_missing)}. Run `uv sync` in the plugin directory "
            "(blpapi requires the Bloomberg package index).",
            file=sys.stderr,
        )
        sys.exit(1)
    print("Bloomberg MCP: environment ready.", file=sys.stderr)


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
