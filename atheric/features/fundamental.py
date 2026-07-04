"""Fundamental feature engineering ("Hitung Ratio dan Growth").

From RawFunda.csv computes, per (ticker, report_period):
- profitability margins (gross / operating / net)
- balance-sheet ratios (current ratio, D/E, D/A, cash ratio, equity ratio)
- trailing-twelve-month (TTM) flows and ROA / ROE
- growth: YoY and QoQ for revenue, net income, EPS
- book value per share (for valuation ratios computed at alignment time)

Rows stay keyed by report_period with the (estimated) release_date used for
point-in-time joining downstream.

Output: DatasetFunda.csv
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from ..utils.logging_utils import get_logger

log = get_logger(__name__)

_FLOW_FIELDS = ["revenue", "cogs", "gross_profit", "operating_profit", "net_income", "eps"]


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    return num / den.replace(0.0, np.nan)


def _row_ratios(df: pd.DataFrame) -> pd.DataFrame:
    revenue = df["revenue"]
    gross = df["gross_profit"]
    if "cogs" in df.columns:
        gross = gross.fillna(revenue - df["cogs"])
    df["gross_margin"] = _safe_div(gross, revenue)
    df["operating_margin"] = _safe_div(df["operating_profit"], revenue)
    df["net_margin"] = _safe_div(df["net_income"], revenue)

    df["current_ratio"] = _safe_div(df["current_assets"], df["current_liabilities"])
    df["debt_to_equity"] = _safe_div(df["total_debt"], df["total_equity"])
    df["debt_to_assets"] = _safe_div(df["total_debt"], df["total_assets"])
    df["cash_to_assets"] = _safe_div(df["cash_equivalents"], df["total_assets"])
    df["equity_ratio"] = _safe_div(df["total_equity"], df["total_assets"])
    df["bvps"] = _safe_div(df["total_equity"], df["shares_out"])
    return df


def _ttm(series: pd.Series, periods: pd.Series, quarters: int) -> pd.Series:
    """Rolling sum of the last `quarters` quarterly values, requiring the
    window to actually span ~one year (guards against reporting gaps)."""
    total = series.rolling(quarters, min_periods=quarters).sum()
    span_days = (periods - periods.shift(quarters - 1)).dt.days
    valid = (span_days >= 250) & (span_days <= 420)
    return total.where(valid)


def _per_ticker(group: pd.DataFrame, ttm_quarters: int, yoy_quarters: int) -> pd.DataFrame:
    group = group.sort_values("report_period").reset_index(drop=True)
    is_q = group["freq"] == "Q"

    # ---- TTM flows (quarterly rows only) ----
    q = group[is_q]
    for field in ("revenue", "net_income", "eps"):
        col = f"{field}_ttm"
        group[col] = np.nan
        if not q.empty:
            group.loc[is_q, col] = _ttm(q[field], q["report_period"], ttm_quarters).to_numpy()
        # annual statements are already a full-year flow
        group.loc[~is_q, col] = group.loc[~is_q, field]

    # ---- ROA / ROE on TTM income vs average capital ----
    for name, base in (("roa", "total_assets"), ("roe", "total_equity")):
        avg_base = (group[base] + group.groupby(group["freq"])[base].shift(1)) / 2.0
        avg_base = avg_base.fillna(group[base])
        group[name] = _safe_div(group["net_income_ttm"], avg_base)

    # ---- growth ----
    for field in ("revenue", "net_income", "eps"):
        for freq_val, shift_n, label in (("Q", yoy_quarters, "yoy"), ("A", 1, "yoy")):
            mask = group["freq"] == freq_val
            prev = group.loc[mask, field].shift(shift_n)
            group.loc[mask, f"{field}_{label}"] = (_safe_div(group.loc[mask, field] - prev,
                                                             prev.abs())).to_numpy()
        prev_q = group.loc[is_q, field].shift(1)
        group.loc[is_q, f"{field}_qoq"] = (_safe_div(group.loc[is_q, field] - prev_q,
                                                     prev_q.abs())).to_numpy()
    return group


def run(cfg: Config, raw: pd.DataFrame | None = None) -> pd.DataFrame:
    if raw is None:
        raw = pd.read_csv(cfg.output_path("raw_funda"),
                          parse_dates=["report_period", "release_date"])
    else:
        raw = raw.copy()
        raw["report_period"] = pd.to_datetime(raw["report_period"])
        raw["release_date"] = pd.to_datetime(raw["release_date"])
    log.info("fundamental features: %d rows, %d tickers", len(raw), raw["ticker"].nunique())

    # ensure every canonical column exists even if a provider never returned it
    for col in _FLOW_FIELDS + ["pretax_income", "shares_out", "total_assets", "total_equity",
                               "total_liabilities", "current_assets", "current_liabilities",
                               "cash_equivalents", "total_debt"]:
        if col not in raw.columns:
            raw[col] = np.nan
        raw[col] = pd.to_numeric(raw[col], errors="coerce")

    raw["total_liabilities"] = raw["total_liabilities"].fillna(
        raw["total_assets"] - raw["total_equity"])

    ttm_quarters = int(cfg.get("fundamentals.ttm_quarters", 4))
    yoy_quarters = int(cfg.get("fundamentals.yoy_quarters", 4))

    frames = [
        _per_ticker(g, ttm_quarters, yoy_quarters)
        for _, g in raw.groupby("ticker", sort=True)
    ]
    out = pd.concat(frames, ignore_index=True)
    out = _row_ratios(out)
    out = out.sort_values(["ticker", "report_period"]).reset_index(drop=True)

    path = cfg.output_path("dataset_funda")
    out.to_csv(path, index=False, float_format=str(cfg.get("alignment.float_format", "%.6g")))
    log.info("DatasetFunda.csv: %d rows, %d cols -> %s", len(out), out.shape[1], path)
    return out
