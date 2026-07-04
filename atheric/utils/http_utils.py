"""Shared HTTP session with retry, backoff and polite request pacing."""

from __future__ import annotations

import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import Config


class HttpClient:
    """requests.Session wrapper honouring the ``http`` section of the config."""

    def __init__(self, cfg: Config):
        self.timeout = float(cfg.get("http.timeout_seconds", 30))
        self.delay = float(cfg.get("http.request_delay_seconds", 0.5))
        self._last_request_ts = 0.0

        retry = Retry(
            total=int(cfg.get("http.max_retries", 3)),
            backoff_factor=float(cfg.get("http.backoff_factor", 1.5)),
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10)
        self.session = requests.Session()
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({"User-Agent": str(cfg.get("http.user_agent", "atheric-pipeline"))})

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_ts = time.monotonic()

    def get(self, url: str, **kwargs) -> requests.Response:
        self._pace()
        kwargs.setdefault("timeout", self.timeout)
        return self.session.get(url, **kwargs)

    def get_json(self, url: str, **kwargs) -> dict:
        resp = self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()
