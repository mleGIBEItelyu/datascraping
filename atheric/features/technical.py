"""Technical feature engineering.

Implements the "Cleaning dan Adjust -> Konstruksi Universe -> Hitung
Indikator" stages of the architecture:

- price adjustment for corporate actions (via adjusted close factor)
- universe construction: liquidity flag + ARA/ARB (IDX auto-reject) flags
- momentum & trend, volume, oscillator, price structure, volatility,
  calendar and cross-sectional relative indicators

Input : RawTeknikal.csv (long OHLCV per ticker/date)
Output: DatasetTeknikal.csv
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from ..utils.logging_utils import get_logger

log = get_logger(__name__)


# --------------------------------------------------------------------------
# Cleaning & adjustment
# --------------------------------------------------------------------------
def clean_and_adjust(df: pd.DataFrame) -> pd.DataFrame:
    """Sanitize one ticker's OHLCV and back-adjust prices for corporate actions.

    Keeps raw close/volume for turnover and ARA/ARB detection; open, high,
    low, close are replaced by their adjusted counterparts (factor =
    adj_close / close) so returns are corporate-action consistent.
    """
    df = df.sort_values("date").drop_duplicates(subset="date", keep="last").copy()
    df = df[(df["close"] > 0) & df["close"].notna()]
    df["volume"] = df["volume"].fillna(0).clip(lower=0)

    df["raw_close"] = df["close"]
    df["turnover_idr"] = df["close"] * df["volume"]

    if "adj_close" in df.columns and df["adj_close"].notna().any():
        factor = (df["adj_close"] / df["close"]).replace([np.inf, -np.inf], np.nan)
        factor = factor.ffill().bfill().fillna(1.0)
    else:
        factor = pd.Series(1.0, index=df.index)
    for col in ("open", "high", "low", "close"):
        df[col] = df[col] * factor

    # guard against broken OHLC after adjustment
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------
# Universe construction
# --------------------------------------------------------------------------
def _price_limit_pct(price: pd.Series, tiers: list[dict]) -> pd.Series:
    """Map previous close to its IDX auto-reject percentage band."""
    limit = pd.Series(np.nan, index=price.index)
    for tier in tiers:
        mask = (price >= float(tier["min_price"])) & (price < float(tier["max_price"]))
        limit[mask] = float(tier["limit_pct"])
    return limit


def add_universe_flags(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Liquidity flag and ARA/ARB (limit-up / limit-down) flags."""
    window = int(cfg.get("universe.liquidity.turnover_window_days", 20))
    min_turnover = float(cfg.get("universe.liquidity.min_median_turnover_idr", 1e9))
    tiers = cfg.get("universe.price_limits.tiers", [])
    tolerance = float(cfg.get("universe.price_limits.tolerance", 0.95))

    median_turnover = df["turnover_idr"].rolling(window, min_periods=window // 2).median()
    df["is_liquid"] = (median_turnover >= min_turnover).astype("Int8")

    prev_raw_close = df["raw_close"].shift(1)
    raw_ret = df["raw_close"] / prev_raw_close - 1.0
    limit = _price_limit_pct(prev_raw_close, tiers) * tolerance
    df["ara_flag"] = ((raw_ret >= limit) & limit.notna()).astype("Int8")
    df["arb_flag"] = ((raw_ret <= -limit) & limit.notna()).astype("Int8")
    return df


# --------------------------------------------------------------------------
# Indicator helpers
# --------------------------------------------------------------------------
def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.where(avg_loss > 0, 100.0).where(avg_gain.notna(), np.nan)


def _mfi(df: pd.DataFrame, period: int) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    mf = tp * df["volume"]
    direction = np.sign(tp.diff())
    pos = mf.where(direction > 0, 0.0).rolling(period, min_periods=period).sum()
    neg = mf.where(direction < 0, 0.0).rolling(period, min_periods=period).sum()
    ratio = pos / neg.replace(0.0, np.nan)
    mfi = 100.0 - 100.0 / (1.0 + ratio)
    return mfi.where(neg > 0, 100.0).where(pos.notna(), np.nan)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def _rolling_slope(values: pd.Series, window: int) -> pd.Series:
    """OLS slope of y on t over a rolling window (vectorized via convolution)."""
    n = window
    t = np.arange(n, dtype=float)
    w = t - t.mean()                        # centered time weights
    denom = float((w ** 2).sum())
    y = values.to_numpy(dtype=float)
    if len(y) < n:
        return pd.Series(np.nan, index=values.index)
    num = np.convolve(y, w[::-1], mode="valid")  # sum(w_i * y_{t-n+1+i})
    slope = np.full(len(y), np.nan)
    slope[n - 1:] = num / denom
    # invalidate windows containing NaNs
    nan_mask = np.convolve(np.isnan(y).astype(float), np.ones(n), mode="valid") > 0
    slope[n - 1:][nan_mask] = np.nan
    return pd.Series(slope, index=values.index)


# --------------------------------------------------------------------------
# Per-ticker indicators
# --------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    close, volume = df["close"], df["volume"]
    log_ret = np.log(close / close.shift(1))

    # --- momentum & trend ---
    for n in cfg.get("technical.return_windows", [5, 20, 60, 250]):
        df[f"ret_{n}d"] = close / close.shift(int(n)) - 1.0
    macd_cfg = cfg.get("technical.macd", {})
    ema_fast = close.ewm(span=int(macd_cfg.get("fast", 12)), adjust=False).mean()
    ema_slow = close.ewm(span=int(macd_cfg.get("slow", 26)), adjust=False).mean()
    macd = ema_fast - ema_slow
    signal = macd.ewm(span=int(macd_cfg.get("signal", 9)), adjust=False).mean()
    df["macd_hist"] = macd - signal
    df["macd_hist_pct"] = df["macd_hist"] / close
    for n in cfg.get("technical.sma_windows", [20, 60]):
        sma = close.rolling(int(n), min_periods=int(n)).mean()
        df[f"price_sma{n}_ratio"] = close / sma

    # --- volume ---
    vol_window = int(cfg.get("technical.volume_sma_window", 20))
    vol_sma = volume.rolling(vol_window, min_periods=vol_window // 2).mean()
    df["volume_ratio"] = volume / vol_sma.replace(0.0, np.nan)
    obv = (np.sign(close.diff()).fillna(0.0) * volume).cumsum()
    obv_window = int(cfg.get("technical.obv_slope_window", 20))
    df["obv_slope_20d"] = _rolling_slope(obv, obv_window) / vol_sma.replace(0.0, np.nan)
    df["mfi_14"] = _mfi(df, int(cfg.get("technical.mfi_period", 14)))

    # --- oscillator ---
    df["rsi_14"] = _rsi(close, int(cfg.get("technical.rsi_period", 14)))

    # --- price structure ---
    hw = int(cfg.get("technical.high_52w_window", 252))
    rolling_high = df["high"].rolling(hw, min_periods=hw // 4).max()
    df["dist_52w_high"] = close / rolling_high - 1.0
    cw = int(cfg.get("technical.close_pos_window", 20))
    lo = df["low"].rolling(cw, min_periods=cw).min()
    hi = df["high"].rolling(cw, min_periods=cw).max()
    df["close_pos_20d"] = (close - lo) / (hi - lo).replace(0.0, np.nan)
    df["gap_open"] = df["open"] / close.shift(1) - 1.0

    # --- volatility ---
    atr_period = int(cfg.get("technical.atr_period", 14))
    df["atr_ratio_14"] = _atr(df, atr_period) / close
    rv_window = int(cfg.get("technical.realized_vol_window", 20))
    df["realized_vol_20d"] = log_ret.rolling(rv_window, min_periods=rv_window).std() * np.sqrt(252.0)
    vr = cfg.get("technical.vol_ratio", {})
    short_w, long_w = int(vr.get("short", 5)), int(vr.get("long", 60))
    vol_short = log_ret.rolling(short_w, min_periods=short_w).std()
    vol_long = log_ret.rolling(long_w, min_periods=long_w).std()
    df["vol_ratio_5_60"] = vol_short / vol_long.replace(0.0, np.nan)
    b = cfg.get("technical.bollinger", {})
    bw, bstd = int(b.get("window", 20)), float(b.get("num_std", 2.0))
    mid = close.rolling(bw, min_periods=bw).mean()
    band = close.rolling(bw, min_periods=bw).std() * bstd
    df["bollinger_pctb"] = (close - (mid - band)) / (2.0 * band).replace(0.0, np.nan)
    return df


def add_calendar_features(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    dates = pd.to_datetime(df["date"])
    df["day_of_week"] = dates.dt.dayofweek.astype("Int8")
    wd_month = int(cfg.get("technical.window_dressing.month", 12))
    last_n = int(cfg.get("technical.window_dressing.last_n_trading_days", 5))
    in_month = dates.dt.month == wd_month
    # rank trading days within each December from the end (per ticker)
    rank_from_end = dates.where(in_month).groupby(dates.dt.year).rank(method="first", ascending=False)
    df["window_dressing_flag"] = (in_month & (rank_from_end <= last_n)).astype("Int8")
    return df


# --------------------------------------------------------------------------
# Cross-sectional relative features
# --------------------------------------------------------------------------
def add_cross_sectional(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    min_stocks = int(cfg.get("technical.cross_section.min_stocks_per_day", 10))
    ret_windows = cfg.get("technical.cross_section.return_windows", [20, 60])
    targets = [f"ret_{int(n)}d" for n in ret_windows] + ["volume_ratio", "realized_vol_20d"]

    grouped = df.groupby("date")
    counts = grouped["ticker"].transform("size")
    for col in targets:
        if col not in df.columns:
            continue
        mean = grouped[col].transform("mean")
        std = grouped[col].transform("std")
        z = (df[col] - mean) / std.replace(0.0, np.nan)
        df[f"{col}_cs_z"] = z.where(counts >= min_stocks)
    return df


# --------------------------------------------------------------------------
# Stage entry point
# --------------------------------------------------------------------------
def run(cfg: Config, raw: pd.DataFrame | None = None) -> pd.DataFrame:
    if raw is None:
        raw = pd.read_csv(cfg.output_path("raw_teknikal"), parse_dates=["date"])
    else:
        raw = raw.copy()
        raw["date"] = pd.to_datetime(raw["date"])
    log.info("technical stage: %d rows, %d tickers", len(raw), raw["ticker"].nunique())

    frames = []
    for ticker, group in raw.groupby("ticker", sort=True):
        g = clean_and_adjust(group)
        g = add_universe_flags(g, cfg)
        g = compute_indicators(g, cfg)
        g = add_calendar_features(g, cfg)
        frames.append(g)
    out = pd.concat(frames, ignore_index=True)
    out = add_cross_sectional(out, cfg)

    drop_cols = [c for c in ("adj_close", "dividends", "stock_splits") if c in out.columns]
    out = out.drop(columns=drop_cols)
    out = out.sort_values(["ticker", "date"]).reset_index(drop=True)

    path = cfg.output_path("dataset_teknikal")
    out.to_csv(path, index=False, float_format=str(cfg.get("alignment.float_format", "%.6g")))
    log.info("DatasetTeknikal.csv: %d rows, %d cols -> %s", len(out), out.shape[1], path)
    return out
