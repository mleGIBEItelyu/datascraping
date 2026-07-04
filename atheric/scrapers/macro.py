"""Macro & commodity series scraper.

Every series in ``macro.series`` declares an ordered source chain; the first
driver that returns data wins. Supported drivers:

- ``fred_api``  : official FRED API (api.stlouisfed.org, needs FRED_API_KEY)
- ``dbnomics``  : DBnomics REST API, no key required (mirrors IMF/BIS/BLS/FED)
- ``yfinance``  : daily close of a Yahoo Finance ticker

Only free, programmatic sources are used — there is no manual/CSV fallback.

Each fetched series is stored raw under ``data/raw/macro/<name>.csv`` with
columns: date (observation period end), value, effective_date (date the
figure became publicly known = date + publication_lag_days).

A series whose entire source chain fails is skipped with a warning and the
pipeline keeps going.
"""

from __future__ import annotations

import os

import pandas as pd
import yfinance as yf

from ..config import Config
from ..utils.http_utils import HttpClient
from ..utils.logging_utils import get_logger

log = get_logger(__name__)


# --------------------------------------------------------------------------
# Drivers — each returns a DataFrame(date, value) or raises / returns empty
# --------------------------------------------------------------------------
def _fetch_fred_api(http: HttpClient, cfg: Config, source: dict) -> pd.DataFrame:
    key_env = str(cfg.get("macro.fred_api.api_key_env", "FRED_API_KEY"))
    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"{key_env} not set")
    payload = http.get_json(
        str(cfg.require("macro.fred_api.base_url")),
        params={"series_id": source["series_id"], "api_key": api_key,
                "file_type": "json", "observation_start": "1990-01-01"},
    )
    rows = [(obs["date"], obs["value"]) for obs in payload.get("observations", [])
            if obs.get("value") not in (None, ".", "")]
    df = pd.DataFrame(rows, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna()


def _fetch_dbnomics(http: HttpClient, cfg: Config, source: dict) -> pd.DataFrame:
    base = str(cfg.require("macro.dbnomics.base_url")).rstrip("/")
    payload = http.get_json(f"{base}/{source['series_id']}",
                            params={"observations": 1, "format": "json"})
    docs = payload.get("series", {}).get("docs", [])
    if not docs:
        return pd.DataFrame()
    doc = docs[0]
    df = pd.DataFrame({"date": doc.get("period_start_day", doc.get("period")),
                       "value": doc.get("value")})
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna()
    # monthly observations are stamped at period start; move to period end
    freq = str(source.get("frequency", "")).lower()
    if freq == "monthly" or (doc.get("@frequency") in ("monthly", "M")):
        df["date"] = df["date"] + pd.offsets.MonthEnd(0)
    return df


def _fetch_yfinance(source: dict) -> pd.DataFrame:
    hist = yf.Ticker(source["ticker"]).history(period="max", interval="1d",
                                               auto_adjust=False)
    if hist is None or hist.empty:
        return pd.DataFrame()
    idx = pd.to_datetime(hist.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df = pd.DataFrame({"date": idx.normalize(), "value": hist["Close"].to_numpy()})
    return df.dropna()


def _apply_transform(df: pd.DataFrame, transform: str) -> pd.DataFrame:
    if transform == "yoy_pct":
        df = df.sort_values("date").reset_index(drop=True)
        prev = df.set_index("date")["value"]
        # match each observation with the one closest to 1 year earlier
        target = df["date"] - pd.DateOffset(years=1)
        matched = prev.reindex(target, method="nearest",
                               tolerance=pd.Timedelta(days=20)).to_numpy()
        df["value"] = (df["value"] - matched) / abs(matched) * 100.0
        df = df.dropna(subset=["value"])
    return df


# --------------------------------------------------------------------------
# Stage entry point
# --------------------------------------------------------------------------
def fetch_series(http: HttpClient, cfg: Config, spec: dict) -> pd.DataFrame | None:
    name = spec["name"]
    default_transform = str(spec.get("transform", "none"))
    default_lag = int(spec.get("publication_lag_days", 0))
    for source in spec.get("sources", []):
        driver = source.get("driver")
        try:
            if driver == "fred_api":
                df = _fetch_fred_api(http, cfg, source)
            elif driver == "dbnomics":
                df = _fetch_dbnomics(http, cfg, source)
            elif driver == "yfinance":
                df = _fetch_yfinance(source)
            else:
                log.warning("%s: unknown driver %s", name, driver)
                continue
        except Exception as exc:  # noqa: BLE001 - fall through the chain
            log.info("%s: driver %s unavailable (%s)", name, driver, exc)
            continue
        if df is None or df.empty:
            log.info("%s: driver %s returned no data", name, driver)
            continue
        df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
        transform = str(source.get("transform", default_transform))
        df = _apply_transform(df, transform)
        if df.empty:
            continue
        lag = int(source.get("publication_lag_days", default_lag))
        df["effective_date"] = df["date"] + pd.Timedelta(days=lag)
        df["series"] = name
        df["driver"] = driver
        df["frequency"] = str(source.get("frequency", spec.get("frequency", "unknown")))
        df["group"] = str(spec.get("group", "other"))
        log.info("%s: %d obs via %s (%s .. %s)", name, len(df), driver,
                 df["date"].iloc[0].date(), df["date"].iloc[-1].date())
        return df
    log.warning("%s: no source in chain produced data — skipped", name)
    return None


def run(cfg: Config) -> pd.DataFrame:
    http = HttpClient(cfg)
    raw_dir = cfg.output_path("raw_macro_dir")
    raw_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    for spec in cfg.get("macro.series", []):
        df = fetch_series(http, cfg, spec)
        if df is None:
            continue
        df.to_csv(raw_dir / f"{spec['name']}.csv", index=False)
        frames.append(df)
    if not frames:
        raise RuntimeError("Macro scraping produced no series at all")
    combined = pd.concat(frames, ignore_index=True)
    combined.to_csv(raw_dir / "_all_series.csv", index=False)
    log.info("macro raw: %d series, %d observations -> %s",
             combined["series"].nunique(), len(combined), raw_dir)
    return combined
