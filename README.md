# Atheric — IDX Kompas100 Forecasting Dataset Pipeline

Pipeline data-mining **end-to-end** yang membangun panel harian siap latih,
**`data/processed/MainDataset.csv`** (satu baris = satu tanggal × satu saham),
untuk melatih model AI forecasting saham Indonesia. Universe = ~100 emiten
Kompas100 di [tickers.json](tickers.json). Arsitektur mengikuti diagram di
[data.pdf](data.pdf).

MainDataset berisi **tiga jalur fitur: teknikal + fundamental + makro**.
**Sentimen dipisah** ke deliverable tersendiri
[`data/processed/sentimen.csv`](data/processed/sentimen.csv) (lihat
[Kamus kolom — dataset 2](#kamus-kolom--dataset-2-sentimencsv)) — sengaja tidak
digabung ke join utama. Semua join bersifat *point-in-time safe* (tidak ada
kebocoran masa depan).

## Ringkasan dataset yang dihasilkan

| Properti | Nilai |
|---|---|
| **Jumlah baris** | **396.892** |
| **Jumlah kolom** | **149** (teknikal + fundamental + makro; tanpa sentimen) |
| **Jumlah saham** | **100 / 100** (semua ticker berhasil) |
| **Rentang tanggal** | **2000-06-06 → 2026-07-03** (sejak listing tiap saham) |
| Baris per saham | min 299 (IPO baru, YUPI) · median 4.475 · max 6.485 (MEDC) |
| Ukuran file | ≈ 450 MB (CSV) |
| Duplikat (ticker, tanggal) | **0** |
| Kebocoran point-in-time | **0** (tidak ada fundamental/makro yang terlihat sebelum tanggal rilis) |

Deliverable tambahan: [`sentimen.csv`](data/processed/sentimen.csv) (sentimen
per saham + level market/sektor) dan
[`MainDataset_coverage.csv`](data/processed/MainDataset_coverage.csv) (tabel
cakupan non-NaN per tahun).

> Baris paling awal sebuah saham dimulai dari tanggal data tertua yang tersedia
> di Yahoo Finance (untuk IDX umumnya ±2000-an; emiten yang IPO belakangan
> seperti AADI/RATU/CBDK/YUPI otomatis mulai dari tanggal IPO-nya).

## Alur pipeline

```
yfinance ──► OHLCV sejak IPO ──► RawTeknikal.csv ──► Cleaning & Adjust
                                                     ──► Konstruksi Universe (likuiditas, ARA/ARB)
                                                     ──► Hitung Indikator ──► DatasetTeknikal.csv ─┐
stockanalysis.com ──► Laba Rugi + Neraca ──► RawFunda.csv                                          │
                     ──► Align by Release Date ──► Ratio & Growth ──► DatasetFunda.csv ────────────┼──► Time & Entity
Makro domestik + global + komoditas (FRED / DBnomics / yfinance)                                   │    Alignment
                     ──► Align by Release Date ──► Surprise, Perubahan,                            │    (join tanggal × saham)
                         Mapping ke Sektor ──► DatasetMacro.csv ───────────────────────────────────┘         │
                                                                                                             ▼
                                                                                                      MainDataset.csv
                                                                                                   (teknikal+funda+makro)

RSS lokal (Kontan, Bisnis, CNBC ID) + global (Google News, Yahoo, CNBC)
        ──► Entity Mapping (ticker/sektor) ──► Agregasi harian dengan decay
        ──► DatasetSentimen*.csv ──────────────────────────────────► sentimen.csv  (deliverable TERPISAH)
```

## Quick start

```bash
pip install -r requirements.txt

# (opsional tapi disarankan) FRED API key untuk sumber makro US/komoditas resmi.
# Buat file .env di root berisi:  FRED_API_KEY=xxxxxxxx   (otomatis dimuat)
# Tanpa key, pipeline tetap jalan dengan fallback DBnomics/yfinance.

python main.py all                    # seluruh pipeline (~7 menit) → MainDataset.csv + sentimen.csv
python main.py ohlcv technical        # jalankan stage tertentu saja (urutan bebas, dinormalisasi)
python main.py all --use-cache        # pakai ulang data mentah yang sudah ada (skip scraping)
```

> Catatan Windows: kalau `MainDataset.csv` sedang dibuka di Excel saat pipeline
> menulis, akan muncul `PermissionError`. Tutup file itu lalu jalankan ulang
> `python main.py align`.

Stage (urutan kanonis): `ohlcv → technical → fundamentals → funda-features →
macro → macro-features → news → sentiment → align`.

## Struktur project

```
atheric/
├── main.py                     # entry point CLI
├── config/config.yaml          # SEMUA konfigurasi: sumber, window, threshold, path (tidak ada hardcode di kode)
├── tickers.json                # universe Kompas100 (input)
├── .env                        # FRED_API_KEY (gitignored)
├── atheric/
│   ├── cli.py                  # orkestrasi stage + loader .env
│   ├── config.py               # loader YAML + resolusi path
│   ├── scrapers/               # ohlcv.py, fundamentals.py, macro.py, news.py
│   ├── features/               # technical.py, fundamental.py, macro.py, sentiment.py
│   ├── pipeline/align.py       # alignment → MainDataset + sentimen.csv + coverage
│   ├── resources/sentiment_lexicon.yaml   # lexicon ID+EN + keyword sektor
│   └── utils/                  # logging, HTTP session (retry + backoff + pacing)
├── data/
│   ├── raw/                    # RawTeknikal.csv, RawFunda.csv, macro/*.csv, news/articles.csv, metadata_saham.csv, corporate_actions.csv
│   ├── interim/                # DatasetTeknikal/Funda/Macro/Sentimen*.csv
│   └── processed/              # MainDataset.csv, sentimen.csv, MainDataset_coverage.csv
└── logs/pipeline.log
```

Tidak ada folder `data/manual/` — pipeline hanya memakai sumber gratis yang
programatik (tidak ada fallback CSV manual).

## Kamus kolom — dataset 1: `MainDataset.csv` (149 kolom)

Kunci baris = (`date`, `ticker`). Kelompok kolom:

### Kunci & identitas (2)
| Kolom | Tipe | Keterangan |
|---|---|---|
| `date` | date | tanggal perdagangan (bursa IDX) |
| `ticker` | str | kode saham + suffix `.JK` (mis. `BBCA.JK`) |

### Harga & likuiditas (10)
| Kolom | Tipe | Keterangan |
|---|---|---|
| `open` `high` `low` `close` | float | OHLC **sudah disesuaikan** corporate action (split/dividen) |
| `volume` | float | volume saham (lembar) |
| `raw_close` | float | harga close **mentah** (belum disesuaikan) — dasar turnover & ARA/ARB |
| `turnover_idr` | float | nilai transaksi = `raw_close × volume` (Rupiah) |
| `is_liquid` | 0/1 | flag likuid: median turnover 20 hari ≥ 1 miliar IDR |
| `ara_flag` | 0/1 | hari ini kena Auto Reject Atas (limit naik IDX per tier harga) |
| `arb_flag` | 0/1 | hari ini kena Auto Reject Bawah (limit turun) |

### Teknikal (21)
| Kolom | Keterangan |
|---|---|
| `ret_5d` `ret_20d` `ret_60d` `ret_250d` | return harga 5/20/60/250 hari perdagangan |
| `macd_hist` | histogram MACD (12,26,9) |
| `macd_hist_pct` | histogram MACD dinormalisasi ke harga (`macd_hist / close`) |
| `price_sma20_ratio` `price_sma60_ratio` | rasio harga terhadap SMA 20 / 60 hari |
| `volume_ratio` | volume hari ini / SMA volume 20 hari |
| `obv_slope_20d` | slope On-Balance-Volume 20 hari (dinormalisasi volume) |
| `mfi_14` | Money Flow Index 14 (0–100) |
| `rsi_14` | Relative Strength Index 14 (0–100) |
| `dist_52w_high` | jarak relatif harga dari puncak 52 minggu (≤ 0) |
| `close_pos_20d` | posisi close dalam range high–low 20 hari (0–1) |
| `gap_open` | gap pembukaan = `open / close_kemarin − 1` |
| `atr_ratio_14` | Average True Range 14 dibagi harga |
| `realized_vol_20d` | volatilitas realisasi 20 hari (annualized) |
| `vol_ratio_5_60` | rasio volatilitas jangka pendek/panjang (5 vs 60 hari) |
| `bollinger_pctb` | %B Bollinger (posisi harga dalam band 20,2σ) |
| `day_of_week` | hari (0=Senin … 4=Jumat) |
| `window_dressing_flag` | 1 bila termasuk 5 hari bursa terakhir Desember |

### Cross-sectional relative (4)
Z-score **lintas seluruh universe pada tanggal yang sama** (terverifikasi ~N(0,1) per hari):
| Kolom | Keterangan |
|---|---|
| `ret_20d_cs_z` `ret_60d_cs_z` | z-score return 20 / 60 hari |
| `volume_ratio_cs_z` | z-score volume ratio |
| `realized_vol_20d_cs_z` | z-score volatilitas 20 hari |

### Metadata & fundamental (27)
| Kolom | Keterangan |
|---|---|
| `sector` `sub_sector` | sektor / industri (yfinance) |
| `ipo_date` | tanggal listing (fallback: data tertua yang tersedia) |
| `funda_report_period` | akhir periode laporan keuangan yang berlaku pada baris ini |
| `funda_release_date` | tanggal rilis laporan (estimasi; dasar join point-in-time) |
| `gross_margin` `operating_margin` `net_margin` | margin laba kotor / operasi / bersih |
| `current_ratio` | aset lancar / liabilitas lancar |
| `debt_to_equity` `debt_to_assets` | total utang / ekuitas, total utang / aset |
| `cash_to_assets` | kas & setara / total aset |
| `equity_ratio` | total ekuitas / total aset |
| `roa` `roe` | Return on Assets / Equity (berbasis laba TTM) |
| `revenue_yoy` `net_income_yoy` `eps_yoy` | pertumbuhan YoY (revenue / laba bersih / EPS) |
| `revenue_qoq` `net_income_qoq` `eps_qoq` | pertumbuhan QoQ |
| `revenue_ttm` `net_income_ttm` `eps_ttm` | nilai trailing-twelve-month |
| `bvps` | book value per share |
| `pe_ttm` | Price/Earnings TTM (harga hari itu / EPS TTM) |
| `pbv` | Price/Book Value (harga hari itu / BVPS) |

### Makro & komoditas (85)
17 seri makro, tiap seri memakai **pola sufiks** yang sama, + 1 kolom sektor:

| Sufiks kolom | Keterangan |
|---|---|
| `<seri>` | level terkini yang **sudah publik** (di-forward-fill dari effective date) |
| `<seri>_surprise` | selisih rilis terbaru vs rilis sebelumnya |
| `<seri>_chg_pct` | surprise dalam persen |
| `<seri>_z252` | z-score 252 hari dari level |
| `<seri>_pct_5d` `<seri>_pct_20d` | perubahan 5 / 20 hari — **hanya untuk seri harian** |
| `sector_cmdty_ret` | (1 kolom) faktor return komoditas per sektor emiten |

17 seri (daily → 6 kolom, monthly → 4 kolom):

| Seri | Frek. | Arti |
|---|---|---|
| `bi_rate` | monthly | BI policy rate (%) |
| `id_cpi_yoy` | monthly | inflasi Indonesia YoY (%) |
| `usd_idr` | daily | kurs USD/IDR |
| `id_fx_reserves` | monthly | cadangan devisa Indonesia (USD) |
| `fed_funds_rate` | monthly | Fed Funds Rate AS (%) |
| `us_cpi_yoy` | monthly | inflasi AS YoY (%) |
| `us_10y_yield` | daily | yield US Treasury 10 tahun (%) |
| `dxy` | daily | US Dollar Index |
| `vix` | daily | indeks volatilitas CBOE |
| `us_manufacturing_activity` | monthly | proxy aktivitas manufaktur AS (IPMAN) |
| `cpo` | daily | harga CPO (crude palm oil) |
| `nickel` | monthly | harga nikel (USD/ton) |
| `brent_oil` | daily | harga minyak Brent |
| `gold` | daily | harga emas |
| `copper` | daily | harga tembaga |
| `tin` | monthly | harga timah (USD/ton) |
| `rubber` | monthly | harga karet |

> Batubara (`coal_newcastle*`) **tidak** ada di dataset — lihat kendala #4.

## Kamus kolom — dataset 2: `sentimen.csv`

Deliverable **terpisah** (sentimen tidak digabung ke MainDataset). Format
long/tidy agar level saham, market, dan sektor hidup berdampingan dalam satu file:

| Kolom | Tipe | Keterangan |
|---|---|---|
| `date` | date | tanggal |
| `level` | str | granularitas baris: `stock` \| `market` \| `sector` |
| `entity` | str | kode saham (level `stock`), `MARKET` (level `market`), atau nama sektor (level `sector`) |
| `sent_score` | float | skor lexicon harian rata-rata, rentang −1..+1 (kosong untuk level `sector`) |
| `sent_decay` | float | agregasi harian dengan exponential decay (half-life 3 hari) |
| `news_count` | int | jumlah artikel hari itu (kosong untuk level `sector`) |

Skor memakai lexicon finansial ID+EN (dengan negasi), pemetaan artikel → ticker
(kode IDX / nama emiten) dan sektor (keyword). **Cara memakai ke model:** join
`MainDataset` dengan baris `level=='stock'` pada `(date, entity==ticker)`,
dan/atau pakai baris `market`/`sector` sebagai fitur konteks pasar.

## Cakupan fitur per tahun (persen non-NaN)

Ditulis otomatis ke log akhir pipeline dan ke
[`MainDataset_coverage.csv`](data/processed/MainDataset_coverage.csv).
**Tidak ada imputasi** — NaN historis dibiarkan jujur.

| Tahun | Baris | Teknikal | Cross-sec | Fundamental | Makro |
|---|---|---|---|---|---|
| 2010 | 14.357 | 98.2% | 98.1% | **0.0%** | 92.3% |
| 2018 | 20.203 | 99.1% | 97.1% | **0.0%** | 100% |
| 2021 | 20.911 | 98.8% | 98.9% | **15.7%** | 100% |
| 2022 | 21.827 | 99.2% | 99.3% | **65.2%** | 100% |
| 2023 | 22.293 | 98.9% | 99.0% | **87.4%** | 100% |
| 2024 | 22.768 | 99.6% | 99.9% | **87.7%** | 100% |
| 2025 | 23.534 | 99.3% | 99.3% | **87.3%** | 100% |
| 2026 | 11.904 | 99.7% | 98.9% | **86.8%** | 100% |

**Rentang training yang disarankan:**
- Model **teknikal/makro murni**: bisa pakai **2001 → sekarang** (cakupan tinggi
  sejak awal; makro 100% sejak 2018).
- Model **ber-fundamental**: pakai **2022 → sekarang** (fundamental baru padat
  setelah 4 kuartal TTM tersedia; 2021 hanya ~16%, sebelum 2021 kosong).

## Sumber data & strategi fallback

Tiap seri makro dideklarasikan di [config.yaml](config/config.yaml) sebagai
*source chain* — driver pertama yang berhasil dipakai. **Hanya sumber gratis
yang programatik** (fred_api / dbnomics / yfinance); tidak ada fallback manual.

| Jalur | Sumber primer | Fallback | Status |
|---|---|---|---|
| OHLCV sejak IPO, sektor, corporate action | yfinance | retry per ticker | ✅ 100/100 saham |
| Fundamental (laba rugi + neraca) | stockanalysis.com | yfinance statements | ✅ 100/100, 1.961 laporan |
| Makro global (Fed funds, US CPI, UST10Y, US manufacturing) | **FRED API** | DBnomics | ✅ dari FRED resmi |
| Makro global (DXY, VIX) | yfinance | — | ✅ |
| Makro domestik (BI rate, CPI ID, USD/IDR, cadangan devisa) | DBnomics (BIS/IMF) + yfinance | — | ✅ |
| Komoditas (CPO, brent, emas, tembaga) | yfinance futures | DBnomics IMF | ✅ |
| Komoditas (nikel, timah) | FRED API | DBnomics IMF | ✅ |
| Komoditas (karet) | DBnomics IMF | — | ✅ |
| Sentimen → sentimen.csv | RSS Kontan, Bisnis, CNBC ID, Google News, Yahoo, CNBC Intl | feed mati dilewati | ⚠️ terbatas (lihat kendala) |

## Point-in-time correctness (anti-leakage)

- **Fundamental** digabung **per tanggal rilis**, bukan report period. Tanggal
  rilis pasti tidak tersedia di sumber gratis, jadi diestimasi konservatif:
  report period + **45 hari** (kuartalan) / + **90 hari** (tahunan), sesuai
  batas waktu pelaporan OJK/IDX. Diverifikasi: **0 baris** yang melihat laporan
  sebelum tanggal rilisnya.
- **Makro** digabung per **effective date** = akhir periode observasi + lag
  publikasi per seri (dikonfigurasi), lalu forward-fill.
- **Sentimen** hanya memakai artikel yang terbit ≤ tanggal baris; agregasi
  memakai exponential decay (half-life 3 hari).

---

## Data yang TIDAK bisa diambil & kendalanya

Semua kendala di bawah adalah konsekuensi memakai **sumber gratis**. Pipeline
tetap berjalan penuh; gap ditandai secara jujur (NaN atau flag), tidak ditambal.

### 1. Empat seri makro tidak punya sumber gratis → tidak dimasukkan
Seri berikut **tidak ada** di FRED/DBnomics/yfinance gratis, jadi **dihapus
sepenuhnya** dari pipeline (tidak muncul sebagai kolom, tidak ada placeholder):
- **PMI Manufaktur Indonesia** (S&P Global — berbayar)
- **PMI China** (Caixin/NBS — berbayar)
- **Neraca Perdagangan Indonesia** (bps.go.id — hanya HTML/rilis, tanpa API)
- **Indeks Keyakinan Konsumen** (bi.go.id — hanya rilis PDF)

> **US PMI Manufaktur (ISM)** juga sudah ditarik dari FRED gratis karena
> lisensi. Sebagai gantinya dipakai **IPMAN** (Industrial Production:
> Manufacturing) — proxy aktivitas manufaktur US yang tetap gratis & resmi.
> Kolomnya bernama `us_manufacturing_activity`.

### 2. Fundamental hanya ±5 tahun terakhir → training ber-fundamental mulai 2022
Free tier stockanalysis.com memberi **20 kuartal + 5 laporan tahunan**
(2021-03-31 → 2026-03-31). Konsekuensinya:
- Fitur fundamental **NaN sebelum 2021**, ~16% terisi 2021, padat (**~87%**)
  sejak 2022 setelah 4 kuartal TTM tersedia.
- **NaN historis dibiarkan apa adanya** — tidak ada imputasi 0/median/forward-
  fill dari masa depan. Gunakan tabel cakupan di atas untuk memilih rentang.
- Tanggal rilis laporan **diestimasi** (bukan tanggal filing sebenarnya).

### 3. Sentimen historis sangat terbatas
RSS **hanya mengekspos artikel terbaru** — tidak ada arsip historis gratis.
Akibatnya sentimen hanya mencakup beberapa hari terakhir dan pemetaan ke ticker
spesifik jarang (banyak berita makro/umum). **Solusi**: store artikel bersifat
**kumulatif** — jalankan `python main.py news sentiment align` rutin (mis. cron
harian); cakupan `sentimen.csv` tumbuh maju ke depan seiring waktu.

### 4. Batubara (coal) tidak dikoleksi
Satu-satunya sumber harga batubara harian gratis (`MTF=F`, Newcastle coal
futures di Yahoo) **berhenti update akhir 2025**. Proxy berbasis saham produsen
(BTU/Peabody, 1088.HK/China Shenhua) memang masih fresh, tapi itu **harga
ekuitas, bukan harga komoditas** — menyesatkan bila dipakai sebagai faktor
harga batubara. Karena itu batubara **sengaja tidak dimasukkan** ke dataset
(tidak ada kolom `coal_newcastle*`), daripada memakai proxy yang keliru.
Sumber gratis lain (IMF PCPS bulanan) terlalu tertinggal (~1 tahun) untuk
berguna. Untuk sektor Energy/Utilities/Industrials, faktor `sector_cmdty_ret`
kini dihitung dari komoditas lain yang valid (brent oil, tembaga).

> Mekanisme penanda basi tetap tersedia di pipeline: set `staleness_flag_days`
> pada seri makro mana pun di `config.yaml` untuk memunculkan kolom
> `<seri>_stale` (=1 bila nilai adalah carry-forward melebihi ambang).

### 5. `sector_cmdty_ret` hanya untuk sektor berbasis komoditas
Faktor komoditas per sektor **sengaja** hanya dihitung untuk sektor dengan
kaitan komoditas jelas: Energy, Basic Materials, Consumer Defensive, Industrials,
Utilities. Sektor lain (Financial Services, Healthcare, Technology, Real Estate,
Communication Services, Consumer Cyclical) **NaN** by design. Mapping bisa
diubah di `config.yaml`.

### 6. Keterbatasan histori & metadata yfinance
- Histori IDX di Yahoo umumnya mulai ±2000-an, bukan tanggal IPO sesungguhnya
  untuk emiten lama.
- `firstTradeDateEpochUtc` kadang kosong → `ipo_date` di-*fallback* ke tanggal
  data tertua yang tersedia.
- ARA/ARB memakai band simetris per tier harga (disederhanakan di config);
  aturan riil IDX berubah dari waktu ke waktu dan tidak selalu simetris.

## Konfigurasi

Semua parameter ada di [config/config.yaml](config/config.yaml): universe file,
window indikator, threshold likuiditas, tier ARA/ARB, chain sumber tiap seri
makro, lag publikasi, `staleness_flag_days`, feed RSS, query Google News,
half-life decay sentimen, kolom fundamental yang dibawa ke MainDataset, format
float CSV. Jalankan dengan config lain:
`python main.py all --config path/ke/config.yaml`.

## Menjaga dataset tetap up-to-date

```bash
python main.py all                       # refresh penuh (MainDataset + sentimen)
python main.py news sentiment align      # cukup update sentimen + tulis ulang deliverable (untuk cron harian)
python main.py all --use-cache           # gabung ulang tanpa scraping ulang
```

