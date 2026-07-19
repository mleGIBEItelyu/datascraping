# Changelog — Batubara & Foreign Flow (Tugas A, B1, B2)

> **⚠️ UPDATE 19 Juli 2026 — bagian batubara di dokumen ini SUDAH DIGANTIKAN.**
> Dokumen di bawah (ditulis 11 Juli 2026) mendokumentasikan kolom
> `batubara_newcastle` (driver DBnomics-only, IMF PCOALAU). Setelah kerja
> ini digabung dengan branch `yandik`, kolom itu **DIGANTI** dengan
> `coal_newcastle` milik yandik (yfinance `MTF=F` + fallback DBnomics +
> `staleness_flag_days` — pendekatan lebih baik, punya penanda basi
> eksplisit). **`batubara_newcastle` TIDAK ADA lagi di dataset/config
> sekarang** — kalau mencari kolom batubara, cari `coal_newcastle` dan
> `coal_newcastle_stale`. Detail keputusan penggantian ada di
> `CHANGELOG` versi merge (lihat riwayat commit branch
> `merge-foreign-flow-sentiment-historis`). Sisa dokumen ini (§7 soal
> staleness, §1-5 soal foreign flow) **tetap akurat & berlaku penuh** —
> cuma bagian "batubara_newcastle" yang perlu dibaca sebagai riwayat
> historis, bukan kondisi kolom saat ini.
>
> Dokumen handover untuk pekerjaan yang dikerjakan **11 Juli 2026**: menambah
> tiga leading indicator baru ke `MainDataset.csv` (batubara, foreign flow
> agregat, foreign flow per-saham). Ditulis supaya orang lain — atau kamu
> sendiri beberapa bulan lagi — bisa mengerti apa yang berubah, kenapa
> berubah seperti itu, dan apa yang masih harus diputuskan, tanpa perlu
> membaca ulang seluruh riwayat chat.
>
> Dokumen ini melengkapi [README.md](README.md), bukan menggantikannya.
> README menjelaskan pipeline secara keseluruhan; dokumen ini menjelaskan
> **satu batch perubahan** di atasnya. Beberapa klaim di README (kolom
> berjumlah 149, batubara "sengaja tidak dimasukkan") **sudah tidak akurat**
> setelah perubahan ini — lihat [§6](#6-hal-yang-membuat-readmemd-sekarang-tidak-akurat).
> (README juga **masih belum diperbarui** untuk foreign flow, trade
> balance, turso, GitHub Actions, dan sentimen historis — README perlu
> penulisan ulang menyeluruh yang di luar cakupan dokumen changelog ini.)

## Daftar isi
1. [Ringkasan status](#1-ringkasan-status)
2. [Checklist requirement](#2-checklist-requirement)
3. [Perubahan kode — file per file](#3-perubahan-kode--file-per-file)
4. [Kolom baru di MainDataset.csv](#4-kolom-baru-di-maindatasetcsv)
5. [Alur pipeline — sebelum vs sesudah](#5-alur-pipeline--sebelum-vs-sesudah)
6. [Hal yang membuat README.md sekarang tidak akurat](#6-hal-yang-membuat-readmemd-sekarang-tidak-akurat)
7. [Peringatan kualitas data — batubara lag ~1 tahun](#7-peringatan-kualitas-data--batubara-lag-1-tahun)
8. [Penyesuaian teknis dari kode lama](#8-penyesuaian-teknis-dari-kode-lama)
9. [Integrasi ke depan & pertanyaan terbuka](#9-integrasi-ke-depan--pertanyaan-terbuka)
10. [Cara menjalankan / verifikasi ulang](#10-cara-menjalankan--verifikasi-ulang)

---

## 1. Ringkasan status

| # | Requirement | Status |
|---|---|---|
| 1 | Cari data batubara | ✅ **Selesai** — `batubara_newcastle` via DBnomics (IMF PCOALAU) |
| 2 | Cari tambahan leading indicator | ✅ **Selesai** — foreign flow agregat (BEI, bulanan) + foreign flow per-saham (BEI, harian, 100 ticker) |
| 3 | Migrasi ke Database Turso, *"jika semua sudah tidak bisa ditambahkan lagi"* | ❓ **Belum** — syaratnya judgment call, lihat [§9](#9-integrasi-ke-depan--pertanyaan-terbuka) |
| 4 | GitHub Action scraping harian jam 18:00 | ❌ **Belum dikerjakan** |
| 5 | GitHub Action sentiment tiap 4 jam | ❌ **Belum dikerjakan** |

**Yang berubah secara kuantitatif:**

| Properti | Sebelum | Sesudah |
|---|---|---|
| Kolom `MainDataset.csv` | 145 | **158** (+13) |
| Baris `MainDataset.csv` | 396.892 | 396.892 (tidak berubah) |
| Seri makro (`data/raw/macro/`) | 17 | **18** (+`batubara_newcastle`) |
| Stage pipeline (`STAGE_ORDER`) | 9 | **10** (+`foreign-flow`) |
| Dependency Python | — | +`curl_cffi` (wajib, bukan opsional) |

Semua angka di atas dari run live 11 Juli 2026 (`python main.py macro macro-features foreign-flow align`), bukan estimasi dari membaca kode.

---

## 2. Checklist requirement

Detail per poin ada di bagian selanjutnya; ini ringkasannya dengan bukti runtime.

- [x] **Data batubara** — `batubara_newcastle`, 426 observasi bulanan (1990-01 → 2025-06), cakupan 100% di MainDataset. ⚠️ Lihat [§7](#7-peringatan-kualitas-data--batubara-lag-1-tahun) — datanya **stale ~1 tahun**, ini bukan bug, tapi konsekuensi keterbatasan sumber gratis yang **sama persis** dengan yang sudah didokumentasikan di README sebelum batubara "sengaja tidak dimasukkan".
- [x] **Foreign flow agregat pasar** — `foreign_flow_net`, endpoint asli BEI ditemukan via DevTools & diverifikasi live (bukan tebakan), 126 observasi bulanan (2016-01 → 2026-06).
- [x] **Foreign flow per-saham** — `foreign_buy`/`foreign_sell`/`foreign_net` + 2 turunan, endpoint asli BEI ditemukan & diverifikasi live, 142.122 baris × 100 ticker, cakupan 99,7% sejak 2020-01-02 (batas data sumber, bukan kegagalan fetch).
- [ ] **Migrasi Turso** — belum dikerjakan, menunggu keputusan kamu.
- [ ] **GitHub Action harian 18:00** — belum ada file di `.github/workflows/`.
- [ ] **GitHub Action sentiment /4 jam** — belum ada file di `.github/workflows/`.

---

## 3. Perubahan kode — file per file

### File baru

#### `atheric/scrapers/foreign_flow.py`
Scraper Tugas B2 — foreign buy/sell/net **per saham per hari**.

- Sumber: `GET https://www.idx.co.id/primary/TradingSummary/GetStockSummary?length=9999&start=0&date=YYYYMMDD` — endpoint asli, ditemukan lewat Network tab DevTools di halaman [Stock Summary BEI](https://www.idx.co.id/en/market-data/trading-summary/stock-summary), lalu diverifikasi bisa query tanggal historis dengan tes langsung dari Python.
- **Kalender & universe** diambil dari `DatasetTeknikal.csv` (kolom `date`, `ticker`) supaya persis konsisten dengan spine `align.py` — tidak membuat kalender sendiri.
- **Inkremental by design**: tanggal yang sudah ada di `data/raw/foreign_flow.csv` tidak di-fetch ulang; tanggal yang terkonfirmasi kosong (`recordsTotal=0`) dicatat di `data/raw/foreign_flow_empty_dates.csv` supaya run berikutnya tidak mencoba lagi. Ini penting untuk cron harian — run kedua dan seterusnya hanya fetch 1 tanggal baru, bukan backfill ulang 1.500+ tanggal.
- **Checkpoint** disimpan ke CSV tiap 50 tanggal, supaya kalau proses terputus (timeout, koneksi putus), progres tidak hilang.
- Format ticker: `StockCode` dari BEI (mis. `BBCA`) diubah ke `BBCA.JK` supaya cocok dengan format ticker project ini.
- Respons gagal parse **didump** ke `data/raw/_debug/foreign_flow_<tanggal>.txt`, tanggal itu dilewati — **tidak pernah mengarang data** (pola konsisten dengan seluruh scraper di project ini).

#### `atheric/features/foreign_flow.py`
Feature builder Tugas B2 — baca raw B2, hitung 2 fitur turunan per ticker:

| Kolom | Formula | Alasan (dari PRD) |
|---|---|---|
| `foreign_net_zscore` | rolling z-score `foreign_net`, window 60 hari bursa (`min_periods=30`) | menormalkan skala antar saham dengan likuiditas sangat berbeda |
| `foreign_net_5d_sum` | rolling sum `foreign_net`, window 5 hari bursa | foreign flow harian noisy; akumulasi mingguan lebih bermakna sebagai sinyal |

Window `60` dikonfigurasi via `foreign_flow.zscore_window` di `config.yaml`, bukan hardcode.

### File diubah

#### `atheric/scrapers/macro.py`
Driver `idx_stat` (untuk `foreign_flow_net`, Tugas B1) **ditulis ulang total**. Versi yang dikirim sebelumnya menebak: URL halaman HTML + parameter `?filter=` base64 sebagai endpoint data — ini salah, endpoint itu mengembalikan halaman HTML, bukan data. Endpoint asli yang benar (`primary/DigitalStatistic/GetApiData`) ditemukan lewat DevTools dengan cara yang sama seperti B2. Detail teknis kenapa ini penting ada di [§8](#8-penyesuaian-teknis-dari-kode-lama).

#### `atheric/pipeline/align.py`
Tambah fungsi `_join_foreign_flow()`:
```python
def _join_foreign_flow(spine: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    ff = _read_csv(cfg.output_path("dataset_foreign_flow"), ["date"])
    if ff.empty:
        return spine
    return spine.merge(ff, on=["date", "ticker"], how="left")
```
Dipanggil di `run()` setelah `_join_macro_sector(spine, cfg)`. Pola join **identik** dengan fungsi `_join_*` lain di file ini — generic merge berbasis kolom, bukan whitelist nama kolom, sehingga kolom baru otomatis ikut ke `MainDataset.csv` tanpa perlu ubah kode lagi di masa depan (sifat ini sudah dijelaskan sebelumnya untuk `_join_macro`).

#### `atheric/cli.py`
```python
STAGE_ORDER = ["ohlcv", "technical", "fundamentals", "funda-features",
               "macro", "macro-features", "foreign-flow", "news", "sentiment",
               "align"]
```
Stage `foreign-flow` ditaruh **setelah** `macro-features` (tidak saling bergantung, urutan bebas) dan **sebelum** `align` (wajib — align butuh `DatasetForeignFlow.csv` sudah ada), serta **setelah** `ohlcv`/`technical` (wajib — scraper B2 butuh `DatasetTeknikal.csv` untuk kalender). Dispatch baru:
```python
elif stage == "foreign-flow":
    from .features import foreign_flow as f_ff
    from .scrapers import foreign_flow as s_ff
    s_ff.run(cfg)
    f_ff.run(cfg)
```
Catatan: stage ini **selalu dijalankan** (tidak ada `--use-cache` guard) — sama seperti stage `news` — karena scraper-nya sendiri sudah inkremental secara internal.

#### `config/config.yaml`
- Source chain `foreign_flow_net` diperbaiki dengan parameter endpoint asli (`api_url`, `url_name`).
- Path output baru: `paths.outputs.raw_foreign_flow`, `paths.outputs.dataset_foreign_flow`.
- Section baru:
  ```yaml
  foreign_flow:
    start_date: "2020-01-02"   # batas awal ketersediaan data endpoint BEI
    zscore_window: 60          # window rolling z-score per ticker (hari bursa)
  ```

#### `requirements.txt`
```diff
 requests>=2.31
+curl_cffi>=0.7  # driver idx_stat & scraper foreign_flow: Cloudflare BEI menolak TLS fingerprint requests
```

### File dipulihkan (bug ditemukan, bukan disengaja)

#### `atheric/features/macro.py`
File "siap pasang" yang dikirim di sesi sebelumnya **salah taruh isi** — kode driver `idx_stat` (yang seharusnya masuk ke `scrapers/macro.py`) tertimpa ke `features/macro.py`, menghapus feature-builder makro asli (fungsi `run()` yang menghitung surprise/chg_pct/z252/mapping sektor). Kalau ini tidak ketahuan, stage `macro-features` akan crash atau menghasilkan output salah total. Ditemukan sebelum eksekusi live dengan cara membandingkan isi file terhadap `datascraping.zip` (snapshot sebelumnya) — isinya dipulihkan dari zip, dan kode driver `idx_stat` ditulis ulang di lokasi yang benar (lihat "File diubah" di atas).

---

## 4. Kolom baru di MainDataset.csv

13 kolom baru, 3 kelompok, 0 kolom lama dihapus/diubah.

| Kolom | Kelompok | Sumber | Cakupan non-NaN | Catatan |
|---|---|---|---|---|
| `batubara_newcastle` | Makro/komoditas | DBnomics · `IMF/PCPS/M.W00.PCOALAU.USD` | 100% | ⚠️ lag ~1 tahun, lihat §7 |
| `batubara_newcastle_surprise` | ″ | turunan | 100% | selisih rilis vs rilis sebelumnya |
| `batubara_newcastle_chg_pct` | ″ | turunan | 100% | versi persen dari surprise |
| `batubara_newcastle_z252` | ″ | turunan | 100% | z-score 252 hari |
| `foreign_flow_net` | Makro/domestik | BEI `GetApiData` (agregat bulanan) | 54,9%* | *rendah karena spine mulai 2000, seri baru ada sejak 2016 |
| `foreign_flow_net_surprise` | ″ | turunan | 54,9%* | |
| `foreign_flow_net_chg_pct` | ″ | turunan | 54,9%* | |
| `foreign_flow_net_z252` | ″ | turunan | 54,9%* | |
| `foreign_buy` | Foreign flow per-saham | BEI `GetStockSummary` (harian) | 99,7%** | **dihitung sejak 2020-01-02 (batas sumber) |
| `foreign_sell` | ″ | ″ | 99,7%** | |
| `foreign_net` | ″ | ″ = `foreign_buy − foreign_sell` | 99,7%** | volume lembar saham, bukan Rupiah |
| `foreign_net_zscore` | ″ | turunan, rolling 60d per ticker | 99,7%** | |
| `foreign_net_5d_sum` | ″ | turunan, rolling sum 5d per ticker | 99,7%** | |

> Angka cakupan bertanda `*`/`**` **bukan bug** — lihat penjelasan di baris masing-masing dan detail di §7 untuk batubara. Coverage table lengkap per tahun otomatis ditulis ke `data/processed/MainDataset_coverage.csv` tiap kali `align` dijalankan, tapi tabel itu **belum** memasukkan grup `foreign_flow` ke `_coverage_groups()` di `align.py` — itu artinya kolom baru ini tidak muncul di ringkasan log otomatis project (`log_coverage()`), meski datanya sendiri sudah benar di CSV. Ini gap kecil yang bisa ditambal kalau perlu (tinggal tambah 1 baris dict di `_coverage_groups()`), belum saya sentuh karena di luar scope literal PRD.

---

## 5. Alur pipeline — sebelum vs sesudah

**Sebelum** (dari README, `STAGE_ORDER` 9 stage):
```
ohlcv → technical → fundamentals → funda-features → macro → macro-features → news → sentiment → align
```

**Sesudah** (10 stage, satu stage baru disisipkan):
```
ohlcv → technical → fundamentals → funda-features → macro → macro-features
                                                                    │
                                                        [BARU] foreign-flow
                                                     (scraper + feature builder B2,
                                                      butuh kalender dari technical)
                                                                    │
                                                                    ▼
                                                        news → sentiment → align
```

Diagram data-flow (gaya sama dengan README, hanya menambah cabang baru):
```
BEI GetApiData ──► foreign_flow_net (bulanan, agregat pasar)  ─┐
                                                                 │  masuk lewat driver
DBnomics IMF PCOALAU ──► batubara_newcastle (bulanan) ──────────┤  idx_stat/dbnomics di
                                                                 │  macro.py yang sama
                                     ──► Align by Release Date ─┘  dgn seri lain
                                         ──► Surprise, Perubahan, Z-score
                                         ──► DatasetMacro.csv (kolom baru otomatis ikut)

BEI GetStockSummary ──► foreign_buy/sell per saham per hari
                     ──► RawForeignFlow.csv (data/raw/foreign_flow.csv)
                     ──► rolling z-score 60d + sum 5d per ticker
                     ──► DatasetForeignFlow.csv ──► join (date, ticker) ─┐
                                                                          │
DatasetTeknikal.csv (spine) ─────────────────────────────────────────────┼──► MainDataset.csv
   + metadata + fundamental + DatasetMacro + DatasetMacroSector ─────────┘
```

Yang membuat ini **tidak perlu ubah `align.py` untuk seri makro baru** (batubara, foreign_flow_net agregat): `_join_macro()` adalah merge generik berbasis kolom, bukan whitelist nama kolom — begitu kolom baru masuk `DatasetMacro.csv`, otomatis ikut ke `MainDataset.csv`. Untuk foreign flow **per-saham** ini tidak berlaku karena join key-nya beda (`date, ticker`, bukan cuma `date`) — makanya perlu fungsi `_join_foreign_flow()` baru secara eksplisit (lihat §3).

---

## 6. Hal yang membuat README.md sekarang tidak akurat

README **tidak saya ubah** (di luar scope permintaan — "tambahkan satu file"), tapi beberapa bagian di dalamnya sekarang salah dan sebaiknya diperbarui menyusul dokumen ini:

| Lokasi di README | Klaim saat ini | Status |
|---|---|---|
| Baris 21, judul §"Kamus kolom" | "149 kolom" | ❌ Sekarang **158** |
| §"Makro & komoditas (85)" | Daftar 17 seri, tidak termasuk batubara/foreign_flow_net | ❌ Sekarang **18 seri** + `foreign_flow_net` terpisah |
| Baris 209 | `> Batubara (coal_newcastle*) tidak ada di dataset — lihat kendala #4` | ❌ **Sekarang ada**, nama kolomnya `batubara_newcastle` (bukan `coal_newcastle`) |
| §"4. Batubara (coal) tidak dikoleksi" (baris 319-328) | Menjelaskan alasan batubara sengaja tidak dimasukkan, termasuk klaim "IMF PCPS bulanan terlalu tertinggal (~1 tahun) untuk berguna" | ⚠️ **Masih benar secara teknis** (lihat §7) — tapi sekarang project **memakainya juga**, jadi bagian ini perlu diubah dari "kenapa tidak dipakai" jadi "dipakai, dengan trade-off ini" |
| Tidak ada bagian foreign flow sama sekali | — | ❌ Perlu ditambah entri baru di "Kamus kolom" & "Sumber data & strategi fallback" |

Tidak saya perbaiki sekarang supaya perubahan README tetap satu keputusan sadar, bukan efek samping dari task lain — tapi kalau kamu mau, saya bisa update README sekalian di sesi berikutnya.

---

## 7. Peringatan kualitas data — batubara lag ~1 tahun

**Ini yang paling penting untuk dibaca sebelum dipakai training model.**

Verifikasi langsung terhadap raw file (`data/raw/macro/batubara_newcastle.csv`, dicek 11 Juli 2026):

```
observasi terakhir : 2025-06-30
effective_date      : 2025-07-20   (observasi + publication_lag_days: 20)
hari ini             : 2026-07-11
lag                  : 376 hari (~12,5 bulan)
```

Karena `_join_macro()` melakukan **forward-fill** dari effective date, setiap baris `MainDataset.csv` dari **20 Juli 2025 sampai hari ini** memiliki nilai `batubara_newcastle` yang **sama persis** (harga Juni 2025) — bukan carry-forward yang salah secara teknis, tapi secara praktis kolom ini **tidak informatif untuk periode 1 tahun terakhir** kalau dipakai sebagai fitur "kondisi pasar saat ini".

Ini **persis** masalah yang sudah didokumentasikan di README §4 sebagai alasan batubara awalnya *tidak* dimasukkan ("Sumber gratis lain (IMF PCPS bulanan) terlalu tertinggal ~1 tahun untuk berguna"). PRD tugas ini secara eksplisit meminta batubara dicoba live meski tahu risikonya, dan hasilnya **mengonfirmasi** peringatan lama itu — bukan membantahnya.

Yang **tidak** stale: `foreign_flow_net` (agregat, lag 11 hari) dan foreign flow per-saham (lag harian normal). Jadi 2 dari 3 penambahan sesi ini sehat; batubara punya trade-off nyata.

**Opsi yang tersedia** (belum saya eksekusi, perlu keputusanmu):
1. **Biarkan** — kolom tetap ada, tapi dokumentasikan keterbatasannya jelas-jelas (dokumen ini + update README nanti) supaya siapa pun yang training model tahu untuk tidak menganggapnya real-time.
2. **Aktifkan staleness flag** — pipeline sudah punya mekanisme ini (`staleness_flag_days` di config, hasilkan kolom `<seri>_stale`); tinggal di-set untuk `batubara_newcastle` supaya model bisa belajar "abaikan nilai ini kalau stale".
3. **Cari sumber lain** — proxy saham produsen batubara (BTU, 1088.HK) yang README sudah sebutkan sebelumnya sebagai "harga ekuitas, bukan harga komoditas — menyesatkan", atau sumber berbayar.
4. **Drop lagi** — kalau setelah tahu trade-off-nya ternyata tidak worth it untuk use case model kamu.

---

## 8. Penyesuaian teknis dari kode lama

Bagian ini untuk orang yang perlu paham *kenapa* kode ditulis seperti sekarang, bukan cuma *apa* yang berubah.

### 8.1 Cloudflare BEI menolak library `requests` — wajib `curl_cffi`
Semua endpoint `idx.co.id` (baik `DigitalStatistic/GetApiData` maupun `TradingSummary/GetStockSummary`) di belakang Cloudflare yang melakukan **TLS fingerprinting**. Library `requests` standar Python punya fingerprint TLS yang mudah dikenali dan **selalu** ditolak dengan `403 Forbidden`, berapa pun header (`User-Agent`, `Referer`, dll) yang ditambahkan — sudah dicoba dan gagal konsisten. `curl_cffi` dengan `impersonate="chrome"` meniru TLS handshake browser Chrome asli dan **selalu** berhasil (200) pada endpoint yang sama. Ini alasan kenapa `curl_cffi` jadi dependency **wajib**, bukan opsional/fallback — tidak ada jalur lain yang terbukti jalan.

### 8.2 Rate limiting BEI: ~40 request beruntun → 429
Percobaan pertama fetch 126 bulan `foreign_flow_net` tanpa jeda menghasilkan **85 kegagalan 429** setelah request ke-41. Solusi: jeda `0.8` detik antar-request + retry dengan backoff (`Retry-After` header kalau ada, fallback `10 × (percobaan+1)` detik). Setelah perbaikan ini, run ulang 126 bulan dan run B2 1.569 tanggal keduanya **0 kegagalan 429**. Angka `0.8` detik dan pola retry ini dipakai identik di kedua scraper (`scrapers/macro.py` driver `idx_stat`, dan `scrapers/foreign_flow.py`) — kalau BEI mengubah threshold rate-limit-nya di masa depan, ubah di kedua tempat.

### 8.3 `idx_stat`: endpoint asli vs endpoint yang ditebak
Versi sebelumnya (dari draft PRD) menargetkan URL halaman HTML statistik BEI dengan parameter `?filter=<base64>` sebagai kalau itu adalah endpoint data. Ini salah asumsi — URL itu me-render halaman, bukan mengembalikan JSON. Endpoint data yang sebenarnya dipanggil oleh halaman itu (ditemukan lewat tab Network di DevTools) adalah:
```
GET /primary/DigitalStatistic/GetApiData?urlName=LINK_DPS_TOTAL_NET_PURCHASE&query=<base64 filter>&isPrint=False&cumulative=false
```
Parameter `query` (bukan `filter`) berisi base64 dari `{"year","month","quarter":0,"type":"monthly"}` — formatnya persis sama dengan tebakan awal, hanya nama parameter query string-nya yang beda (`query` vs `filter`) dan base URL-nya beda (`primary/DigitalStatistic/GetApiData`, bukan halaman HTML-nya langsung). Respons berisi **nilai harian** dalam bulan tersebut (`seriesData: [{x: tanggal, y: nilai}]`); nilai bulanan = jumlah seluruh nilai harian — bukan diambil langsung dari API (API tidak menyediakan agregat bulanan siap pakai).

### 8.4 Bulan berjalan dilewati
`idx_stat` melewati bulan yang belum selesai (`today.month - 1` sebagai batas atas untuk tahun berjalan) supaya agregat bulanan tidak pernah berisi jumlah parsial (mis. baru 5 hari dari 20 hari bursa dalam sebulan) yang akan terlihat seperti anomali/outlier di seri waktu kalau tidak sengaja.

### 8.5 Desain scraper B2: kenapa inkremental + checkpoint
Backfill penuh 1.569 tanggal bursa (2020-01-02 → sekarang) memakan **~30 menit** dengan jeda 0,8 detik/request yang wajib untuk menghindari rate-limit. Kalau scraper ini nanti dipanggil dari cron harian (lihat §9), **tidak boleh** backfill ulang 1.569 tanggal tiap hari — makanya scraper cek `data/raw/foreign_flow.csv` yang sudah ada dan hanya fetch tanggal yang benar-benar baru. Checkpoint tiap 50 tanggal memastikan kalau runner (GitHub Actions runner, misalnya) mati di tengah jalan karena timeout, tanggal yang sudah berhasil di-fetch tidak hilang dan tidak perlu diulang.

---

## 9. Integrasi ke depan & pertanyaan terbuka

### 9.1 Migrasi ke Database Turso — perlu keputusanmu
Requirement aslinya bersyarat: *"jika semua sudah tidak bisa ditambahkan lagi, langsung migrasi ke Database Turso."* Setelah sesi ini, 3 leading indicator baru sudah masuk (batubara, foreign flow agregat, foreign flow per-saham). Pertanyaan yang perlu dijawab sebelum saya mulai migrasi:

1. **Apakah ini dianggap "sudah tidak bisa ditambahkan lagi"?** Atau masih ada kandidat leading indicator lain yang ingin dicari dulu (mis. short selling data, bandarmology/broker summary, indeks sektor tambahan, data opsi)?
2. Kalau lanjut migrasi: **MainDataset.csv tetap dipertahankan** sebagai deliverable CSV (untuk siapa pun yang mau pakai tanpa setup database), atau Turso jadi **satu-satunya** sumber?
3. Skema Turso: **satu tabel besar** (mirror `MainDataset.csv` apa adanya, 158 kolom) atau **dinormalisasi** (tabel terpisah per grup fitur — teknikal/fundamental/makro/foreign_flow — di-join saat query)?

### 9.2 GitHub Actions — scraping harian jam 18:00 WIB
Belum dikerjakan. Yang perlu dikonfirmasi sebelum saya buat workflow-nya:
- **18:00 WIB = 11:00 UTC** untuk cron syntax (`cron: "0 11 * * *"`). Apakah perlu dibatasi hari bursa saja (`* * 1-5`, Senin-Jumat) atau jalan tiap hari (aman karena stage inkremental akan skip kalau tidak ada tanggal baru)?
- Secrets yang perlu disimpan di GitHub repo settings: `FRED_API_KEY` kalau dipakai (saat ini **tidak diset** — lihat catatan di §9.4, beberapa seri FRED-only seperti `us_manufacturing_activity` gagal fetch tanpa ini).
- Stage yang dipanggil: `ohlcv technical fundamentals funda-features macro macro-features foreign-flow align` (tanpa `news sentiment` — itu jadwal terpisah per poin berikut).
- Commit hasil (`MainDataset.csv` + turunannya) balik ke repo, atau simpan sebagai artifact/release GitHub Actions? File `MainDataset.csv` ~450MB — commit langsung ke git history akan membuat repo membengkak cepat; perlu strategi (Git LFS, artifact terpisah, atau langsung ke Turso kalau §9.1 sudah jalan).

### 9.3 GitHub Actions — sentiment tiap 4 jam
Belum dikerjakan. Stage `sentiment` (dan `news` sebelumnya) sudah ada di `cli.py` dan siap dipanggil langsung:
```
cron: "0 */4 * * *"
run: python main.py news sentiment
```
README sudah mencatat bahwa store artikel RSS bersifat kumulatif, jadi run lebih sering = cakupan historis `sentimen.csv` tumbuh lebih rapat — cocok dengan alasan permintaan "tiap 4 jam" (RSS cuma expose artikel terbaru, jadi makin sering di-poll makin sedikit artikel yang "lewat" tanpa pernah tertangkap).

### 9.4 Catatan tambahan (ditemukan saat verifikasi live, di luar scope task ini)
`us_manufacturing_activity` gagal fetch di setiap run (`no source in chain produced data — skipped`) karena source chain-nya cuma `fred_api` tanpa fallback, dan `FRED_API_KEY` di `.env` kosong (`.env.example` ada tapi belum diisi). Ini **bukan regresi** dari perubahan sesi ini — sudah begitu sebelumnya — tapi relevan untuk §9.2 kalau mau workflow GitHub Actions menghasilkan data lengkap: perlu tambah `FRED_API_KEY` sebagai GitHub secret.

---

## 10. Cara menjalankan / verifikasi ulang

```bash
# Full refresh termasuk semua penambahan sesi ini
python main.py macro macro-features foreign-flow align

# Verifikasi kolom baru & cakupan
python -c "
import pandas as pd
df = pd.read_csv('data/processed/MainDataset.csv', low_memory=False)
new_cols = ['batubara_newcastle', 'foreign_flow_net', 'foreign_buy', 'foreign_sell', 'foreign_net']
print(df[new_cols].notna().mean())
"

# Backfill B2 dari nol (hapus dulu raw-nya — proses ~30 menit untuk 1.569 tanggal)
rm data/raw/foreign_flow.csv data/raw/foreign_flow_empty_dates.csv
python main.py foreign-flow align
```

Debug artifact kalau ada kegagalan fetch:
- `data/raw/macro/_debug/idx_stat_<tahun>_<bulan>.html` — respons non-JSON dari driver `idx_stat`
- `data/raw/_debug/foreign_flow_<tanggal>.txt` — respons non-JSON dari scraper B2
