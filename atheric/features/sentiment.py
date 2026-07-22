"""Sentiment feature engineering.

Pipeline (per architecture): raw articles -> lexicon polarity scoring ->
entity mapping (ticker / sector) -> daily aggregation per stock with
exponential decay -> DatasetSentimen.csv (+ market- and sector-level tables).

Scoring: bag-of-words polarity with negation flipping, using the
Indonesian/English financial lexicon shipped in ``atheric/resources``.
Score = (pos - neg) / (pos + neg) in [-1, 1].

Entity mapping: an article is attributed to a ticker when its text contains
the IDX code as a standalone uppercase token (e.g. "BBCA") or the
normalized company name. Sector tagging uses lexicon sector keywords plus
the sectors of any matched tickers.

Aggregation: for each (ticker, day) — decayed score S_d = f*S_{d-1} + sum
of today's scores, weight W_d likewise, with f = 0.5^(1/half_life).
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import yaml

from ..config import Config, load_tickers
from ..utils.logging_utils import get_logger

log = get_logger(__name__)


# --------------------------------------------------------------------------
# Lexicon & scoring
# --------------------------------------------------------------------------
def load_lexicon(cfg: Config) -> dict:
    path = cfg.root / str(cfg.require("sentiment.lexicon_file"))
    with open(path, encoding="utf-8") as fh:
        lex = yaml.safe_load(fh)
    def _norm(words) -> set[str]:
        # YAML 1.1 quirks: bare "no"/"yes" parse as booleans — normalize via str()
        return {("no" if w is False else "yes" if w is True else str(w)).lower()
                for w in (words or [])}

    return {
        "positive": _norm(lex.get("positive")),
        "negative": _norm(lex.get("negative")),
        "negations": _norm(lex.get("negations")),
        "sector_keywords": {sector: sorted(_norm(words))
                            for sector, words in (lex.get("sector_keywords") or {}).items()},
    }


_TOKEN_RE = re.compile(r"[a-zA-Z]+")


def score_text(text: str, lexicon: dict) -> float | None:
    tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
    pos = neg = 0
    for i, tok in enumerate(tokens):
        polarity = 0
        if tok in lexicon["positive"]:
            polarity = 1
        elif tok in lexicon["negative"]:
            polarity = -1
        if polarity == 0:
            continue
        if i > 0 and tokens[i - 1] in lexicon["negations"]:
            polarity = -polarity
        if polarity > 0:
            pos += 1
        else:
            neg += 1
    if pos + neg == 0:
        return None
    return (pos - neg) / (pos + neg)


# --------------------------------------------------------------------------
# Entity mapping
# --------------------------------------------------------------------------
def _normalize_name(name: str, stopwords: set[str]) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]", " ", str(name).lower())
    tokens = [t for t in cleaned.split() if t and t not in stopwords]
    return " ".join(tokens)


def build_entity_patterns(cfg: Config, metadata: pd.DataFrame | None) -> list[dict]:
    stopwords = {w.lower() for w in cfg.get("sentiment.company_name_stopwords", [])}
    min_len = int(cfg.get("sentiment.min_name_token_len", 4))
    sector_by_ticker = {}
    if metadata is not None and not metadata.empty:
        sector_by_ticker = metadata.set_index("ticker")["sector"].to_dict()

    patterns = []
    for entry in load_tickers(cfg):
        ticker = entry["ticker"]
        code = ticker.split(".")[0]
        norm = _normalize_name(entry.get("name", ""), stopwords)
        name_pattern = None
        if len(norm.replace(" ", "")) >= min_len:
            name_pattern = re.compile(r"\b" + re.escape(norm) + r"\b")
        patterns.append({
            "ticker": ticker,
            "code_pattern": re.compile(r"\b" + re.escape(code) + r"\b"),
            "name_pattern": name_pattern,
            "norm_name": norm,
            "sector": sector_by_ticker.get(ticker),
        })
    return patterns


def map_entities(text: str, patterns: list[dict]) -> list[str]:
    matched = []
    lowered = _normalize_name(text, set())  # lowercase, punctuation stripped
    for pat in patterns:
        if pat["code_pattern"].search(text):
            matched.append(pat["ticker"])
        elif pat["name_pattern"] is not None and pat["name_pattern"].search(lowered):
            matched.append(pat["ticker"])
    return matched


def map_sectors(text_lower: str, sector_keywords: dict[str, list[str]]) -> list[str]:
    return [sector for sector, words in sector_keywords.items()
            if any(w in text_lower for w in words)]


# --------------------------------------------------------------------------
# Decayed aggregation
# --------------------------------------------------------------------------
def _decay_aggregate(daily: pd.DataFrame, key_cols: list[str], half_life: float,
                     window_days: int) -> pd.DataFrame:
    """daily: columns key_cols + [date, score_sum, n]. Returns decayed columns."""
    factor = 0.5 ** (1.0 / half_life)
    out_frames = []
    for _keys, group in daily.groupby(key_cols, sort=False):
        g = (group.set_index("date")[["score_sum", "n"]]
             .sort_index())
        full_idx = pd.date_range(g.index.min(), g.index.max(), freq="D")
        g = g.reindex(full_idx, fill_value=0.0)
        s = np.zeros(len(g))
        w = np.zeros(len(g))
        acc_s = acc_w = 0.0
        for i, (score_sum, n) in enumerate(zip(g["score_sum"].to_numpy(), g["n"].to_numpy())):
            acc_s = acc_s * factor + score_sum
            acc_w = acc_w * factor + n
            s[i], w[i] = acc_s, acc_w
        g["sent_decay"] = np.where(w > 1e-9, s / np.maximum(w, 1e-9), np.nan)
        g["date"] = g.index
        for col, val in zip(key_cols, _keys if isinstance(_keys, tuple) else (_keys,)):
            g[col] = val
        # drop rows further than window_days from the last article
        last_seen = g.index.to_series().where(g["n"] > 0).ffill()
        age = (g.index.to_series() - last_seen).dt.days
        g = g[age.to_numpy() <= window_days]
        out_frames.append(g.reset_index(drop=True))
    if not out_frames:
        return pd.DataFrame(columns=key_cols + ["date", "score_sum", "n", "sent_decay"])
    return pd.concat(out_frames, ignore_index=True)


# --------------------------------------------------------------------------
# Stage entry point
# --------------------------------------------------------------------------
def _load_articles(cfg: Config) -> pd.DataFrame:
    """Gabungkan RSS live (kumulatif) + backfill historis (Wayback, opsional).

    ``source_type`` ditandai per baris artikel MENTAH (live/backfill) supaya
    bisa dilacak sampai ke DatasetSentimen.csv sebagai kolom ``sent_source``
    (lihat run()). Backfill historis SANGAT bolong (lihat disclaimer di
    scrapers/news_historical.py) -- tanda ini WAJIB ada, bukan opsional,
    supaya baris hasil backfill tidak dianggap setara dengan RSS langsung.
    """
    live_path = cfg.output_path("raw_news")
    live = (pd.read_csv(live_path, parse_dates=["published"])
            if live_path.exists() else pd.DataFrame())
    if not live.empty:
        live["source_type"] = "live"

    hist_path = live_path.parent / "articles_historical.csv"
    hist = (pd.read_csv(hist_path, parse_dates=["published"])
            if hist_path.exists() else pd.DataFrame())
    if not hist.empty:
        hist["source_type"] = "backfill"
        log.info("sentiment: +%d artikel dari backfill historis (Wayback)", len(hist))

    if live.empty and hist.empty:
        return pd.DataFrame()
    return pd.concat([live, hist], ignore_index=True)


def run(cfg: Config, articles: pd.DataFrame | None = None) -> pd.DataFrame:
    if articles is None:
        articles = _load_articles(cfg)
    if "source_type" not in articles.columns:
        articles["source_type"] = "live"
    empty_cols = ["date", "ticker", "sent_score", "sent_decay", "news_count", "sent_source"]
    ffmt = str(cfg.get("alignment.float_format", "%.6g"))
    if articles.empty:
        log.warning("no articles available; DatasetSentimen will be empty")
        out = pd.DataFrame(columns=empty_cols)
        out.to_csv(cfg.output_path("dataset_sentimen"), index=False)
        pd.DataFrame(columns=["date", "market_sent", "market_sent_decay", "market_news_count"]) \
            .to_csv(cfg.output_path("dataset_sentimen_market"), index=False)
        pd.DataFrame(columns=["date", "sector", "sector_sent"]) \
            .to_csv(cfg.output_path("dataset_sentimen_sector"), index=False)
        return out

    lexicon = load_lexicon(cfg)
    meta_path = cfg.output_path("metadata_saham")
    metadata = pd.read_csv(meta_path) if meta_path.exists() else None
    patterns = build_entity_patterns(cfg, metadata)

    articles = articles.copy()
    articles["text"] = (articles["title"].fillna("") + ". " + articles["summary"].fillna(""))
    articles["date"] = pd.to_datetime(articles["published"]).dt.normalize()
    articles["score"] = articles["text"].map(lambda t: score_text(t, lexicon))
    scored = articles.dropna(subset=["score"])
    log.info("scored %d/%d articles", len(scored), len(articles))

    # ---- entity mapping ----
    ticker_rows, sector_rows = [], []
    sector_kw = lexicon["sector_keywords"]
    for row in articles.itertuples(index=False):
        score = row.score
        tickers = map_entities(row.text, patterns)
        text_lower = row.text.lower()
        sectors = set(map_sectors(text_lower, sector_kw))
        for t in tickers:
            ticker_rows.append({"date": row.date, "ticker": t, "score": score,
                                "source_type": row.source_type})
        for pat in patterns:
            if pat["ticker"] in tickers and pat["sector"]:
                sectors.add(pat["sector"])
        for s in sectors:
            sector_rows.append({"date": row.date, "sector": s, "score": score})

    half_life = float(cfg.get("sentiment.aggregation.decay_half_life_days", 3.0))
    window = int(cfg.get("sentiment.aggregation.decay_window_days", 14))

    # ---- per-stock aggregation ----
    tdf = pd.DataFrame(ticker_rows)
    if tdf.empty:
        log.warning("no articles matched any ticker")
        out = pd.DataFrame(columns=empty_cols)
    else:
        daily = (tdf.groupby(["ticker", "date"])
                 .agg(score_sum=("score", lambda s: s.dropna().sum()),
                      sent_score=("score", "mean"),
                      n=("score", lambda s: float(s.notna().sum())),
                      news_count=("score", "size"))
                 .reset_index())
        # sent_source: asal artikel BARU hari itu (live/backfill/mixed) --
        # bukan atribusi sent_decay yg bisa terpengaruh hari-hari sebelumnya
        # (lihat catatan di features/sentiment.py::_load_articles). Hari
        # tanpa artikel baru (murni carry-forward decay) -> NaN, jujur soal
        # keterbatasan atribusi, tidak dipaksa isi.
        src = tdf.groupby(["ticker", "date"])["source_type"].agg(
            lambda s: "mixed" if s.nunique() > 1
            else ("lexicon_backfill" if s.iloc[0] == "backfill" else "lexicon_live")
        ).rename("sent_source").reset_index()
        decayed = _decay_aggregate(daily[["ticker", "date", "score_sum", "n"]],
                                   ["ticker"], half_life, window)
        out = pd.merge(decayed[["ticker", "date", "sent_decay"]],
                       daily[["ticker", "date", "sent_score", "news_count"]],
                       on=["ticker", "date"], how="left")
        out = pd.merge(out, src, on=["ticker", "date"], how="left")
        out["news_count"] = out["news_count"].fillna(0).astype(int)
        out = out[["date", "ticker", "sent_score", "sent_decay", "news_count", "sent_source"]] \
            .sort_values(["ticker", "date"]).reset_index(drop=True)
    out.to_csv(cfg.output_path("dataset_sentimen"), index=False, float_format=ffmt)
    log.info("DatasetSentimen.csv: %d rows", len(out))

    # ---- market-level ----
    mkt = (scored.groupby("date")
           .agg(score_sum=("score", "sum"), n=("score", "size"))
           .reset_index())
    mkt["market_sent"] = mkt["score_sum"] / mkt["n"]
    mkt["_k"] = "all"
    mdec = _decay_aggregate(mkt[["_k", "date", "score_sum", "n"]], ["_k"], half_life, window)
    market = pd.merge(mdec[["date", "sent_decay"]].rename(columns={"sent_decay": "market_sent_decay"}),
                      mkt[["date", "market_sent", "n"]].rename(columns={"n": "market_news_count"}),
                      on="date", how="left")
    market["market_news_count"] = market["market_news_count"].fillna(0).astype(int)
    market.to_csv(cfg.output_path("dataset_sentimen_market"), index=False, float_format=ffmt)

    # ---- sector-level ----
    sdf = pd.DataFrame(sector_rows).dropna(subset=["score"]) if sector_rows else pd.DataFrame()
    if sdf.empty:
        sector_out = pd.DataFrame(columns=["date", "sector", "sector_sent"])
    else:
        sdaily = (sdf.groupby(["sector", "date"])
                  .agg(score_sum=("score", "sum"), n=("score", "size")).reset_index())
        sdec = _decay_aggregate(sdaily, ["sector"], half_life, window)
        sector_out = sdec.rename(columns={"sent_decay": "sector_sent"})[["date", "sector", "sector_sent"]]
    sector_out.to_csv(cfg.output_path("dataset_sentimen_sector"), index=False, float_format=ffmt)
    log.info("sentiment market rows: %d, sector rows: %d", len(market), len(sector_out))
    return out
