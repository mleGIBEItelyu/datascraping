"""Macro feature engineering ("Align by Release Date -> Surprise, Perubahan,
Mapping ke Sektor").

Builds a daily macro table aligned on *effective dates* (observation date +
publication lag) so no figure is visible before it was actually released:

- ``<series>``            : latest publicly-known level (forward-filled)
- ``<series>_surprise``   : latest release minus the release before it
- ``<series>_chg_pct``    : percentage version of the surprise
- ``<series>_pct_{n}d``   : n-day change of the daily level (daily series)
- ``<series>_z252``       : rolling 252-day z-score of the level

Sector mapping: for every sector in ``macro.sector_commodity_map`` the
average rolling return of its mapped commodities becomes a sector-level
factor, written to DatasetMacroSector.csv (long: date, sector, value).

Outputs: DatasetMacro.csv, DatasetMacroSector.csv
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from ..utils.logging_utils import get_logger

log = get_logger(__name__)


def _load_raw(cfg: Config) -> pd.DataFrame:
    path = cfg.output_path("raw_macro_dir") / "_all_series.csv"
    df = pd.read_csv(path, parse_dates=["date", "effective_date"])
    return df


def _daily_series_features(obs: pd.DataFrame, calendar: pd.DatetimeIndex,
                           name: str, frequency: str,
                           change_windows: list[int], z_window: int,
                           staleness_days: int | None = None) -> pd.DataFrame:
    """Observation table for one series -> daily feature columns."""
    obs = obs.sort_values("effective_date").drop_duplicates("effective_date", keep="last")

    # release-level surprise (vs previous release)
    obs = obs.copy()
    prev = obs["value"].shift(1)
    obs["surprise"] = obs["value"] - prev
    obs["chg_pct"] = (obs["value"] - prev) / prev.abs().replace(0.0, np.nan)

    daily = obs.set_index("effective_date")[["value", "surprise", "chg_pct"]]
    daily = daily.reindex(calendar.union(daily.index)).ffill().reindex(calendar)

    out = pd.DataFrame(index=calendar)
    out[name] = daily["value"]
    out[f"{name}_surprise"] = daily["surprise"]
    out[f"{name}_chg_pct"] = daily["chg_pct"]
    if frequency == "daily":
        for n in change_windows:
            out[f"{name}_pct_{n}d"] = out[name].pct_change(int(n))
    roll = out[name].rolling(z_window, min_periods=z_window // 4)
    out[f"{name}_z252"] = (out[name] - roll.mean()) / roll.std().replace(0.0, np.nan)

    if staleness_days is not None:
        # age of the most recent real observation carried forward to each date
        obs_dates = pd.Series(obs["effective_date"].to_numpy(), index=obs["effective_date"])
        last_real = obs_dates.reindex(calendar.union(obs_dates.index)).ffill().reindex(calendar)
        age_days = (calendar.to_series().to_numpy() - last_real.to_numpy()) / np.timedelta64(1, "D")
        stale = (age_days > staleness_days)
        # where there is no observation yet (leading NaN), value is also NaN, not "stale"
        stale = np.where(out[name].isna().to_numpy(), np.nan, stale.astype(float))
        out[f"{name}_stale"] = pd.array(stale, dtype="Int8")
    return out


def run(cfg: Config, raw: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    if raw is None:
        raw = _load_raw(cfg)
    change_windows = [int(n) for n in cfg.get("macro.features.change_windows", [5, 20])]
    z_window = int(cfg.get("macro.features.zscore_window", 252))
    # per-series staleness thresholds declared in config
    staleness_map = {str(s["name"]): int(s["staleness_flag_days"])
                     for s in cfg.get("macro.series", [])
                     if s.get("staleness_flag_days") is not None}

    start = raw["effective_date"].min().normalize()
    end = max(raw["effective_date"].max(), pd.Timestamp.today()).normalize()
    calendar = pd.date_range(start, end, freq="D")

    blocks = []
    for name, obs in raw.groupby("series", sort=True):
        frequency = str(obs["frequency"].iloc[0]) if "frequency" in obs.columns else "unknown"
        blocks.append(_daily_series_features(obs, calendar, str(name), frequency,
                                             change_windows, z_window,
                                             staleness_map.get(str(name))))
    macro = pd.concat(blocks, axis=1)
    macro.index.name = "date"
    macro = macro.reset_index()

    path = cfg.output_path("dataset_macro")
    macro.to_csv(path, index=False, float_format=str(cfg.get("alignment.float_format", "%.6g")))
    log.info("DatasetMacro.csv: %d rows, %d cols -> %s", len(macro), macro.shape[1], path)

    # ---- sector mapping: commodity factor per sector ----
    sector_map: dict[str, list[str]] = cfg.get("macro.sector_commodity_map", {}) or {}
    window = int(cfg.get("macro.sector_return_window", 20))
    macro_idx = macro.set_index("date")
    sector_rows = []
    for sector, commodities in sector_map.items():
        cols = [c for c in commodities if c in macro_idx.columns]
        if not cols:
            log.warning("sector %s: none of %s available", sector, commodities)
            continue
        returns = macro_idx[cols].pct_change(window)
        factor = returns.mean(axis=1)
        sector_rows.append(pd.DataFrame({
            "date": macro_idx.index, "sector": sector,
            "sector_cmdty_ret": factor.to_numpy(),
        }))
    sector_df = (pd.concat(sector_rows, ignore_index=True)
                 if sector_rows else pd.DataFrame(columns=["date", "sector", "sector_cmdty_ret"]))
    spath = cfg.output_path("dataset_macro_sector")
    sector_df.to_csv(spath, index=False, float_format=str(cfg.get("alignment.float_format", "%.6g")))
    log.info("DatasetMacroSector.csv: %d rows -> %s", len(sector_df), spath)
    return macro, sector_df
