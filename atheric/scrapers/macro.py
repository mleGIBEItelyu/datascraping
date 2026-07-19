"""Macro & commodity series scraper.

Every series in ``macro.series`` declares an ordered source chain; the first
driver that returns data wins. Supported drivers:

- ``fred_api``  : official FRED API (api.stlouisfed.org, needs FRED_API_KEY)
- ``dbnomics``  : DBnomics REST API, no key required (mirrors IMF/BIS/BLS/FED)
- ``yfinance``  : daily close of a Yahoo Finance ticker
- ``bps_dynamic_table`` : BPS (Badan Pusat Statistik) dynamic-table API, needs
                  BPS_API_KEY (free signup at webapi.bps.go.id)
- ``idx_stat``  : IDX Digital Statistic API (Tugas B1 -- foreign flow) --
                  "Total Trading by Investor's Type and Net Purchase by
                  Foreigners". TERVERIFIKASI LIVE 11 Jul 2026 via DevTools:
                  GET primary/DigitalStatistic/GetApiData?urlName=...&query=
                  <base64 {"year","month","quarter","type"}> -> JSON
                  {"seriesData": [{"x": tanggal, "y": nilai_harian}, ...]}
                  (nilai harian, miliar Rp; agregat bulanan = jumlah harian).
                  Endpoint dilindungi Cloudflare yang menolak TLS fingerprint
                  library ``requests`` (403) -- wajib pakai ``curl_cffi``
                  dengan ``impersonate="chrome"``.

Only free, programmatic sources are used — there is no manual/CSV fallback.

Each fetched series is stored raw under ``data/raw/macro/<name>.csv`` with
columns: date (observation period end), value, effective_date (date the
figure became publicly known = date + publication_lag_days).

A series whose entire source chain fails is skipped with a warning and the
pipeline keeps going.
"""

from __future__ import annotations

import base64
import json
import os
import time

import pandas as pd
import yfinance as yf

from ..config import Config
from ..utils.http_utils import HttpClient
from ..utils.logging_utils import get_logger

log = get_logger(__name__)


# --------------------------------------------------------------------------
# Drivers — each returns a DataFrame(date, value) or raises / returns empty
# --------------------------------------------------------------------------
def _fetch_fred_api(http: HttpClient, cfg: Config, source: dict) -> pd.DataFrame:
    key_env = str(cfg.get("macro.fred_api.api_key_env", "FRED_API_KEY"))
    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"{key_env} not set")
    payload = http.get_json(
        str(cfg.require("macro.fred_api.base_url")),
        params={"series_id": source["series_id"], "api_key": api_key,
                "file_type": "json", "observation_start": "1990-01-01"},
    )
    rows = [(obs["date"], obs["value"]) for obs in payload.get("observations", [])
            if obs.get("value") not in (None, ".", "")]
    df = pd.DataFrame(rows, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna()


def _fetch_dbnomics(http: HttpClient, cfg: Config, source: dict) -> pd.DataFrame:
    base = str(cfg.require("macro.dbnomics.base_url")).rstrip("/")
    payload = http.get_json(f"{base}/{source['series_id']}",
                            params={"observations": 1, "format": "json"})
    docs = payload.get("series", {}).get("docs", [])
    if not docs:
        return pd.DataFrame()
    doc = docs[0]
    df = pd.DataFrame({"date": doc.get("period_start_day", doc.get("period")),
                       "value": doc.get("value")})
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna()
    # monthly observations are stamped at period start; move to period end
    freq = str(source.get("frequency", "")).lower()
    if freq == "monthly" or (doc.get("@frequency") in ("monthly", "M")):
        df["date"] = df["date"] + pd.offsets.MonthEnd(0)
    return df


def _fetch_yfinance(source: dict) -> pd.DataFrame:
    hist = yf.Ticker(source["ticker"]).history(period="max", interval="1d",
                                               auto_adjust=False)
    if hist is None or hist.empty:
        return pd.DataFrame()
    idx = pd.to_datetime(hist.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df = pd.DataFrame({"date": idx.normalize(), "value": hist["Close"].to_numpy()})
    return df.dropna()


def _fetch_bps_dynamic_table(http: HttpClient, cfg: Config, source: dict) -> pd.DataFrame:
    """BPS (Badan Pusat Statistik) dynamic-table API — free, sign up at
    https://webapi.bps.go.id for an API key.

    Confirmed against the real API response (2026-07-12) for var 498 ("Nilai
    Neraca Perdagangan"): the actual figures live in the `datacontent` dict,
    keyed by concatenating (as plain strings, NOT zero-padded beyond their
    natural width) `vervar + var + turvar + th_id + turtahun_month`, e.g.
    vervar=9999 (Indonesia), var=498, turvar=0 ("Tidak ada" — no sub-split),
    th_id=126 (year 2026), month=5 -> key "999949801265".

    Two API calls per series:
      1. list/model/th/.../var/{var}/key/{key}     -> all available th_id/year
      2. list/model/data/.../var/{var}/th/{th_id}/key/{key} -> one year's data
         (turvar/vervar/turtahun options + datacontent for that year)

    We loop over every th_id to build the full available history in one go
    (BPS var lists typically span ~9-10 years), and month code "13" (annual
    total) is skipped since it's a derived aggregate, not a monthly point.
    """
    base = str(cfg.require("macro.bps.base_url")).rstrip("/")
    key_env = str(cfg.get("macro.bps.api_key_env", "BPS_API_KEY"))
    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"{key_env} not set")
    var_id = source["var_id"]
    vervar = str(source.get("vervar", 9999))  # 9999 = national/Indonesia

    th_payload = http.get_json(f"{base}/list/model/th/domain/0000/var/{var_id}/key/{api_key}")
    if str(th_payload.get("status", "")).upper() != "OK":
        raise RuntimeError(f"BPS th-list status={th_payload.get('status')}")
    th_body = th_payload.get("data", [])
    th_rows = th_body[1] if len(th_body) > 1 else []
    if not th_rows:
        return pd.DataFrame()

    all_rows = []
    for th_row in th_rows:
        th_id = th_row["th_id"]
        year = int(th_row["th"])
        payload = http.get_json(
            f"{base}/list/model/data/domain/0000/var/{var_id}/th/{th_id}/key/{api_key}"
        )
        if str(payload.get("status", "")).upper() != "OK":
            continue
        turvar_list = payload.get("turvar") or [{"val": "0"}]
        turvar = str(turvar_list[0].get("val", "0"))
        turtahun_list = payload.get("turtahun") or []
        content = payload.get("datacontent") or {}
        for tt in turtahun_list:
            month = tt.get("val")
            if month in (None, "13", 13):  # skip annual-total row
                continue
            month = int(month)
            key_str = f"{vervar}{var_id}{turvar}{th_id}{month}"
            if key_str in content:
                all_rows.append((year, month, content[key_str]))

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=["year", "month", "value"])
    df["date"] = pd.to_datetime(dict(year=df["year"], month=df["month"], day=1)) + pd.offsets.MonthEnd(0)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df[["date", "value"]].dropna().drop_duplicates("date").sort_values("date")


def _idx_stat_filter_param(year: int, month: int) -> str:
    """Bangun parameter 'query' ter-base64 sesuai pola API statistik BEI:
    {"year":"2023","month":"2","quarter":0,"type":"monthly"}"""
    payload = {"year": str(year), "month": str(month), "quarter": 0, "type": "monthly"}
    raw = json.dumps(payload, separators=(",", ":"))
    return base64.b64encode(raw.encode()).decode()


def _fetch_idx_stat(http: HttpClient, cfg: Config, source: dict) -> pd.DataFrame:
    """
    Driver Tugas B1 -- statistik resmi BEI "Total Trading by Investor's
    Type and Net Purchase by Foreigners", agregat bulanan.

    TERVERIFIKASI LIVE 11 Jul 2026 (endpoint ditemukan via network inspector,
    lalu dites langsung dari Python): respons JSON berisi ``seriesData`` =
    daftar nilai net purchase HARIAN dalam bulan yang diminta (miliar Rp);
    nilai bulanan = jumlah seluruh nilai harian. Cloudflare menolak library
    ``requests`` (403 di semua percobaan) tapi menerima ``curl_cffi`` dengan
    ``impersonate="chrome"``. Bulan berjalan (belum lengkap) dilewati supaya
    agregat bulanan tidak berisi angka parsial.

    Respons yang gagal di-parse tetap didump ke data/raw/macro/_debug/ dan
    driver gagal bersih (TIDAK mengarang data).
    """
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:
        raise RuntimeError(
            "driver idx_stat butuh curl_cffi (pip install curl_cffi) -- "
            "Cloudflare BEI menolak TLS fingerprint library requests") from exc

    api_url = str(source.get(
        "api_url", "https://www.idx.co.id/primary/DigitalStatistic/GetApiData"))
    url_name = str(source.get("url_name", "LINK_DPS_TOTAL_NET_PURCHASE"))
    referer = str(source.get("base_url", "https://www.idx.co.id/"))
    start_year = int(source.get("start_year", 2016))
    today = pd.Timestamp.today()
    headers = {"Accept": "application/json, text/plain, */*", "Referer": referer}
    session = curl_requests.Session(impersonate="chrome")
    rows = []
    attempts = 0

    for year in range(start_year, today.year + 1):
        # bulan berjalan dilewati: jumlah harian bulan itu belum final
        max_month = today.month - 1 if year == today.year else 12
        for month in range(1, max_month + 1):
            attempts += 1
            filt = _idx_stat_filter_param(year, month)
            url = (f"{api_url}?urlName={url_name}&query={filt}"
                   "&isPrint=False&cumulative=false")
            resp = None
            try:
                # server BEI membatasi laju (429 setelah ~40 request beruntun,
                # teramati live 11 Jul 2026) -- backoff lalu ulangi bulan yg sama
                for retry in range(4):
                    resp = session.get(url, headers=headers, timeout=30)
                    if resp.status_code != 429:
                        break
                    wait = float(resp.headers.get("Retry-After") or 0) or 10.0 * (retry + 1)
                    log.info("idx_stat %d-%02d: 429 rate-limit, tunggu %.0fs",
                             year, month, wait)
                    time.sleep(wait)
            except Exception as exc:  # noqa: BLE001
                log.info("idx_stat %d-%02d: request gagal (%s)", year, month, exc)
                continue

            data = None
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    data = None
            if isinstance(data, dict):
                points = data.get("seriesData") or []
                vals = [p.get("y") for p in points
                        if isinstance(p, dict) and isinstance(p.get("y"), (int, float))]
                if vals:
                    period_end = (pd.Timestamp(year=year, month=month, day=1)
                                  + pd.offsets.MonthEnd(0))
                    rows.append((period_end, float(sum(vals))))
                else:
                    log.info("idx_stat %d-%02d: seriesData kosong -- dilewati",
                             year, month)
                time.sleep(0.8)  # sopan ke server BEI (di bawah ambang 429)
                continue

            # Bukan JSON valid -> dump utk diagnosis, JANGAN mengarang data.
            debug_dir = cfg.root / "data" / "raw" / "macro" / "_debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_path = debug_dir / f"idx_stat_{year}_{month:02d}.html"
            debug_path.write_text(resp.text[:300_000], encoding="utf-8", errors="ignore")
            log.info("idx_stat %d-%02d: status %s bukan JSON, didump -> %s",
                     year, month, resp.status_code, debug_path)
            time.sleep(0.8)

    if not rows:
        log.warning("idx_stat: 0 bulan berhasil di-parse dari %d percobaan -- "
                    "cek data/raw/macro/_debug/*.html utk sesuaikan parser",
                    attempts)
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["date", "value"])
    return df.dropna()


def _apply_transform(df: pd.DataFrame, transform: str) -> pd.DataFrame:
    if transform == "yoy_pct":
        df = df.sort_values("date").reset_index(drop=True)
        prev = df.set_index("date")["value"]
        # match each observation with the one closest to 1 year earlier
        target = df["date"] - pd.DateOffset(years=1)
        matched = prev.reindex(target, method="nearest",
                               tolerance=pd.Timedelta(days=20)).to_numpy()
        df["value"] = (df["value"] - matched) / abs(matched) * 100.0
        df = df.dropna(subset=["value"])
    return df


# --------------------------------------------------------------------------
# Stage entry point
# --------------------------------------------------------------------------
def fetch_series(http: HttpClient, cfg: Config, spec: dict) -> pd.DataFrame | None:
    name = spec["name"]
    default_transform = str(spec.get("transform", "none"))
    default_lag = int(spec.get("publication_lag_days", 0))
    for source in spec.get("sources", []):
        driver = source.get("driver")
        try:
            if driver == "fred_api":
                df = _fetch_fred_api(http, cfg, source)
            elif driver == "dbnomics":
                df = _fetch_dbnomics(http, cfg, source)
            elif driver == "yfinance":
                df = _fetch_yfinance(source)
            elif driver == "bps_dynamic_table":
                df = _fetch_bps_dynamic_table(http, cfg, source)
            elif driver == "idx_stat":
                df = _fetch_idx_stat(http, cfg, source)
            else:
                log.warning("%s: unknown driver %s", name, driver)
                continue
        except Exception as exc:  # noqa: BLE001 - fall through the chain
            log.info("%s: driver %s unavailable (%s)", name, driver, exc)
            continue
        if df is None or df.empty:
            log.info("%s: driver %s returned no data", name, driver)
            continue
        df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
        transform = str(source.get("transform", default_transform))
        df = _apply_transform(df, transform)
        if df.empty:
            continue
        lag = int(source.get("publication_lag_days", default_lag))
        df["effective_date"] = df["date"] + pd.Timedelta(days=lag)
        df["series"] = name
        df["driver"] = driver
        df["frequency"] = str(source.get("frequency", spec.get("frequency", "unknown")))
        df["group"] = str(spec.get("group", "other"))
        log.info("%s: %d obs via %s (%s .. %s)", name, len(df), driver,
                 df["date"].iloc[0].date(), df["date"].iloc[-1].date())
        return df
    log.warning("%s: no source in chain produced data — skipped", name)
    return None


def run(cfg: Config) -> pd.DataFrame:
    http = HttpClient(cfg)
    raw_dir = cfg.output_path("raw_macro_dir")
    raw_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    for spec in cfg.get("macro.series", []):
        df = fetch_series(http, cfg, spec)
        if df is None:
            continue
        df.to_csv(raw_dir / f"{spec['name']}.csv", index=False)
        frames.append(df)
    if not frames:
        raise RuntimeError("Macro scraping produced no series at all")
    combined = pd.concat(frames, ignore_index=True)
    combined.to_csv(raw_dir / "_all_series.csv", index=False)
    log.info("macro raw: %d series, %d observations -> %s",
             combined["series"].nunique(), len(combined), raw_dir)
    return combined
