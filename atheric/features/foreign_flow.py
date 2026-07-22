"""Fitur turunan foreign flow per-saham (Tugas B2).

Baca data/raw/foreign_flow.csv (date, ticker, foreign_buy, foreign_sell,
foreign_net -- satuan volume saham) lalu hitung per ticker:

- ``foreign_net_zscore`` : rolling z-score foreign_net (window mengikuti
  pola window teknikal project ini; default 60 hari bursa, konfigurabel via
  ``foreign_flow.zscore_window``) -- menormalkan skala antar saham yang
  likuiditasnya sangat berbeda.
- ``foreign_net_5d_sum`` : akumulasi 5 hari bursa -- foreign flow harian
  noisy, akumulasi mingguan lebih bermakna sebagai sinyal.

Output: data/interim/DatasetForeignFlow.csv (long: date, ticker, ...),
di-join ke MainDataset oleh pipeline/align.py pada (date, ticker).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from ..utils.logging_utils import get_logger

log = get_logger(__name__)

OUT_COLS = ["date", "ticker", "foreign_buy", "foreign_sell", "foreign_net",
            "foreign_net_zscore", "foreign_net_5d_sum"]


def run(cfg: Config) -> pd.DataFrame:
    raw_path = cfg.output_path("raw_foreign_flow")
    out_path = cfg.output_path("dataset_foreign_flow")

    if not raw_path.exists():
        log.warning("foreign_flow features: %s belum ada -- output kosong", raw_path)
        empty = pd.DataFrame(columns=OUT_COLS)
        empty.to_csv(out_path, index=False)
        return empty

    df = pd.read_csv(raw_path, parse_dates=["date"])
    if df.empty:
        log.warning("foreign_flow features: raw kosong -- output kosong")
        df = pd.DataFrame(columns=OUT_COLS)
        df.to_csv(out_path, index=False)
        return df

    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    window = int(cfg.get("foreign_flow.zscore_window", 60))
    min_periods = max(window // 2, 5)

    grp = df.groupby("ticker", group_keys=False)["foreign_net"]
    roll_mean = grp.transform(
        lambda s: s.rolling(window, min_periods=min_periods).mean())
    roll_std = grp.transform(
        lambda s: s.rolling(window, min_periods=min_periods).std())
    df["foreign_net_zscore"] = ((df["foreign_net"] - roll_mean)
                                / roll_std.replace(0.0, np.nan))
    df["foreign_net_5d_sum"] = grp.transform(
        lambda s: s.rolling(5, min_periods=5).sum())

    df = df[OUT_COLS]
    df.to_csv(out_path, index=False,
              float_format=str(cfg.get("alignment.float_format", "%.6g")))
    log.info("DatasetForeignFlow: %d baris x %d ticker (%s .. %s) -> %s",
             len(df), df["ticker"].nunique(),
             df["date"].min().date(), df["date"].max().date(), out_path)
    return df
