"""OHLCV + stock metadata scraper (yfinance).

Downloads the complete daily price history since listing ("max") for every
ticker in the universe, plus per-stock metadata (sector / sub-sector mapping,
IPO date) and the corporate-action history (splits, dividends).

Outputs
-------
- RawTeknikal.csv        : long table, one row per (ticker, date)
- metadata_saham.csv     : one row per ticker
- corporate_actions.csv  : long table of splits / dividends / other actions
"""

from __future__ import annotations

import time

import pandas as pd
import yfinance as yf

from ..config import Config, load_tickers
from ..utils.logging_utils import get_logger

log = get_logger(__name__)

_OHLCV_COLUMNS = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
    "Dividends": "dividends",
    "Stock Splits": "stock_splits",
}


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _normalize_frame(frame: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """yfinance frame -> tidy long frame for a single ticker."""
    if frame is None or frame.empty:
        return None
    df = frame.copy()
    df = df.rename(columns=_OHLCV_COLUMNS)
    keep = [c for c in _OHLCV_COLUMNS.values() if c in df.columns]
    df = df[keep]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index = df.index.normalize()
    df = df[~df.index.duplicated(keep="last")]
    df = df.dropna(subset=["close"])
    if df.empty:
        return None
    df.insert(0, "ticker", ticker)
    df = df.reset_index().rename(columns={"index": "date", "Date": "date"})
    return df


def _download_batch(batch: list[str], cfg: Config) -> dict[str, pd.DataFrame]:
    """Download one batch of tickers, return per-ticker normalized frames."""
    raw = yf.download(
        tickers=batch,
        period=str(cfg.get("ohlcv.period", "max")),
        interval=str(cfg.get("ohlcv.interval", "1d")),
        auto_adjust=bool(cfg.get("ohlcv.auto_adjust", False)),
        actions=True,
        group_by="ticker",
        threads=True,
        progress=False,
    )
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    for ticker in batch:
        try:
            sub = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        norm = _normalize_frame(sub, ticker)
        if norm is not None:
            out[ticker] = norm
    return out


def _fetch_metadata(tickers: list[dict[str, str]], first_dates: dict[str, pd.Timestamp],
                    delay: float) -> pd.DataFrame:
    """Sector / sub-sector / IPO date per ticker via yfinance .info."""
    rows = []
    for entry in tickers:
        ticker = entry["ticker"]
        sector = industry = long_name = None
        ipo_date = None
        try:
            info = yf.Ticker(ticker).info or {}
            sector = info.get("sector")
            industry = info.get("industry")
            long_name = info.get("longName")
            epoch = info.get("firstTradeDateEpochUtc")
            if epoch:
                ipo_date = pd.Timestamp(epoch, unit="s").normalize()
        except Exception as exc:  # noqa: BLE001 - metadata is best-effort
            log.warning("metadata fetch failed for %s: %s", ticker, exc)
        if ipo_date is None:
            ipo_date = first_dates.get(ticker)
        rows.append({
            "ticker": ticker,
            "name": long_name or entry.get("name"),
            "sector": sector,
            "sub_sector": industry,
            "ipo_date": ipo_date,
            "first_data_date": first_dates.get(ticker),
        })
        time.sleep(delay)
    return pd.DataFrame(rows)


def _corporate_actions(prices: pd.DataFrame) -> pd.DataFrame:
    """Extract split / dividend events from the price table."""
    events = []
    if "dividends" in prices.columns:
        div = prices.loc[prices["dividends"].fillna(0) > 0, ["ticker", "date", "dividends"]]
        for row in div.itertuples(index=False):
            events.append({"ticker": row.ticker, "date": row.date,
                           "action": "dividend", "value": row.dividends})
    if "stock_splits" in prices.columns:
        spl = prices.loc[prices["stock_splits"].fillna(0) != 0, ["ticker", "date", "stock_splits"]]
        for row in spl.itertuples(index=False):
            events.append({"ticker": row.ticker, "date": row.date,
                           "action": "stock_split", "value": row.stock_splits})
    if not events:
        return pd.DataFrame(columns=["ticker", "date", "action", "value"])
    return pd.DataFrame(events).sort_values(["ticker", "date"]).reset_index(drop=True)


def run(cfg: Config) -> pd.DataFrame:
    """Execute the OHLCV stage; returns the raw price table."""
    tickers = load_tickers(cfg)
    codes = [t["ticker"] for t in tickers]
    log.info("OHLCV stage: %d tickers, period=%s", len(codes), cfg.get("ohlcv.period"))

    batch_size = int(cfg.get("ohlcv.batch_size", 25))
    min_rows = int(cfg.get("ohlcv.min_rows", 60))
    frames: list[pd.DataFrame] = []
    fetched: set[str] = set()

    for i, batch in enumerate(_chunks(codes, batch_size), start=1):
        log.info("downloading batch %d (%d tickers)", i, len(batch))
        result = _download_batch(batch, cfg)
        # single-ticker retry for anything the batch call missed
        for missing in [t for t in batch if t not in result]:
            log.info("retrying %s individually", missing)
            try:
                hist = yf.Ticker(missing).history(
                    period=str(cfg.get("ohlcv.period", "max")),
                    interval=str(cfg.get("ohlcv.interval", "1d")),
                    auto_adjust=bool(cfg.get("ohlcv.auto_adjust", False)),
                )
                norm = _normalize_frame(hist, missing)
                if norm is not None:
                    result[missing] = norm
            except Exception as exc:  # noqa: BLE001
                log.warning("no OHLCV for %s: %s", missing, exc)
        for ticker, frame in result.items():
            if len(frame) < min_rows:
                log.warning("dropping %s: only %d rows (< min_rows=%d)", ticker, len(frame), min_rows)
                continue
            frames.append(frame)
            fetched.add(ticker)

    missing_final = sorted(set(codes) - fetched)
    if missing_final:
        log.warning("no usable OHLCV for %d tickers: %s", len(missing_final), ", ".join(missing_final))

    prices = pd.concat(frames, ignore_index=True).sort_values(["ticker", "date"]).reset_index(drop=True)
    first_dates = prices.groupby("ticker")["date"].min().to_dict()

    metadata = _fetch_metadata([t for t in tickers if t["ticker"] in fetched], first_dates,
                               delay=float(cfg.get("http.request_delay_seconds", 0.5)))
    actions = _corporate_actions(prices)

    raw_path = cfg.output_path("raw_teknikal")
    prices.to_csv(raw_path, index=False)
    metadata.to_csv(cfg.output_path("metadata_saham"), index=False)
    actions.to_csv(cfg.output_path("corporate_actions"), index=False)
    log.info("RawTeknikal.csv: %d rows x %d tickers -> %s", len(prices), prices["ticker"].nunique(), raw_path)
    return prices
