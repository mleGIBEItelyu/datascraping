# Proposal — Sentimen Historis (belum diimplementasi)

> **STATUS: RENCANA KONKRET — MENUNGGU KONFIRMASI FINAL UNTUK MULAI KODING.**
> Belum ada satu baris kode pipeline yang diubah. Update 17 Juli 2026:
> keputusan sudah dipersempit ke **Opsi A saja** (GDELT file mentah, HTTP
> gratis, tanpa akun/infrastruktur baru) berdasarkan arahan eksplisit:
> **full gratis, tanpa budget berbayar, histori sebagian (tidak harus
> sampai 2000-an) lebih baik daripada tidak ada sama sekali.** Opsi B
> (BigQuery) dan D (berbayar) di §2/§3 di bawah **dicoret dari rencana**,
> tetap didokumentasikan sebagai referensi kalau batasan berubah nanti.
> Lihat [§6](#6-rencana-implementasi-konkret-opsi-a-terpilih) dan
> [§7](#7-pemeliharaan--update-ke-depan) untuk rencana teknis & jawaban
> pertanyaan susulan.

## TL;DR

**Bisa, dengan Opsi A (GDELT, full gratis).** Sumber yang dipakai sekarang
(RSS Kontan/Bisnis/CNBC/Yahoo/Google News) memang cuma bisa maju ke depan —
itu keterbatasan sumbernya sendiri, bukan kode kita. Ada satu sumber gratis
(**GDELT**) yang punya arsip sampai 2015, tapi cakupan berita Indonesia di
dalamnya **tipis** dan **skor sentimennya beda metodologi** dari yang dipakai
sekarang — jadi perlu kerja ekstra biar tetap konsisten, bukan tinggal
sambung pipa.

---

## 1. Kenapa sekarang tidak bisa historikal

Arsitektur saat ini ([atheric/scrapers/news.py](atheric/scrapers/news.py)):
setiap run, pipeline pull RSS feed yang dikonfigurasi di `config.yaml`
(`sentiment.feeds` + `sentiment.google_news`), lalu artikel baru
di-*append* ke store kumulatif `data/raw/news/articles.csv` (dedup by link).

**Akar masalahnya**: RSS secara desain cuma expose item terbaru (biasanya
20-50 artikel terakhir per feed) — situs sumber (Kontan, Bisnis, CNBC ID,
dst.) **tidak menyediakan** cara resmi gratis untuk query "semua artikel
tentang BBCA dari 2019". Ini bukan keterbatasan kode kita, tapi keterbatasan
apa yang situs-situs itu sediakan secara gratis & programatik. README project
sudah mendokumentasikan ini di kendala #3.

Konsekuensinya: cakupan `sentimen.csv` **tumbuh maju** seiring waktu (makin
sering pipeline dijalankan, makin banyak artikel tertangkap sebelum lewat),
tapi tidak bisa mundur ke masa lalu dengan sumber yang sama.

---

## 2. Opsi yang diriset

### Opsi A — GDELT raw archive (HTTP gratis, sudah dites live)

**Apa itu**: proyek riset global (bukan komersial) yang merekam & menganalisis
berita dunia tiap 15 menit sejak Feb 2015, hasilnya didownload sebagai file
CSV terkompresi via HTTP polos, **tanpa API key/akun**.

**Yang sudah saya verifikasi live:**
```
Master file list: http://data.gdeltproject.org/gdeltv2/masterfilelist.txt
→ 1.178.163 entri file, mulai 2015-02-18 23:00:00 s/d hari ini
→ Contoh: http://data.gdeltproject.org/gdeltv2/20260717121500.gkg.csv.zip
  (file "GKG" = Global Knowledge Graph, satu per 15 menit, 27 kolom)
```

**Temuan penting dari sampling 8 file (≈2 jam data, 4.968 baris global):**

| Temuan | Detail |
|---|---|
| Volume total | ~5-10 MB terkompresi per file × 96 file/hari × ~11 tahun ≈ **ratusan GB - low TB** kalau didownload mentah semua |
| Porsi konten terkait Indonesia | **~1%** dari total baris (46/4.968) — dan sebagian besar berupa mention negara "Indonesia" dalam roundup regional Asia oleh situs aggregator (`haitisun.com`, `myanmarnews.net`, dll), **bukan** artikel finansial spesifik saham |
| Isi field | GKG **tidak menyimpan judul/isi artikel lengkap** — hanya metadata: skor tone (`V2Tone`), entitas (orang/organisasi), lokasi, tema, dan URL sumber |
| API pencarian (DOC 2.0) | Ada endpoint pencarian yang lebih ramah (`api.gdeltproject.org/api/v2/doc/doc`), tapi kena **rate-limit ketat** (429 di percobaan pertama saya, mereka minta jeda antar-request) — cocok untuk pencarian ad-hoc kecil, tidak untuk bulk-pull historis |

**Masalah konsistensi yang krusial**: sistem scoring sentimen project ini
([atheric/features/sentiment.py](atheric/features/sentiment.py)) memakai
**lexicon kata positif/negatif buatan sendiri** terhadap `title + summary`
artikel. GDELT tidak memberi title/summary — hanya skor `V2Tone` versi
metodologi GDELT sendiri. Ada 2 cara menyiasati, keduanya ada ongkosnya:

1. **Fetch ulang teks artikel** dari URL yang GDELT catat, lalu skor pakai
   lexicon yang sama seperti sekarang (konsisten, tapi banyak link artikel
   lama sudah mati/dihapus — perlu dites berapa persen yang masih hidup).
2. **Pakai `V2Tone` GDELT langsung** sebagai pengganti `sent_score` untuk
   periode historis (lebih reliable secara akses, tapi artinya **definisi
   angka sentimen berbeda** antara baris lama vs baris baru — perlu kolom
   penanda sumber, mis. `sent_source: lexicon|gdelt_tone`, supaya user
   dataset sadar ini bukan angka yang 100% sebanding).

### Opsi B — GDELT via BigQuery (bukan file mentah)

Google menghost dataset GDELT penuh di BigQuery (`gdelt-bq.gdeltv2.gkg`).
Satu query SQL bisa filter 11 tahun sekaligus (jauh lebih efisien dari
download manual ratusan ribu file). Free tier BigQuery: 1 TB scan/bulan.

**Trade-off**: ini **infrastruktur baru** yang project ini belum pernah pakai
sama sekali — perlu akun Google Cloud (proyek + billing account walau
gratis, Google tetap minta kartu kredit terdaftar untuk verifikasi). Semua
sumber data project ini sekarang murni HTTP tanpa akun apa pun (kecuali
`FRED_API_KEY` yang gratis daftar). Menambah BigQuery berarti keluar dari
pola itu — perlu keputusan sadar, bukan default.

### Opsi C — Wayback Machine (archive.org)

Situs `kontan.co.id`, `bisnis.com`, `cnbcindonesia.com` yang **sudah**
dipakai sekarang mungkin punya snapshot lama di Wayback Machine (gratis,
API CDX tanpa key). **Belum saya tes live** — potensi masalahnya: Wayback
cuma nyimpen URL spesifik yang pernah di-crawl, dan kita tidak punya daftar
URL artikel lama (masalah ayam-telur — perlu tahu URL dulu baru bisa ambil
snapshot-nya). Kemungkinan hanya berguna untuk cakupan yang sangat parsial.

### Opsi D — Beli data (paid archive API)

Ada layanan berbayar (Event Registry/NewsAPI.ai, Webz.io, NewsCatcher) yang
jual akses historis berita dengan pencarian penuh. **Di luar filosofi
project ini** ("hanya sumber gratis yang programatik" — README baris 102),
tapi saya cantumkan sebagai opsi kalau kadiv mau pertimbangkan budget demi
kualitas & kelengkapan yang jauh lebih baik daripada opsi gratis manapun.

---

## 3. Perbandingan opsi

| | A: GDELT file mentah | B: GDELT BigQuery | C: Wayback | D: Berbayar |
|---|---|---|---|---|
| Biaya | Gratis | Gratis (dgn kuota) | Gratis | Berbayar |
| Infrastruktur baru | Tidak | **Ya** (akun Google Cloud) | Tidak | Tidak |
| Rentang historis | 2015 → sekarang | 2015 → sekarang | Tergantung crawl | Bervariasi, bisa lebih jauh |
| Cakupan konten Indonesia | Tipis (~1%), banyak noise | Sama, tapi query lebih presisi/cepat | Tidak diketahui, perlu tes | Biasanya kuat (dedicated financial news) |
| Konsisten dgn scoring sekarang | Perlu kerja tambahan (lihat §2) | Sama | Bisa (teks asli, kalau ketemu) | Tergantung provider |
| Effort implementasi | Sedang-tinggi | Sedang (query gampang, infra baru) | Rendah, tapi hasil tidak pasti | Rendah (biasanya API rapi) |
| Risiko | Volume besar, hasil bisa tipis | Perlu approval infra baru | Kemungkinan hasil sangat terbatas | Butuh approval budget |

---

## 4. Rekomendasi workflow (kalau disetujui)

Supaya **tidak mengubah arsitektur keseluruhan** dan **tetap sinkron** dengan
kode & data yang sudah ada, desainnya begini:

```
                    (SUDAH ADA, tidak disentuh)
RSS feeds ──► scrapers/news.py ──► data/raw/news/articles.csv (live, kumulatif)
                                            │
                    (BARU, terpisah — one-time backfill, bukan cron harian)
GDELT archive ──► scrapers/news_historical.py ──► data/raw/news/articles_historical.csv
                                            │
                                            ▼
                        features/sentiment.py  ← baca KEDUA file, digabung
                        (union articles.csv + articles_historical.csv sebelum scoring,
                         tandai kolom `sent_source` biar bisa dibedakan asalnya)
                                            │
                                            ▼
                        DatasetSentimen*.csv → sentimen.csv (skema TIDAK berubah)
```

**Kenapa desain ini tidak mengganggu yang sudah jalan:**
- `scrapers/news.py` (RSS harian) **tidak disentuh sama sekali** — tetap
  jalan seperti sekarang, tidak ada risiko regresi ke pipeline live.
  Backfill historis run **terpisah, sekali jalan** (bukan bagian dari
  `STAGE_ORDER` harian), dipicu manual saat dibutuhkan.
- `features/sentiment.py` **hanya perlu 1 perubahan kecil**: baca &
  gabungkan 2 file input alih-alih 1, sebelum proses scoring/agregasi
  berjalan — logic lexicon, entity mapping, decay aggregation semuanya
  **sama persis**, tidak diubah.
- Skema output (`DatasetSentimen.csv`, `sentimen.csv`) **tidak berubah** —
  align.py dan MainDataset.csv tidak perlu disentuh sama sekali karena
  sentimen memang sudah deliverable terpisah (tidak di-join ke MainDataset).

**Tahapan kerja yang saya sarankan** (biar tidak all-or-nothing):

1. **Pilot 1 bulan** — ambil 1 bulan data GDELT (2025-06, misalnya), filter
   ke ticker Kompas100, cek manual berapa yang relevan vs noise. Effort:
   ~1 hari.
2. **Keputusan berdasar pilot**: kalau relevansinya bagus → lanjut ke
   backfill lebih luas dengan Opsi A. Kalau noise terlalu tinggi atau
   volume-nya bikin engineering effort tidak sepadan → pertimbangkan Opsi
   B/D atau terima keterbatasan sekarang.
3. **Backfill bertahap per tahun** (2015→2026), bukan sekali jalan 11 tahun
   — supaya kalau ada masalah di tengah jalan, tidak perlu ulang dari nol.
4. Tandai `sent_source` di setiap baris (`lexicon_live` vs `gdelt_backfill`)
   supaya siapa pun yang pakai dataset tahu metodologi skornya beda sumber.

---

## 5. Pertanyaan yang sudah dijawab (17 Juli 2026)

1. ~~Toleransi kualitas~~ → **Dijawab**: histori sebagian (tidak sampai
   2000-an) lebih baik daripada tidak ada. Opsi D (berbayar) dicoret.
2. ~~Infrastruktur baru~~ → **Dijawab implisit**: "full gratis" tanpa
   penyebutan akun cloud → diasumsikan **tidak** menambah infrastruktur baru.
   Opsi B (BigQuery) dicoret dari rencana aktif, tetap didokumentasikan di
   §2 sebagai opsi cadangan kalau nanti Opsi A ternyata terlalu lambat/berat.
3. ~~Budget~~ → **Dijawab**: tidak ada budget berbayar sampai sekarang.
   Opsi D dicoret.
4. ~~Skala waktu~~ → **Dijawab**: 2015 → sekarang (batas GDELT) diterima,
   tidak perlu sampai 2000-an.

**Keputusan: lanjut Opsi A.** Rencana konkretnya di §6 — ini masih perlu
**satu konfirmasi terakhir** (lihat penutup §6) sebelum saya mulai koding,
karena backfill penuh makan waktu sampai berhari-hari (§6.3) dan saya tidak
mau mulai proses berat itu tanpa kamu tahu persis apa yang sedang berjalan.

---

## 6. Rencana implementasi konkret (Opsi A terpilih)

### 6.1 Apakah sebelumnya ada sentimen historikal?

**Tidak ada** — ini perlu ditegaskan supaya jelas titik baseline-nya. Sistem
saat ini ([atheric/scrapers/news.py](atheric/scrapers/news.py)) hanya
mengumpulkan **maju ke depan** sejak pipeline pertama kali dijalankan —
tidak ada mekanisme apa pun untuk menarik artikel dari masa lalu. Berapa pun
lama pipeline ini sudah jalan, itulah persis cakupan historis
`sentimen.csv` sekarang (cek `data/raw/news/articles.csv` — tanggal
`published` paling awal di file itu adalah histori riil yang kita punya
hari ini, bukan estimasi). Proposal ini adalah **pertama kalinya** ada
mekanisme untuk mengisi ke belakang.

### 6.2 File yang akan dibuat/diubah

| File | Perlakuan | Isi perubahan |
|---|---|---|
| `atheric/scrapers/news_historical.py` | **Baru** | Download file GKG GDELT per periode, filter ke entitas Kompas100 (pakai ulang `build_entity_patterns`/`map_entities` dari `features/sentiment.py` — tidak duplikasi logic), coba fetch teks asli per URL match, simpan ke `data/raw/news/articles_historical.csv` dengan skema kolom **identik** ke `articles.csv` + 1 kolom tambahan `sent_source_hint` |
| `atheric/features/sentiment.py` | **Diubah kecil** | Fungsi `run()`: baca `articles.csv` **dan** `articles_historical.csv` kalau ada, `pd.concat` sebelum scoring. Tambah kolom `sent_source` (`lexicon_live` / `lexicon_backfill` / `gdelt_tone_fallback`) ke output `DatasetSentimen.csv` supaya transparan sumbernya. Logic scoring/decay/agregasi **tidak diubah**. |
| `atheric/cli.py` | **Diubah kecil** | Tambah 1 stage opsional `sentiment-backfill` — **tidak** masuk `STAGE_ORDER` default (tidak ikut ke-trigger `python main.py all`), harus dipanggil eksplisit: `python main.py sentiment-backfill`. Ini yang memastikan proses berat ini **tidak pernah tidak sengaja ke-trigger** oleh cron harian. |
| `config/config.yaml` | **Diubah kecil** | Section baru `sentiment.gdelt_backfill: {start_date, end_date, checkpoint_every}` |

Tidak ada file lain yang tersentuh — `align.py`, `MainDataset.csv`, dan
seluruh stage teknikal/fundamental/makro/foreign-flow **sama sekali tidak
terpengaruh** (sentimen memang deliverable terpisah, bukan bagian join
utama).

### 6.3 Cara kerja teknis & ongkos nyata (dihitung, bukan tebakan)

```
Rentang tersedia GDELT : 2015-02-18 → hari ini (dicek live)
Jumlah hari            : 4.167 hari
File GKG (per 15 menit): 400.032 file
Estimasi ukuran unduh  : ~2.300 GB (2,3 TB) kalau semua diunduh
```

Ini **jauh** dari ringan — makanya desainnya **bukan** "download semua dulu
baru filter", tapi **filter saat itu juga per file lalu buang**:
1. Unduh 1 file GKG (~6 MB terkompresi) → ekstrak → cek tiap baris apakah
   menyebut ticker/nama perusahaan Kompas100 (regex yang **sama** dengan
   yang sudah dipakai `map_entities()` di `features/sentiment.py`) → kalau
   cocok, simpan barisnya + coba fetch teks artikel asli dari URL-nya →
   kalau tidak cocok, buang filenya (tidak disimpan mentah).
2. **Checkpoint per hari** ke `data/raw/news/_gdelt_backfill_progress.json`
   — pola sama dengan scraper foreign-flow (B2) yang sudah terbukti jalan:
   kalau proses terhenti (mati listrik, laptop ditutup, dsb), lanjut dari
   hari terakhir yang selesai, tidak mengulang dari 2015.
3. Estimasi waktu jalan (server GDELT tidak menunjukkan rate-limit ketat di
   endpoint file mentah saat saya tes, beda dengan endpoint pencarian yang
   sempat kena 429):

   | Kecepatan | Estimasi waktu total |
   |---|---|
   | 2 file/detik (konservatif, sopan ke server) | **~2,3 hari** nonstop |
   | 5 file/detik | **~22 jam** |
   | 10 file/detik | **~11 jam** |

   → **Realistisnya dijalankan sebagai proses background berhari-hari**,
   bukan sekali `python main.py sentiment-backfill` yang selesai dalam
   hitungan menit. Ini alasan kenapa checkpointing di poin 2 wajib, bukan
   nice-to-have.

Untuk skor sentimen tiap artikel yang match, alurnya coba dulu paling
konsisten baru fallback:
```
match ticker di GKG → fetch teks asli dari URL
   ├─ berhasil (URL masih hidup) → skor pakai lexicon SAMA seperti live
   │                                 → sent_source = "lexicon_backfill"
   └─ gagal (URL mati/404, umum   → pakai skor V2Tone bawaan GDELT
      untuk artikel lama)            → sent_source = "gdelt_tone_fallback"
                                       (ditandai eksplisit, BUKAN dicampur
                                        diam-diam dgn skor lexicon)
```

### 6.4 Sebelum backfill penuh: pilot wajib (bukan opsional)

Mengingat ongkos §6.3 (berhari-hari proses), saya **tidak akan** langsung
jalankan backfill penuh 2015→2026 tanpa validasi dulu. Urutan kerja:

1. **Pilot 1 bulan** (contoh: Juni 2025) — ~2.880 file, selesai dalam
   hitungan menit-jam, bukan hari. Hasil pilot dilaporkan balik: berapa
   artikel match ticker Kompas100, berapa yang teksnya masih bisa di-fetch
   vs fallback ke GDELT tone, contoh 5-10 hasil match buat dicek manual
   relevansinya.
2. **Baru setelah pilot terlihat masuk akal** (match rate & relevansi
   wajar, bukan noise semua) → lanjut ke backfill penuh 2015→sekarang,
   dijalankan sebagai proses background dengan checkpoint.

Ini bukan birokrasi — ini supaya kita tidak habiskan 2 hari proses
berjalan cuma untuk sadar di akhir bahwa hasilnya 90% noise.

---

## 7. Pemeliharaan & update ke depan

**Setelah backfill awal selesai, backfill GDELT TIDAK perlu diulang rutin.**
Modelnya:

- **Sentimen harian/berjalan** tetap 100% dari `scrapers/news.py` (RSS
  Kontan/Bisnis/CNBC/dst) seperti sekarang — **tidak berubah**, tetap masuk
  cron/GitHub Action reguler (termasuk rencana "tiap 4 jam" yang sudah
  dibahas sebelumnya). RSS tetap sumber utama untuk berita *terkini* karena
  lebih terarah & relevan untuk saham Indonesia dibanding firehose global
  GDELT.
- **Backfill GDELT** adalah operasi **sekali jalan** untuk mengisi gap masa
  lalu (2015 → titik di mana cakupan RSS live sudah mulai solid). Setelah
  itu selesai, `articles_historical.csv` jadi **statis** — tidak perlu
  di-refresh lagi, karena tujuannya cuma mengisi masa lalu yang RSS tidak
  bisa jangkau.
- **Kapan perlu dijalankan ulang**: cuma kalau nanti ditemukan gap baru
  (mis. mau mundurkan cakupan sebelum 2015 pakai sumber lain, atau
  menambah entitas baru ke `tickers.json` yang perlu di-backfill juga).
  Bukan bagian dari siklus update rutin.
- **Stage `sentiment-backfill` sengaja di luar `STAGE_ORDER`** (§6.2) justru
  supaya ini tidak pernah campur dengan alur update harian — dipanggil
  manual saat memang dibutuhkan.

**Ringkasan untuk kadiv**: sekali proses berat ini selesai (estimasi 1-3
hari kerja background, tergantung hasil pilot), tidak ada beban rutin
tambahan ke pipeline. Update harian/4-jam-an yang sudah direncanakan
sebelumnya (GitHub Actions) tetap jalan seperti desain awal, tidak berubah.

---

## 8. Konfirmasi terakhir sebelum mulai koding

Sebelum saya mulai menulis `scrapers/news_historical.py` dan menjalankan
pilot 1 bulan:

1. **OK jalankan pilot dulu** (1 bulan, cepat, buat validasi kualitas)
   sebelum commit ke backfill penuh berhari-hari?
2. Asumsi saya "tanpa Google Cloud/akun baru" dari jawaban "full gratis" —
   **benar**, atau sebenarnya BigQuery (Opsi B, masih gratis tapi perlu
   akun) boleh dipertimbangkan kalau itu bikin jauh lebih cepat?
