from __future__ import annotations

import os
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BACKEND_DIR / "app.db"
DEFAULT_LIB_ROOT = BACKEND_DIR / "libraries"


def _clean_env(name: str) -> str:
    return os.getenv(name, "").strip()


def _path_from_env(name: str) -> Path | None:
    value = _clean_env(name)
    if not value:
        return None
    return Path(value).expanduser()


def _ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path | None:
    """
    Optional app-managed data root for packaged deployments.

    HEIMGEIST_DATA_DIR is internal sidecar plumbing. Normal users should not
    need to configure it manually, and development intentionally leaves it
    unset so data stays in backend/app.db and backend/libraries.
    """

    path = _path_from_env("HEIMGEIST_DATA_DIR")
    if path is None:
        return None
    return _ensure_dir(path)


def database_path() -> Path:
    """
    Resolve the SQLite database path.

    Precedence:
    1. HEIMGEIST_DB_PATH, for explicit packaged sidecar plumbing.
    2. HEIMGEIST_DATA_DIR/app.db, for packaged app-managed data.
    3. backend/app.db, for unchanged local development behavior.
    """

    explicit_path = _path_from_env("HEIMGEIST_DB_PATH")
    if explicit_path is not None:
        return _ensure_parent(explicit_path)

    base_dir = data_dir()
    if base_dir is not None:
        return _ensure_parent(base_dir / "app.db")

    return _ensure_parent(DEFAULT_DB_PATH)


def library_root() -> Path:
    """
    Resolve the local RAG library root.

    Precedence:
    1. HEIMGEIST_LIB_ROOT, for explicit packaged sidecar plumbing.
    2. HEIMGEIST_DATA_DIR/libraries, for packaged app-managed data.
    3. backend/libraries, for unchanged local development behavior.
    """

    explicit_path = _path_from_env("HEIMGEIST_LIB_ROOT")
    if explicit_path is not None:
        return _ensure_dir(explicit_path)

    base_dir = data_dir()
    if base_dir is not None:
        return _ensure_dir(base_dir / "libraries")

    return _ensure_dir(DEFAULT_LIB_ROOT)


def sqlite_database_url(path: Path | None = None) -> str:
    target = path or database_path()
    return f"sqlite:///{target.as_posix()}"
