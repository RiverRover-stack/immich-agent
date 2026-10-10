"""Settings loaded from the environment and .env (never printed, never committed)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class ConfigError(Exception):
    """Raised when settings are missing or unsafe."""


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m:
            values[m.group(1)] = m.group(2).strip().strip("\"'")
    return values


@dataclass(frozen=True)
class Settings:
    immich_url: str
    api_key: str = ""

    def __repr__(self) -> str:  # keep the key out of logs and tracebacks
        return f"Settings(immich_url={self.immich_url!r}, api_key=<hidden>)"


def load_settings(env_file: Path | None = None) -> Settings:
    file_values = read_env_file(env_file or ROOT / ".env")

    def get(name: str, default: str = "") -> str:
        return os.environ.get(name) or file_values.get(name, default)

    url = get("IMMICH_URL", "http://127.0.0.1:2283").rstrip("/")
    host = httpx.URL(url).host
    if host not in LOCAL_HOSTS:
        raise ConfigError(
            f"refusing non-local target {host!r}: ShutterStorm only targets the local sandbox"
        )
    return Settings(immich_url=url, api_key=get("IMMICH_API_KEY"))
