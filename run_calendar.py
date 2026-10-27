#!/usr/bin/env python3
"""Portable launcher for Calendar Scheduler.

Prefer uv when it is available, but do not rely on an interactive shell PATH.
When uv cannot be found, create a cached virtual environment, install the
skill requirements, and run scripts/main.py from the skill root.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


MAIN_SCRIPT = Path("scripts") / "main.py"
NO_UV_ENV = "CALENDAR_SCHEDULER_NO_UV"
UV_BIN_ENV = "UV_BIN"
SELF_TEST_ARG = "--launcher-self-test"


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _normalize_path_value(value: str) -> str:
    """Normalize user-provided paths for the current platform."""
    expanded = os.path.expandvars(os.path.expanduser(value.strip().strip("'\"")))
    if os.name == "nt" and len(expanded) >= 2 and expanded[0] == "/" and expanded[1].isalpha():
        rest = expanded[2:]
        if not rest or rest.startswith("/"):
            return f"{expanded[1].upper()}:{rest}".replace("/", "\\")
    return expanded


def _safe_home() -> Path | None:
    try:
        return Path.home()
    except RuntimeError:
        pass

    for env_name in ("USERPROFILE", "HOME"):
        value = os.environ.get(env_name)
        if not value:
            continue
        candidate = Path(_normalize_path_value(value))
        if candidate.exists():
            return candidate

    return None


def _which_or_path(value: str) -> Path | None:
    expanded = _normalize_path_value(value)
    found = shutil.which(expanded)
    if found:
        return Path(found)

    candidate = Path(expanded)
    if _is_executable(candidate):
        return candidate

    if os.name == "nt" and candidate.suffix.lower() != ".exe":
        exe_candidate = candidate.with_suffix(candidate.suffix + ".exe") if candidate.suffix else Path(str(candidate) + ".exe")
        if _is_executable(exe_candidate):
            return exe_candidate

    return None


def find_uv() -> Path | None:
    """Find uv without assuming user shell startup files were loaded."""
    if os.environ.get(NO_UV_ENV):
        return None

    explicit = os.environ.get(UV_BIN_ENV)
    if explicit:
        uv = _which_or_path(explicit)
        if uv:
            return uv
        print(f"Warning: {UV_BIN_ENV}={explicit!r} is missing or not executable; continuing uv discovery.", file=sys.stderr)

    uv_from_path = shutil.which("uv") or shutil.which("uv.exe")
    if uv_from_path:
        return Path(uv_from_path)

    candidates = [
        Path("/opt/homebrew/bin/uv"),
        Path("/usr/local/bin/uv"),
    ]

    home = _safe_home()
    if home:
        candidates.extend(
            [
                home / ".local" / "bin" / "uv",
                home / ".local" / "bin" / "uv.exe",
                home / ".cargo" / "bin" / "uv",
                home / ".cargo" / "bin" / "uv.exe",
            ]
        )

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        local_app_data_path = Path(_normalize_path_value(local_app_data))
        candidates.extend(
            [
                local_app_data_path / "uv" / "uv.exe",
                local_app_data_path / "Programs" / "uv" / "uv.exe",
            ]
        )

    for candidate in candidates:
        if _is_executable(candidate):
            return candidate

    return None


def cache_root() -> Path:
    override = os.environ.get("CALENDAR_SCHEDULER_CACHE")
    if override:
        return Path(_normalize_path_value(override))

    if os.environ.get("XDG_CACHE_HOME"):
        return Path(_normalize_path_value(os.environ["XDG_CACHE_HOME"])) / "opencode-calendar-scheduler"

    if os.environ.get("LOCALAPPDATA"):
        return Path(_normalize_path_value(os.environ["LOCALAPPDATA"])) / "opencode-calendar-scheduler"

    home = _safe_home()
    if home:
        return home / ".cache" / "opencode-calendar-scheduler"

    return Path(tempfile.gettempdir()) / "opencode-calendar-scheduler"


def venv_dir() -> Path:
    version = f"py{sys.version_info.major}{sys.version_info.minor}"
    return cache_root() / version


def venv_python(directory: Path) -> Path:
    if os.name == "nt":
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


def ensure_venv_python(directory: Path) -> Path:
    python = venv_python(directory)
    if python.exists():
        return python

    directory.parent.mkdir(parents=True, exist_ok=True)
    print(f"uv not found; creating cached virtual environment: {directory}", file=sys.stderr)
    subprocess.run([sys.executable, "-m", "venv", str(directory)], check=True)
    return python


def _read_pyproject_dependencies(pyproject: Path) -> list[str]:
    try:
        import tomllib
    except ModuleNotFoundError:
        return []

    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    deps = data.get("project", {}).get("dependencies", [])
    return [dep for dep in deps if isinstance(dep, str) and dep.strip()]


def dependency_install_args(skill_dir: Path) -> tuple[list[str], str]:
    requirements = skill_dir / "requirements.txt"
    if requirements.exists():
        return ["-r", str(requirements)], _file_digest(requirements)

    pyproject = skill_dir / "pyproject.toml"
    if pyproject.exists():
        deps = _read_pyproject_dependencies(pyproject)
        if deps:
            return deps, _file_digest(pyproject)

    return [], "no-dependencies"


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_marker(directory: Path) -> Path:
    return directory / ".calendar-requirements.sha256"


def ensure_dependencies(python: Path, skill_dir: Path, directory: Path) -> None:
    install_args, digest = dependency_install_args(skill_dir)
    if not install_args:
        return

    marker = install_marker(directory)
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == digest:
        return

    print("Installing Calendar dependencies from skill metadata.", file=sys.stderr)
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            *install_args,
        ],
        check=True,
    )
    marker.write_text(digest + "\n", encoding="utf-8")


def run_with_uv(uv: Path, skill_dir: Path, args: list[str]) -> int:
    return subprocess.run([str(uv), "run", "python", str(MAIN_SCRIPT), *args], cwd=str(skill_dir), check=False).returncode


def run_with_venv(skill_dir: Path, main_script: Path, args: list[str]) -> int:
    directory = venv_dir()
    python = ensure_venv_python(directory)
    ensure_dependencies(python, skill_dir, directory)
    return subprocess.run([str(python), str(main_script), *args], cwd=str(skill_dir), check=False).returncode


def self_test(skill_dir: Path, main_script: Path) -> int:
    uv = find_uv()
    print(f"skill_dir={skill_dir}")
    print(f"main_script={main_script}")
    print("auth=not-run")
    print("api=not-run")
    print("dependency_install=not-run")
    if uv:
        print("mode=uv")
        print(f"uv={uv}")
    else:
        directory = venv_dir()
        install_args, _ = dependency_install_args(skill_dir)
        print("mode=venv")
        print(f"fallback=uv-not-found-or-disabled")
        print(f"venv_dir={directory}")
        print(f"venv_python={venv_python(directory)}")
        print("dependency_source=" + (" ".join(install_args) if install_args else "none"))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    skill_dir = Path(__file__).resolve().parent
    main_script = skill_dir / MAIN_SCRIPT

    if not main_script.exists():
        print(f"Error: Calendar main script not found: {main_script}", file=sys.stderr)
        return 2

    if args and args[0] == SELF_TEST_ARG:
        return self_test(skill_dir, main_script)

    uv = find_uv()
    if uv:
        return run_with_uv(uv, skill_dir, args)

    try:
        return run_with_venv(skill_dir, main_script, args)
    except subprocess.CalledProcessError as exc:
        print(f"Error: failed to start Calendar environment; exit code {exc.returncode}.", file=sys.stderr)
        print(f"Install uv or set {UV_BIN_ENV}=/path/to/uv to use uv explicitly.", file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
