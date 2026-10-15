"""User settings (TOML file) and API key handling (environment, then OS keychain)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

import tomli_w
from pydantic import BaseModel, Field

from . import paths

KEYRING_SERVICE = "liveatc-digest"

# Provider name -> environment variable consulted before the keychain.
KEY_ENV_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "adsbexchange": "ADSBX_API_KEY",
    "flightaware": "AEROAPI_KEY",
}


class LiveATCSettings(BaseModel):
    user_agent: str = "liveatc-digest/0.1 (+https://github.com/agjersy/liveatc-digest)"
    retention_days: int = 30
    request_timeout_s: float = 60.0


class AsrSettings(BaseModel):
    provider: Literal["openai", "local-whisper"] = "openai"
    model: str | None = None


class LlmSettings(BaseModel):
    provider: Literal["openai", "anthropic"] = "openai"
    model: str = "gpt-4o-mini"


class AdsbSettings(BaseModel):
    provider: Literal["adsblol", "adsbexchange", "flightaware"] = "adsblol"


class Settings(BaseModel):
    cache_dir: Path = Field(default_factory=paths.cache_dir)
    workspace_db: Path = Field(default_factory=paths.workspace_db)
    liveatc: LiveATCSettings = Field(default_factory=LiveATCSettings)
    asr: AsrSettings = Field(default_factory=AsrSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    adsb: AdsbSettings = Field(default_factory=AdsbSettings)

    def to_toml(self) -> str:
        # TOML has no null: omit unset optional values so the file round-trips.
        return tomli_w.dumps(_drop_none(self.model_dump(mode="json")))


def _drop_none(value):
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    return value


def load_settings(path: Path | None = None) -> Settings:
    path = path or paths.config_file()
    if not path.exists():
        return Settings()
    with path.open("rb") as fh:
        return Settings.model_validate(tomllib.load(fh))


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    path = path or paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.to_toml(), encoding="utf-8")
    return path


def set_value(settings: Settings, dotted_key: str, value: str) -> Settings:
    """Return a copy of *settings* with ``section.key`` set from a string value."""
    data = settings.model_dump(mode="json")
    parts = dotted_key.split(".")
    node = data
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            raise KeyError(dotted_key)
        node = node[part]
    if parts[-1] not in node:
        raise KeyError(dotted_key)
    node[parts[-1]] = value
    return Settings.model_validate(data)


def get_api_key(provider: str) -> str | None:
    env_name = KEY_ENV_VARS.get(provider)
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, provider)
    except Exception:  # keyring backend unavailable (headless CI, etc.)
        return None


def set_api_key(provider: str, value: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, provider, value)
