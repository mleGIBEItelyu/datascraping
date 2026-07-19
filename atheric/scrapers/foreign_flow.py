"""Per-stock daily foreign flow scraper (Tugas B2).

Endpoint TERVERIFIKASI LIVE 11 Jul 2026 (ditemukan via network inspector di
halaman Stock Summary BEI, lalu dites langsung dari Python):

    GET https://www.idx.co.id/primary/TradingSummary/GetStockSummary
        ?length=9999&start=0&date=YYYYMMDD
    -> JSON {"recordsTotal": N,
             "data": [{"StockCode", "Date", "ForeignBuy", "ForeignSell",
                       "Close", "Volume", ...}, ...]}

ForeignBuy/ForeignSell = volume saham (lembar), bukan rupiah.

KETERBATASAN SUMBER (dikonfirmasi live dgn pencarian batas tanggal):
endpoint hanya punya data mulai 2020-01-02 -- semua tanggal 2019 ke bawah
mengembalikan recordsTotal=0. ``foreign_flow.start_date`` di config
merefleksikan batas ini.

Perilaku:
- Kalender tanggal diambil dari DatasetTeknikal.csv supaya konsisten dgn
  spine; universe ticker juga dibatasi ke ticker DatasetTeknikal
  (StockCode BEI + sufiks ".JK" = format ticker project ini).
- INKREMENTAL: tanggal yang sudah ada di data/raw/foreign_flow.csv tidak
  di-fetch ulang; tanggal yang terkonfirmasi kosong dicatat di
  data/raw/foreign_flow_empty_dates.csv supaya tidak dicoba terus-menerus.
- Checkpoint disimpan tiap 50 tanggal supaya progres tidak hilang.
- Cloudflare BEI menolak TLS fingerprint library ``requests`` (403) --
  wajib ``curl_cffi`` ``impersonate="chrome"``; rate-limit 429 ditangani
  backoff (pola sama dgn driver idx_stat di scrapers/macro.py).
- Respons yang gagal di-parse didump ke data/raw/_debug/ dan tanggal itu
  dilewati -- TIDAK mengarang data (pola konsisten seluruh project).
"""

from __future__ import annotations

import time

import pandas as pd

from ..config import Config
from ..utils.logging_utils import get_logger

log = get_logger(__name__)

API_URL = "https://www.idx.co.id/primary/TradingSummary/GetStockSummary"
REFERER = "https://www.idx.co.id/en/market-data/trading-summary/stock-summary"
CHECKPOINT_EVERY = 50  # simpan progres tiap N tanggal


def _fetch_date(session, date: pd.Timestamp) -> list[dict] | None:
    """Fetch satu tanggal bursa; return daftar baris mentah, [] kalau kosong,
    None kalau gagal (caller yang memutuskan dump/skip)."""
    params = {"length": 9999, "start": 0, "date": date.strftime("%Y%m%d")}
    headers = {"Accept": "application/json, text/plain, */*", "Referer": REFERER}
    resp = None
    for retry in range(4):
        resp = session.get(API_URL, params=params, headers=headers, timeout=60)
        if resp.status_code != 429:
            break
        wait = float(resp.headers.get("Retry-After") or 0) or 10.0 * (retry + 1)
        log.info("foreign_flow %s: 429 rate-limit, tunggu %.0fs",
                 date.date(), wait)
        time.sleep(wait)
    if resp is None or resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    if not isinstance(payload, dict) or "data" not in payload:
        return None
    return payload.get("data") or []


def run(cfg: Config) -> pd.DataFrame:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:
        raise RuntimeError(
            "scraper foreign_flow butuh curl_cffi (pip install curl_cffi) -- "
            "Cloudflare BEI menolak TLS fingerprint library requests") from exc

    raw_path = cfg.output_path("raw_foreign_flow")
    empty_path = raw_path.parent / "foreign_flow_empty_dates.csv"
    debug_dir = cfg.root / "data" / "raw" / "_debug"

    # kalender & universe dari DatasetTeknikal supaya konsisten dgn spine
    teknikal_path = cfg.output_path("dataset_teknikal")
    if not teknikal_path.exists():
        raise RuntimeError("DatasetTeknikal.csv belum ada -- jalankan stage "
                           "ohlcv+technical dulu (foreign-flow butuh kalender bursa)")
    teknikal = pd.read_csv(teknikal_path, usecols=["date", "ticker"])
    teknikal["date"] = pd.to_datetime(teknikal["date"])
    universe = set(teknikal["ticker"].unique())

    start_date = pd.Timestamp(str(cfg.get("foreign_flow.start_date", "2020-01-02")))
    calendar = sorted(d for d in teknikal["date"].unique() if d >= start_date)

    existing = pd.DataFrame()
    done: set = set()
    if raw_path.exists():
        existing = pd.read_csv(raw_path, parse_dates=["date"])
        done |= set(existing["date"].unique())
    if empty_path.exists():
        done |= set(pd.read_csv(empty_path, parse_dates=["date"])["date"].unique())

    todo = [d for d in calendar if d not in done]
    log.info("foreign_flow: %d tanggal bursa (>= %s), %d sudah ada, %d akan di-fetch",
             len(calendar), start_date.date(), len(calendar) - len(todo), len(todo))
    if not todo:
        log.info("foreign_flow: raw sudah lengkap -> %s (%d baris)",
                 raw_path, len(existing))
        return existing

    session = curl_requests.Session(impersonate="chrome")
    frames = [existing] if not existing.empty else []
    empty_dates: list[pd.Timestamp] = []
    fetched = failed = 0

    def _checkpoint() -> pd.DataFrame:
        combined = (pd.concat(frames, ignore_index=True) if frames
                    else pd.DataFrame(columns=["date", "ticker", "foreign_buy",
                                               "foreign_sell", "foreign_net"]))
        if not combined.empty:
            combined = (combined.drop_duplicates(["date", "ticker"], keep="last")
                        .sort_values(["date", "ticker"]).reset_index(drop=True))
        combined.to_csv(raw_path, index=False)
        if empty_dates:
            prev = (pd.read_csv(empty_path, parse_dates=["date"])
                    if empty_path.exists() else pd.DataFrame(columns=["date"]))
            allde = pd.concat([prev, pd.DataFrame({"date": empty_dates})])
            allde.drop_duplicates("date").sort_values("date").to_csv(empty_path, index=False)
        return combined

    for i, date in enumerate(todo, start=1):
        rows = _fetch_date(session, date)
        if rows is None:
            failed += 1
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_path = debug_dir / f"foreign_flow_{date.strftime('%Y%m%d')}.txt"
            try:
                resp = session.get(API_URL,
                                   params={"length": 9999, "start": 0,
                                           "date": date.strftime("%Y%m%d")},
                                   headers={"Accept": "application/json",
                                            "Referer": REFERER}, timeout=60)
                debug_path.write_text(resp.text[:300_000],
                                      encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001 - dump adalah usaha terbaik
                pass
            log.warning("foreign_flow %s: fetch/parse gagal, didump -> %s",
                        date.date(), debug_path)
        elif not rows:
            # tanggal bursa (menurut spine) tapi endpoint kosong -- catat
            # supaya run berikutnya tidak mencoba lagi
            empty_dates.append(date)
            log.info("foreign_flow %s: recordsTotal=0 -- dicatat kosong", date.date())
        else:
            recs = []
            for r in rows:
                code = str(r.get("StockCode", "")).strip().upper()
                if not code:
                    continue
                ticker = f"{code}.JK"  # format ticker project (lihat DatasetTeknikal)
                if ticker not in universe:
                    continue
                buy = r.get("ForeignBuy")
                sell = r.get("ForeignSell")
                if not isinstance(buy, (int, float)) or not isinstance(sell, (int, float)):
                    continue
                recs.append((date, ticker, float(buy), float(sell),
                             float(buy) - float(sell)))
            frames.append(pd.DataFrame(recs, columns=["date", "ticker", "foreign_buy",
                                                      "foreign_sell", "foreign_net"]))
            fetched += 1
        if i % CHECKPOINT_EVERY == 0:
            _checkpoint()
            log.info("foreign_flow: progres %d/%d tanggal", i, len(todo))
        time.sleep(0.8)  # sopan ke server BEI (di bawah ambang 429)

    combined = _checkpoint()
    log.info("foreign_flow raw: +%d tanggal baru (%d gagal, %d kosong), "
             "total %d baris -> %s",
             fetched, failed, len(empty_dates), len(combined), raw_path)
    if failed and not fetched:
        log.warning("foreign_flow: SEMUA fetch gagal -- cek data/raw/_debug/"
                    "foreign_flow_*.txt utk diagnosis")
    return combined
