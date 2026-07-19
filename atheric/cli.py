"""Command-line entry point for the Atheric dataset pipeline.

Usage:
    python main.py all                    # full pipeline -> MainDataset.csv
    python main.py ohlcv technical        # run specific stages, in order
    python main.py all --use-cache        # reuse raw files already on disk

Stages (in canonical order):
    ohlcv, technical, fundamentals, funda-features,
    macro, macro-features, foreign-flow, news, sentiment, align

Stage tambahan (TIDAK ikut 'all', harus dipanggil manual):
    sentiment-backfill  -- backfill sentimen historis via Wayback Machine,
                           sekali-jalan (lihat PROPOSAL_HISTORICAL_SENTIMENT.md)
"""

from __future__ import annotations

import argparse
import sys
import time

import pandas as pd

from .config import Config, load_config, load_dotenv
from .utils.logging_utils import get_logger, setup_logging

log = get_logger("atheric")

STAGE_ORDER = ["ohlcv", "technical", "fundamentals", "funda-features",
               "macro", "macro-features", "foreign-flow", "news", "sentiment",
               "align"]

# Stage manual-only: TIDAK pernah ikut ter-trigger oleh 'all' atau cron
# harian/4-jam-an. Dipanggil eksplisit: `python main.py sentiment-backfill`
EXTRA_STAGES = ["sentiment-backfill"]
ALL_STAGES = STAGE_ORDER + EXTRA_STAGES


def _cached(cfg: Config, output_key: str, parse_dates: list[str]) -> pd.DataFrame | None:
    path = cfg.output_path(output_key)
    if path.exists():
        log.info("cache hit: %s", path)
        df = pd.read_csv(path)
        for col in parse_dates:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
        return df
    return None


def run_stage(stage: str, cfg: Config, use_cache: bool) -> None:
    from .features import fundamental as f_funda
    from .features import macro as f_macro
    from .features import sentiment as f_sent
    from .features import technical as f_tech
    from .pipeline import align
    from .scrapers import fundamentals as s_funda
    from .scrapers import macro as s_macro
    from .scrapers import news as s_news
    from .scrapers import ohlcv as s_ohlcv

    started = time.monotonic()
    log.info("=== stage: %s ===", stage)

    if stage == "ohlcv":
        if not (use_cache and _cached(cfg, "raw_teknikal", ["date"]) is not None):
            s_ohlcv.run(cfg)
    elif stage == "technical":
        f_tech.run(cfg)
    elif stage == "fundamentals":
        if not (use_cache and _cached(cfg, "raw_funda", []) is not None):
            s_funda.run(cfg)
    elif stage == "funda-features":
        f_funda.run(cfg)
    elif stage == "macro":
        cache = cfg.output_path("raw_macro_dir") / "_all_series.csv"
        if not (use_cache and cache.exists()):
            s_macro.run(cfg)
        else:
            log.info("cache hit: %s", cache)
    elif stage == "macro-features":
        f_macro.run(cfg)
    elif stage == "foreign-flow":
        # scraper inkremental: hanya tanggal yang belum ada yang di-fetch,
        # jadi selalu dijalankan (pola sama dgn stage news)
        from .features import foreign_flow as f_ff
        from .scrapers import foreign_flow as s_ff
        s_ff.run(cfg)
        f_ff.run(cfg)
    elif stage == "news":
        s_news.run(cfg)  # always refresh: articles store is cumulative
    elif stage == "sentiment":
        f_sent.run(cfg)
    elif stage == "sentiment-backfill":
        # manual-only, sekali-jalan -- lihat PROPOSAL_HISTORICAL_SENTIMENT.md
        from .scrapers import news_historical as s_hist
        s_hist.run(cfg)
    elif stage == "align":
        align.run(cfg)
    else:
        raise ValueError(f"unknown stage: {stage}")

    log.info("=== stage %s done in %.1fs ===", stage, time.monotonic() - started)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="atheric",
        description="IDX Kompas100 forecasting-dataset pipeline (per data.pdf architecture)",
    )
    parser.add_argument("stages", nargs="+",
                        help=f"stages to run, or 'all' — order: {', '.join(STAGE_ORDER)}. "
                             f"Manual-only (not in 'all'): {', '.join(EXTRA_STAGES)}")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    parser.add_argument("--use-cache", action="store_true",
                        help="skip scraping stages whose raw output already exists")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    load_dotenv(cfg.root)
    setup_logging(cfg.path("paths.log_dir"))

    # 'all' TIDAK PERNAH memasukkan EXTRA_STAGES (sentiment-backfill) --
    # stage itu cuma jalan kalau diminta eksplisit lewat argumen.
    stages = STAGE_ORDER if "all" in args.stages else args.stages
    invalid = [s for s in stages if s not in ALL_STAGES]
    if invalid:
        parser.error(f"unknown stage(s): {', '.join(invalid)}")
    stages = [s for s in ALL_STAGES if s in stages]

    log.info("pipeline start — stages: %s", " -> ".join(stages))
    t0 = time.monotonic()
    for stage in stages:
        run_stage(stage, cfg, args.use_cache)
    log.info("pipeline finished in %.1f min", (time.monotonic() - t0) / 60.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
