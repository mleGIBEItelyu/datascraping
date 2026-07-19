## Ringkasan

Menggabungkan kerjaan **foreign flow BEI + backfill sentimen historis** dengan branch `yandik` (batubara, trade balance BPS, turso scaffold, GitHub Actions) — tanpa membuang salah satu kerjaan. Sudah dites end-to-end live, termasuk `python main.py all` sebagai satu perintah utuh.

## Yang ditambahkan

- **`foreign_flow_net`** — net foreign purchase agregat pasar BEI (bulanan), endpoint asli ditemukan & diverifikasi live
- **`foreign_buy` / `foreign_sell` / `foreign_net` / `foreign_net_zscore` / `foreign_net_5d_sum`** — foreign flow per-saham harian (100 ticker), endpoint BEI, wajib `curl_cffi` (Cloudflare)
- **Backfill sentimen historis** (stage manual `sentiment-backfill`, di luar `STAGE_ORDER` default) — via Wayback Machine, kolom `sent_source` menandai transparan asal tiap baris (`lexicon_live` vs `lexicon_backfill`)
- 2 dokumen baru: `CHANGELOG_BATUBARA_FOREIGN_FLOW.md`, `PROPOSAL_HISTORICAL_SENTIMENT.md`

## Yang dipertahankan dari `yandik` (tidak diubah)

- `coal_newcastle` (dipakai, menggantikan `batubara_newcastle` versi lama yang stale)
- `id_trade_balance` (BPS API + fallback DBnomics)
- `atheric/storage/turso.py` (scaffold, tetap nonaktif — `storage.turso.enabled: false`)
- `.github/workflows/*.yml` (dipatch: +secret `BPS_API_KEY`, timeout 30→60 menit)

## Bug fix

- `.env.example`: typo `BPS API_KEY` → `BPS_API_KEY` (env var tidak pernah ke-set tanpa ini)
- `atheric/cli.py`: stage di luar `STAGE_ORDER` sebelumnya selalu ditolak parser — diperbaiki via `EXTRA_STAGES` supaya `sentiment-backfill` bisa dipanggil manual tanpa ikut `python main.py all`

## Verifikasi

- ✅ `python main.py all --use-cache` — 10/10 stage lolos, 0 error, 1,5 menit
- ✅ `MainDataset.csv`: 397.392 baris × 166 kolom
- ✅ Backfill sentimen: 6.825 artikel (2021-2025)
- ✅ Semua driver macro baru (`idx_stat`, `bps_dynamic_table`) hidup berdampingan, dites live
- ✅ Scraper `foreign-flow` terbukti inkremental (1,8 detik run kedua, vs ~30 menit backfill pertama)

## Data

**Tidak ikut PR ini** — GitHub Action akan generate ulang data resmi setelah merge (jadwal 18:00 WIB harian, sentimen tiap 4 jam).

## Diketahui belum sempurna (tidak menghalangi merge)

- `README.md` belum di-update mencerminkan seluruh perubahan ini (utang dokumentasi, bukan bug)
- GitHub Actions belum pernah dites jalan di runner GitHub asli (baru divalidasi logika & lokal)
