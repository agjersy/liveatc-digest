"""Filesystem locations for config, cache, and workspace data.

All paths can be overridden with the ``LIVEATC_DIGEST_HOME`` environment variable, which
keeps tests and portable installs self-contained.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir

APP_NAME = "liveatc-digest"
ENV_HOME = "LIVEATC_DIGEST_HOME"


def _home() -> Path | None:
    value = os.environ.get(ENV_HOME)
    return Path(value).expanduser() if value else None


def config_file() -> Path:
    home = _home()
    base = home if home else Path(user_config_dir(APP_NAME))
    return base / "config.toml"


def cache_dir() -> Path:
    home = _home()
    return (home / "cache") if home else Path(user_cache_dir(APP_NAME))


def data_dir() -> Path:
    home = _home()
    return (home / "data") if home else Path(user_data_dir(APP_NAME))


def workspace_db() -> Path:
    return data_dir() / "workspace.sqlite"
