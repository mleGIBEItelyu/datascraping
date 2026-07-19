"""Historical news backfill — Wayback Machine (DIAKTIFKAN 19 Juli 2026).

⚠️  DISCLAIMER KUALITAS DATA — WAJIB DIBACA sebelum memakai output ini:
    Cakupan historis dari sumber ini SANGAT JARANG/BOLONG, BUKAN histori
    harian penuh. Diverifikasi live 17 Juli 2026: archive.org cuma
    menyimpan sekitar 40-80 snapshot per feed, tersebar antara 2020-2025
    (bukan setiap hari). Kalau `articles_historical.csv` kosong atau nyaris
    kosong, itu BUKAN bug — itu memang batas ketersediaan arsip gratis yang
    ada. Keputusan sadar (bukan kompromi diam-diam): "ada tapi bolong" tetap
    dijalankan karena dianggap lebih baik daripada tidak ada sama sekali —
    lihat PROPOSAL_HISTORICAL_SENTIMENT.md untuk detail keputusan ini.

Baris yang berasal dari file ini ditandai `sent_source='lexicon_backfill'`
di DatasetSentimen.csv (lihat atheric/features/sentiment.py) supaya siapa
pun yang memakai dataset tahu baris mana yang dari backfill historis vs
pengumpulan RSS langsung — JANGAN dianggap setara tanpa embel-embel ini.

Tujuan: mengisi sentimen MASA LALU (yang RSS live tidak bisa jangkau —
lihat kendala #3 di README) ke dalam store artikel kumulatif, dengan skema
kolom IDENTIK ke data/raw/news/articles.csv supaya features/sentiment.py
bisa memprosesnya tanpa perubahan logika scoring.

--------------------------------------------------------------------------
HASIL RISET VALIDITAS (tes live 17 Juli 2026) — ringkas:

  Backend GDELT (firehose global)        -> DITOLAK untuk saham ID
    - DOC search API: throttled 429 keras dari IP kita (semua metode)
    - Raw GKG files : mekanis jalan, TAPI cakupan domain Indonesia hanya
      ~0.018% (2 dari 11.122 baris), dan pencocokan kode/nama ticker
      didominasi FALSE POSITIVE (archi->architect, kode 4-huruf bentrok
      ticker AS). Volume 2015->kini ~2,3 TB unduh. Rasio manfaat rendah.

  Backend Wayback (arsip RSS feed kita)  -> LEBIH MENJANJIKAN, tapi jarang
    - archive.org menyimpan snapshot XML feed yg SUDAH kita pakai; tiap
      snapshot = artikel Indonesia ASLI, format sama, langsung bisa di-parse
      feedparser (terbukti live). Konten on-topic & relevan.
    - Keterbatasan: snapshot JARANG (Bisnis ~42 hari, CNBC ~36 hari, Kontan
      ~0 — tersebar 2020..2025), jadi hasilnya bolong, bukan harian penuh.
    - Volume: ringan (puluhan-ratusan snapshot, hitungan MB).

  => Kerangka ini mengutamakan backend WAYBACK (sinyal bersih) dan menyertakan
     GDELT sbg opsi cadangan yg dinonaktifkan default. Keputusan akhir backend
     mana yang dipakai MENUNGGU konfirmasi.
--------------------------------------------------------------------------
"""

from __future__ import annotations

import time

import pandas as pd

from ..config import Config
from ..utils.logging_utils import get_logger

log = get_logger(__name__)

# Skema kolom WAJIB sama dgn data/raw/news/articles.csv supaya sentiment.py
# tidak perlu tahu asal-usul baris (lihat scrapers/news.py).
ARTICLE_COLS = ["source", "feed_url", "lang", "title", "summary", "link",
                "published", "fetched_at"]


# ==========================================================================
# BACKEND A — Wayback Machine atas RSS feed yang sudah dipakai (DIUTAMAKAN)
# ==========================================================================
def _wayback_list_snapshots(feed_url: str, limit: int = 5000) -> list[str]:
    """Daftar timestamp snapshot (status 200, maks 1/hari) utk satu feed.

    ✅ TERVERIFIKASI LIVE: CDX API mengembalikan daftar snapshot per feed.
    Contoh hasil nyata: rss.bisnis.com -> 42 snapshot 200 (2020..2025).
    """
    import requests
    cdx = "http://web.archive.org/cdx/search/cdx"
    params = {"url": feed_url, "output": "json", "limit": limit,
              "collapse": "timestamp:8",           # <= 1 snapshot per hari
              "filter": "statuscode:200",
              "fl": "timestamp,original"}
    resp = requests.get(cdx, params=params, timeout=60)
    resp.raise_for_status()
    rows = resp.json()
    return [r[0] for r in rows[1:]] if len(rows) > 1 else []


def _wayback_fetch_feed(feed_url: str, timestamp: str, source: str,
                        lang: str) -> list[dict]:
    """Ambil 1 snapshot RSS dari arsip, parse jadi baris artikel.

    ✅ TERVERIFIKASI LIVE: `...<ts>id_/<url>` mengembalikan XML mentah (tanpa
    banner arsip) yang feedparser bisa baca. Field published/title/summary
    keluar normal.
    """
    import feedparser
    import requests
    from .news import _clean_text  # pakai ulang pembersih yg sama, tanpa duplikasi

    raw_url = f"http://web.archive.org/web/{timestamp}id_/{feed_url}"
    resp = requests.get(raw_url, timeout=60)
    if resp.status_code != 200:
        log.info("wayback %s @ %s -> HTTP %d, dilewati", feed_url, timestamp,
                 resp.status_code)
        return []
    parsed = feedparser.parse(resp.content)
    rows = []
    for entry in parsed.entries:
        published = None
        for key in ("published_parsed", "updated_parsed"):
            t = entry.get(key)
            if t:
                published = pd.Timestamp(*t[:6])
                break
        rows.append({
            "source": source, "feed_url": feed_url, "lang": lang,
            "title": _clean_text(entry.get("title")),
            "summary": _clean_text(entry.get("summary") or entry.get("description")),
            "link": (entry.get("link") or "").strip(),
            "published": published,
        })
    return rows


def _run_wayback(cfg: Config) -> pd.DataFrame:
    """Backfill via arsip RSS. Memakai ULANG daftar feed berbahasa Indonesia
    di config `sentiment.feeds` (lang == 'id') — tidak menambah sumber baru,
    hanya menariknya dari masa lalu.

    ✅ Dijalankan end-to-end 19 Juli 2026. Satu snapshot yang gagal (timeout/
    HTTP error ke archive.org) di-log & DILEWATI, TIDAK mematikan seluruh
    backfill -- pola sama dgn scraper lain di project ini (macro.py,
    foreign_flow.py: satu kegagalan tidak boleh menggagalkan semuanya).
    """
    feeds = [f for f in cfg.get("sentiment.feeds", [])
             if str(f.get("lang", "")).lower() == "id"]
    sleep_s = float(cfg.get("sentiment.historical.sleep_seconds", 2.0))
    all_rows: list[dict] = []
    failed = 0
    for feed in feeds:
        url = str(feed["url"])
        source = str(feed.get("source", "rss"))
        try:
            snaps = _wayback_list_snapshots(url)
        except Exception as exc:  # noqa: BLE001 - feed mati tidak mematikan run
            log.warning("wayback CDX gagal utk %s: %s", url, exc)
            continue
        log.info("wayback %s: %d snapshot harian ditemukan", url, len(snaps))
        for ts in snaps:
            try:
                all_rows.extend(_wayback_fetch_feed(url, ts, source, "id"))
            except Exception as exc:  # noqa: BLE001 - 1 snapshot gagal != run gagal
                failed += 1
                log.info("wayback %s @ %s: gagal (%s), dilewati", url, ts, exc)
            time.sleep(sleep_s)   # sopan ke archive.org
    if failed:
        log.warning("wayback: %d snapshot gagal di-fetch (dilewati, bukan fatal)",
                    failed)
    return pd.DataFrame(all_rows, columns=[c for c in ARTICLE_COLS
                                           if c not in ("fetched_at",)])


# ==========================================================================
# BACKEND B — GDELT GKG (CADANGAN, dinonaktifkan default; hasil riset buruk)
# ==========================================================================
def _run_gdelt(cfg: Config) -> pd.DataFrame:
    """Backfill via GDELT GKG raw files.

    ⚠️  DITOLAK berdasar riset (lihat header file): cakupan ID ~0.018%,
        false-positive dominan, volume ~2,3 TB. Dibiarkan sbg placeholder
        supaya arsitektur terbuka kalau strategi entity-matching diperkuat
        nanti. TIDAK diimplementasikan penuh di kerangka ini.
    """
    raise NotImplementedError(
        "backend gdelt sengaja belum diimplementasi — hasil riset 17 Jul 2026 "
        "menunjukkan sinyal-ke-noise tidak layak utk saham Indonesia. "
        "Gunakan backend 'wayback'.")


# ==========================================================================
# Entry point — dipanggil stage opsional `sentiment-backfill` (DI LUAR
# STAGE_ORDER default -- lihat cli.py. Jalankan manual:
#   python main.py sentiment-backfill
# Ini backfill SEKALI-JALAN, bukan bagian dari update harian/4-jam-an --
# lihat PROPOSAL_HISTORICAL_SENTIMENT.md §7 utk alasan desainnya.
# ==========================================================================
def run(cfg: Config) -> pd.DataFrame:
    """Tulis data/raw/news/articles_historical.csv (skema == articles.csv).

    Hasilnya dibaca & digabung oleh features/sentiment.py::run() secara
    otomatis (kalau file ini ada), ditandai sent_source='lexicon_backfill'.
    """
    backend = str(cfg.get("sentiment.historical.backend", "wayback")).lower()
    log.info("news_historical: backend = %s", backend)

    if backend == "wayback":
        fresh = _run_wayback(cfg)
    elif backend == "gdelt":
        fresh = _run_gdelt(cfg)
    else:
        raise ValueError(f"backend historical tidak dikenal: {backend}")

    if fresh.empty:
        log.warning("news_historical: 0 artikel terkumpul")
        out_path = cfg.output_path("raw_news").parent / "articles_historical.csv"
        pd.DataFrame(columns=ARTICLE_COLS).to_csv(out_path, index=False)
        return fresh

    fresh["published"] = pd.to_datetime(fresh["published"], errors="coerce")
    fresh["published"] = fresh["published"].fillna(pd.Timestamp.now().normalize())
    fresh["fetched_at"] = pd.Timestamp.now()
    fresh = fresh[fresh["title"].astype(str).str.len() > 0]
    # dedup by link (pola sama dgn news.py)
    fresh = (fresh.sort_values("published")
             .drop_duplicates(subset="link", keep="first")
             .reset_index(drop=True))

    out_path = cfg.output_path("raw_news").parent / "articles_historical.csv"
    fresh.to_csv(out_path, index=False)
    log.info("articles_historical.csv: %d artikel (%s .. %s) -> %s",
             len(fresh), fresh["published"].min().date(),
             fresh["published"].max().date(), out_path)
    return fresh
