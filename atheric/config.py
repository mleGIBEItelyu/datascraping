"""Configuration loading and path resolution.

The whole pipeline is driven by a single YAML file; this module loads it,
resolves every relative path against the project root and makes sure output
directories exist.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config") / "config.yaml"


def load_dotenv(root: Path) -> None:
    """Load ``KEY=VALUE`` pairs from ``.env`` (and ``.env.example`` as
    fallback defaults) into ``os.environ``.

    No third-party dependency: a minimal parser is enough for our needs.
    ``.env`` takes precedence over ``.env.example``.
    """
    for filename in (".env.example", ".env"):  # later file overrides earlier
        path = root / filename
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value:
                os.environ[key] = value


class Config:
    """Thin dict wrapper with dotted-path access: ``cfg.get("a.b.c")``."""

    def __init__(self, data: dict[str, Any], root: Path):
        self._data = data
        self.root = root

    def get(self, dotted_key: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted_key: str) -> Any:
        value = self.get(dotted_key, default=None)
        if value is None:
            raise KeyError(f"Missing required config key: {dotted_key}")
        return value

    def path(self, dotted_key: str) -> Path:
        """Resolve a config value as a path relative to the project root."""
        return (self.root / str(self.require(dotted_key))).resolve()

    def output_path(self, name: str) -> Path:
        """Resolve ``paths.outputs.<name>`` and create its parent directory."""
        p = self.path(f"paths.outputs.{name}")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def load_config(config_path: str | os.PathLike | None = None,
                root: str | os.PathLike | None = None) -> Config:
    root_path = Path(root).resolve() if root else Path.cwd()
    cfg_path = Path(config_path) if config_path else root_path / DEFAULT_CONFIG_PATH
    with open(cfg_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return Config(data, root_path)


def load_tickers(cfg: Config) -> list[dict[str, str]]:
    """Load the ticker universe: list of {ticker, name} dicts."""
    with open(cfg.path("paths.tickers_file"), encoding="utf-8") as fh:
        payload = json.load(fh)
    entries: list[dict[str, str]] = []
    for _country, items in payload.items():
        for item in items:
            ticker = str(item["ticker"]).strip().upper()
            if ticker:
                entries.append({"ticker": ticker, "name": str(item.get("name", "")).strip()})
    if not entries:
        raise ValueError("Ticker file contains no tickers")
    # de-duplicate, keep order
    seen: set[str] = set()
    unique = [e for e in entries if not (e["ticker"] in seen or seen.add(e["ticker"]))]
    return unique
