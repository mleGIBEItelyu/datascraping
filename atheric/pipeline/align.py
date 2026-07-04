"""Time & entity alignment — the final join producing MainDataset.csv.

Spine: DatasetTeknikal (one row per trading date x ticker).
Joined onto it, all point-in-time safe:

- stock metadata      : sector / sub-sector / IPO date (static)
- DatasetFunda        : as-of join on (estimated) release_date per ticker,
                        capped by ``fundamental_max_staleness_days``;
                        valuation ratios (P/E TTM, P/BV) computed here
- DatasetMacro        : exact join on calendar date (levels already
                        forward-filled at their effective dates)
- DatasetMacroSector  : sector commodity factor per (date, sector)
- DatasetSentimen     : per (date, ticker) + market & sector sentiment
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from ..utils.logging_utils import get_logger

log = get_logger(__name__)


def _read_csv(path, parse_dates: list[str]) -> pd.DataFrame:
    if not path.exists():
        log.warning("missing input %s — continuing without it", path)
        return pd.DataFrame()
    df = pd.read_csv(path)
    for col in parse_dates:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
    return df


def _join_metadata(spine: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    meta = _read_csv(cfg.output_path("metadata_saham"), ["ipo_date", "first_data_date"])
    if meta.empty:
        return spine
    keep = [c for c in ("ticker", "sector", "sub_sector", "ipo_date") if c in meta.columns]
    return spine.merge(meta[keep], on="ticker", how="left")


def _join_fundamentals(spine: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    funda = _read_csv(cfg.output_path("dataset_funda"), ["report_period", "release_date"])
    if funda.empty:
        return spine
    cols = [c for c in cfg.get("alignment.fundamental_columns", []) if c in funda.columns]
    right = funda[["ticker", "report_period", "release_date"] + cols].copy()
    right = right.dropna(subset=["release_date"]).sort_values("release_date")
    right = right.rename(columns={"report_period": "funda_report_period",
                                  "release_date": "funda_release_date"})

    staleness = pd.Timedelta(days=int(cfg.get("alignment.fundamental_max_staleness_days", 400)))
    merged = pd.merge_asof(
        spine.sort_values("date"),
        right,
        left_on="date", right_on="funda_release_date",
        by="ticker", direction="backward", tolerance=staleness,
    )

    # valuation ratios: price (unadjusted) vs per-share fundamentals
    price = merged["raw_close"] if "raw_close" in merged.columns else merged["close"]
    if "eps_ttm" in merged.columns:
        eps = merged["eps_ttm"]
        merged["pe_ttm"] = (price / eps.replace(0.0, np.nan)).where(eps > 0)
    if "bvps" in merged.columns:
        bvps = merged["bvps"]
        merged["pbv"] = (price / bvps.replace(0.0, np.nan)).where(bvps > 0)
    return merged


def _join_macro(spine: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    macro = _read_csv(cfg.output_path("dataset_macro"), ["date"])
    if macro.empty:
        return spine
    return spine.merge(macro, on="date", how="left")


def _join_macro_sector(spine: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    sec = _read_csv(cfg.output_path("dataset_macro_sector"), ["date"])
    if sec.empty or "sector" not in spine.columns:
        return spine
    return spine.merge(sec, on=["date", "sector"], how="left")


def build_sentimen(cfg: Config) -> pd.DataFrame:
    """Assemble the standalone sentiment deliverable (``processed/sentimen.csv``).

    Long/tidy format so stock-, market- and sector-level rows coexist:
        date, level (stock|market|sector), entity, sent_score, sent_decay, news_count
    This file is intentionally NOT joined into MainDataset — sentiment is kept
    as a separate deliverable.
    """
    frames = []

    stock = _read_csv(cfg.output_path("dataset_sentimen"), ["date"])
    if not stock.empty:
        frames.append(pd.DataFrame({
            "date": stock["date"], "level": "stock", "entity": stock["ticker"],
            "sent_score": stock.get("sent_score"), "sent_decay": stock.get("sent_decay"),
            "news_count": stock.get("news_count"),
        }))

    market = _read_csv(cfg.output_path("dataset_sentimen_market"), ["date"])
    if not market.empty:
        frames.append(pd.DataFrame({
            "date": market["date"], "level": "market", "entity": "MARKET",
            "sent_score": market.get("market_sent"), "sent_decay": market.get("market_sent_decay"),
            "news_count": market.get("market_news_count"),
        }))

    sector = _read_csv(cfg.output_path("dataset_sentimen_sector"), ["date"])
    if not sector.empty:
        frames.append(pd.DataFrame({
            "date": sector["date"], "level": "sector", "entity": sector["sector"],
            "sent_score": pd.NA, "sent_decay": sector.get("sector_sent"), "news_count": pd.NA,
        }))

    cols = ["date", "level", "entity", "sent_score", "sent_decay", "news_count"]
    out = (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols))
    if not out.empty:
        out = out.sort_values(["level", "entity", "date"]).reset_index(drop=True)

    path = cfg.output_path("sentimen")
    out.to_csv(path, index=False, float_format=str(cfg.get("alignment.float_format", "%.6g")))
    by_level = out.groupby("level").size().to_dict() if not out.empty else {}
    log.info("sentimen.csv (separate deliverable): %d rows %s -> %s", len(out), by_level, path)
    return out


def _coverage_groups(columns: list[str]) -> dict[str, list[str]]:
    """Classify MainDataset columns into feature groups for a coverage report."""
    technical = ["ret_5d", "ret_20d", "ret_60d", "ret_250d", "macd_hist_pct", "rsi_14",
                 "mfi_14", "obv_slope_20d", "atr_ratio_14", "realized_vol_20d", "bollinger_pctb"]
    cross_sec = [c for c in columns if c.endswith("_cs_z")]
    fundamental = ["gross_margin", "operating_margin", "net_margin", "current_ratio",
                   "debt_to_equity", "roa", "roe", "revenue_yoy", "net_income_yoy",
                   "eps_yoy", "pe_ttm", "pbv"]
    macro = [c for c in columns if c in (
        "bi_rate", "id_cpi_yoy", "usd_idr", "id_fx_reserves", "fed_funds_rate",
        "us_cpi_yoy", "us_10y_yield", "dxy", "vix", "us_manufacturing_activity",
        "cpo", "nickel", "brent_oil", "gold", "copper", "tin", "rubber")]
    groups = {
        "technical": [c for c in technical if c in columns],
        "cross_sectional": cross_sec,
        "fundamental": [c for c in fundamental if c in columns],
        "macro": macro,
    }
    return {k: v for k, v in groups.items() if v}


def log_coverage(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Log & return per-year non-NaN coverage (%) per feature group.

    Makes explicit which date range is usable for a fundamental-bearing model
    (fundamentals only populate from ~2022 once 4 TTM quarters exist). No
    imputation is performed anywhere — historical NaNs are left honest.
    """
    groups = _coverage_groups(list(df.columns))
    years = df["date"].dt.year
    rows = []
    for year, idx in df.groupby(years).groups.items():
        block = df.loc[idx]
        rec = {"year": int(year), "rows": len(block)}
        for gname, cols in groups.items():
            rec[gname] = round(block[cols].notna().mean().mean() * 100.0, 1)
        rows.append(rec)
    coverage = pd.DataFrame(rows).sort_values("year").reset_index(drop=True)

    header = "year   rows   " + "  ".join(f"{g[:12]:>12s}" for g in groups)
    log.info("=== MainDataset coverage (%% non-NaN per feature group, per year) ===")
    log.info(header)
    for r in coverage.itertuples(index=False):
        line = f"{r.year}  {r.rows:6d}   " + "  ".join(
            f"{getattr(r, g):11.1f}%" for g in groups)
        log.info(line)
    log.info("Note: fundamentals populate from ~2022 (need 4 TTM quarters); "
             "rows before that are honest NaN and should be excluded when "
             "training a fundamental-bearing model.")
    cov_path = cfg.output_path("main_dataset").parent / "MainDataset_coverage.csv"
    coverage.to_csv(cov_path, index=False)
    log.info("coverage table -> %s", cov_path)
    return coverage


def run(cfg: Config) -> pd.DataFrame:
    spine = _read_csv(cfg.output_path("dataset_teknikal"), ["date"])
    if spine.empty:
        raise RuntimeError("DatasetTeknikal.csv missing — run the technical stage first")
    log.info("alignment: spine %d rows x %d tickers", len(spine), spine["ticker"].nunique())

    spine = _join_metadata(spine, cfg)
    spine = _join_fundamentals(spine, cfg)
    spine = _join_macro(spine, cfg)
    spine = _join_macro_sector(spine, cfg)
    # NOTE: sentiment is deliberately NOT joined here — MainDataset is purely
    # technical + fundamental + macro. Sentiment goes to processed/sentimen.csv.

    spine = spine.sort_values(["ticker", "date"]).reset_index(drop=True)

    path = cfg.output_path("main_dataset")
    spine.to_csv(path, index=False, float_format=str(cfg.get("alignment.float_format", "%.6g")))
    log.info("MainDataset.csv: %d rows x %d cols -> %s", len(spine), spine.shape[1], path)

    if bool(cfg.get("alignment.write_parquet", False)):
        try:
            spine.to_parquet(path.with_suffix(".parquet"), index=False)
        except Exception as exc:  # noqa: BLE001 - parquet is a nice-to-have
            log.warning("parquet write skipped: %s", exc)

    build_sentimen(cfg)
    log_coverage(spine, cfg)
    return spine
