"""Fundamental statement scraper.

Primary provider: stockanalysis.com (SvelteKit ``__data.json`` endpoints,
decoded from their devalue-serialized payload). Fallback provider: yfinance
quarterly statements.

Per architecture, we collect income-statement items (revenue, COGS,
operating profit, net income, EPS) and balance-sheet items (total assets /
equity / liabilities, current assets / liabilities, cash & equivalents,
total debt) plus the mandatory metadata: report period and release date.

The sources do not publish the actual filing date, so ``release_date`` is
estimated as ``report_period + release_lag_days`` (configurable per
frequency, aligned with IDX/OJK reporting deadlines) and flagged in
``release_date_estimated``.

Output: RawFunda.csv — one row per (ticker, report_period, freq).
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf

from ..config import Config, load_tickers
from ..utils.http_utils import HttpClient
from ..utils.logging_utils import get_logger

log = get_logger(__name__)


# --------------------------------------------------------------------------
# stockanalysis.com provider
# --------------------------------------------------------------------------
def _devalue_resolve(flat: list, idx: int) -> Any:
    """Recursively materialize one node of a SvelteKit devalue array."""
    if idx < 0:  # -1 encodes undefined
        return None
    value = flat[idx]
    if isinstance(value, dict):
        return {k: _devalue_resolve(flat, i) for k, i in value.items()}
    if isinstance(value, list):
        return [_devalue_resolve(flat, i) if isinstance(i, int) else i for i in value]
    return value


def _fetch_statement(http: HttpClient, url: str) -> dict | None:
    resp = http.get(url)
    if resp.status_code != 200:
        return None
    payload = resp.json()
    nodes = [n for n in payload.get("nodes", []) if n and n.get("type") == "data"]
    if not nodes:
        return None
    root = _devalue_resolve(nodes[-1]["data"], 0)
    if not isinstance(root, dict) or "financialData" not in root:
        return None
    return root["financialData"]


def _statement_to_frame(fin: dict, field_map: dict[str, list[str]],
                        canonical_side: set[str]) -> pd.DataFrame:
    """financialData column-arrays -> frame indexed by report period."""
    datekeys = fin.get("datekey") or []
    if not datekeys:
        return pd.DataFrame()
    # annual payloads carry a synthetic "TTM" row — coerce drops it
    frame = {"report_period": pd.to_datetime(datekeys, errors="coerce"),
             "fiscal_year": fin.get("fiscalYear"),
             "fiscal_quarter": fin.get("fiscalQuarter")}
    for canonical, candidates in field_map.items():
        if canonical not in canonical_side:
            continue
        series = None
        for cand in candidates:
            values = fin.get(cand)
            if values is not None and any(v is not None for v in values):
                series = values
                break
        frame[canonical] = series if series is not None else [None] * len(datekeys)
    df = pd.DataFrame(frame)
    return df.dropna(subset=["report_period"])


_INCOME_FIELDS = {"revenue", "cogs", "gross_profit", "operating_profit",
                  "pretax_income", "net_income", "eps", "shares_out"}
_BALANCE_FIELDS = {"total_assets", "total_equity", "total_liabilities",
                   "current_assets", "current_liabilities",
                   "cash_equivalents", "total_debt"}


def fetch_stockanalysis(http: HttpClient, cfg: Config, symbol: str) -> pd.DataFrame:
    """All statements x configured periods for one IDX symbol (no suffix)."""
    field_map = cfg.require("fundamentals.field_map")
    income_url = cfg.require("fundamentals.stockanalysis.income_url")
    balance_url = cfg.require("fundamentals.stockanalysis.balance_url")
    frames = []
    for period in cfg.get("fundamentals.stockanalysis.periods", ["quarterly", "annual"]):
        inc = _fetch_statement(http, income_url.format(symbol=symbol, period=period))
        bal = _fetch_statement(http, balance_url.format(symbol=symbol, period=period))
        if inc is None and bal is None:
            continue
        inc_df = _statement_to_frame(inc, field_map, _INCOME_FIELDS) if inc else pd.DataFrame()
        bal_df = _statement_to_frame(bal, field_map, _BALANCE_FIELDS) if bal else pd.DataFrame()
        if inc_df.empty and bal_df.empty:
            continue
        if inc_df.empty:
            merged = bal_df
        elif bal_df.empty:
            merged = inc_df
        else:
            merged = pd.merge(inc_df, bal_df.drop(columns=["fiscal_year", "fiscal_quarter"],
                                                  errors="ignore"),
                              on="report_period", how="outer")
        merged["freq"] = "Q" if period == "quarterly" else "A"
        frames.append(merged)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["provider"] = "stockanalysis"
    return out


# --------------------------------------------------------------------------
# yfinance fallback provider
# --------------------------------------------------------------------------
_YF_INCOME_MAP = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "cogs": ["Cost Of Revenue"],
    "gross_profit": ["Gross Profit"],
    "operating_profit": ["Operating Income"],
    "pretax_income": ["Pretax Income"],
    "net_income": ["Net Income Common Stockholders", "Net Income"],
    "eps": ["Diluted EPS", "Basic EPS"],
    "shares_out": ["Diluted Average Shares", "Basic Average Shares"],
}
_YF_BALANCE_MAP = {
    "total_assets": ["Total Assets"],
    "total_equity": ["Stockholders Equity", "Common Stock Equity"],
    "total_liabilities": ["Total Liabilities Net Minority Interest"],
    "current_assets": ["Current Assets"],
    "current_liabilities": ["Current Liabilities"],
    "cash_equivalents": ["Cash And Cash Equivalents"],
    "total_debt": ["Total Debt"],
}


def _yf_extract(stmt: pd.DataFrame, mapping: dict[str, list[str]]) -> pd.DataFrame:
    if stmt is None or stmt.empty:
        return pd.DataFrame()
    rows = {}
    for canonical, candidates in mapping.items():
        for cand in candidates:
            if cand in stmt.index:
                rows[canonical] = stmt.loc[cand]
                break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.index)
    df.index.name = "report_period"
    return df.reset_index()


def fetch_yfinance(yf_ticker: str) -> pd.DataFrame:
    tk = yf.Ticker(yf_ticker)
    frames = []
    for freq, income, balance in (
        ("Q", tk.quarterly_income_stmt, tk.quarterly_balance_sheet),
        ("A", tk.income_stmt, tk.balance_sheet),
    ):
        inc_df = _yf_extract(income, _YF_INCOME_MAP)
        bal_df = _yf_extract(balance, _YF_BALANCE_MAP)
        if inc_df.empty and bal_df.empty:
            continue
        if inc_df.empty:
            merged = bal_df
        elif bal_df.empty:
            merged = inc_df
        else:
            merged = pd.merge(inc_df, bal_df, on="report_period", how="outer")
        merged["freq"] = freq
        frames.append(merged)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["provider"] = "yfinance"
    return out


# --------------------------------------------------------------------------
# Stage entry point
# --------------------------------------------------------------------------
def run(cfg: Config) -> pd.DataFrame:
    tickers = load_tickers(cfg)
    http = HttpClient(cfg)
    chain = cfg.get("fundamentals.provider_chain", ["stockanalysis", "yfinance"])
    lag_map = cfg.get("fundamentals.release_lag_days", {"quarterly": 45, "annual": 90})

    all_frames: list[pd.DataFrame] = []
    failed: list[str] = []
    for i, entry in enumerate(tickers, start=1):
        yf_code = entry["ticker"]                  # e.g. BBCA.JK
        symbol = yf_code.split(".")[0]             # e.g. BBCA
        df = pd.DataFrame()
        for provider in chain:
            try:
                if provider == "stockanalysis":
                    df = fetch_stockanalysis(http, cfg, symbol)
                elif provider == "yfinance":
                    df = fetch_yfinance(yf_code)
                else:
                    log.warning("unknown fundamentals provider: %s", provider)
                    continue
            except Exception as exc:  # noqa: BLE001 - continue down the chain
                log.warning("%s: provider %s failed: %s", symbol, provider, exc)
                df = pd.DataFrame()
            if not df.empty:
                break
        if df.empty:
            failed.append(symbol)
            continue
        df.insert(0, "ticker", yf_code)
        all_frames.append(df)
        if i % 10 == 0:
            log.info("fundamentals: %d/%d tickers processed", i, len(tickers))

    if failed:
        log.warning("no fundamentals for %d tickers: %s", len(failed), ", ".join(failed))
    if not all_frames:
        raise RuntimeError("Fundamental scraping produced no data at all")

    raw = pd.concat(all_frames, ignore_index=True)
    raw["report_period"] = pd.to_datetime(raw["report_period"])

    # prefer quarterly rows over annual for the same period, then dedupe
    raw = raw.sort_values(["ticker", "report_period", "freq"],
                          ascending=[True, True, False])  # 'Q' > 'A'
    raw = raw.drop_duplicates(subset=["ticker", "report_period"], keep="first")

    # mandatory metadata: estimated release date
    lag_q = int(lag_map.get("quarterly", 45))
    lag_a = int(lag_map.get("annual", 90))
    lag_days = raw["freq"].map({"Q": lag_q, "A": lag_a}).fillna(lag_a).astype(int)
    raw["release_date"] = raw["report_period"] + pd.to_timedelta(lag_days, unit="D")
    raw["release_date_estimated"] = 1

    raw = raw.sort_values(["ticker", "report_period"]).reset_index(drop=True)
    path = cfg.output_path("raw_funda")
    raw.to_csv(path, index=False)
    log.info("RawFunda.csv: %d rows, %d tickers -> %s", len(raw), raw["ticker"].nunique(), path)
    return raw
