# Catatan Pengembangan — Atheric Pipeline

## Sudah dikerjakan

- [x] **Data batubara** — diaktifkan kembali di `config/config.yaml` sebagai
      seri `coal_newcastle` (yfinance `MTF=F`, fallback IMF PCPS Australia coal
      tahunan via DBnomics). Dilengkapi `staleness_flag_days: 10` sehingga bila
      harga basi/carry-forward, muncul kolom `coal_newcastle_stale=1` alih-alih
      didiamkan tanpa keterangan. Ditambahkan juga ke `sector_commodity_map`
      (Energy, Utilities).

- [x] **Leading indicator: neraca perdagangan Indonesia** — awalnya pakai IMF
      DOT via DBnomics, TAPI setelah dites nyata di komputer user datanya
      ternyata lag ~14 bulan (terakhir Mei 2025 saat dicek Juli 2026).
      **Diupgrade** ke **BPS WebAPI langsung** (`webapi.bps.go.id`, indicator_id
      `498` = "Nilai Neraca Perdagangan") sebagai sumber utama — resmi, gratis
      (perlu daftar API key gratis), dan rilis BPS hanya lag ~1 bulan. DBnomics
      IMF DOT tetap jadi fallback kedua kalau BPS API down.
      Driver baru `bps_indicator` ditambahkan ke `atheric/scrapers/macro.py`
      dan sudah ditest dengan response simulasi (parsing logic terbukti benar) —
      **belum ditest dengan API key BPS asli**, jadi field JSON (`year`/`month`/
      `value`) mungkin perlu disesuaikan sedikit begitu dicoba dengan key nyata.
      **Kandidat yang DICEK tapi TIDAK dipakai:** OECD Composite Leading
      Indicator (CLI) Indonesia (FRED `IDNLOLITONOSTSAM`) — dicek langsung ke
      FRED dan **datanya berhenti di Januari 2024** ("Next Release: Not
      Available"). OECD menghentikan CLI untuk 5 ekonomi non-member (termasuk
      Indonesia) sejak 2024, jadi kalau dipaksa masuk akan basi permanen.
      PMI Manufaktur Indonesia/China dan Indeks Keyakinan Konsumen BI masih
      seperti temuan lama di README: tidak ada API gratis.

      **Cara aktifkan BPS API:**
      1. Daftar di https://webapi.bps.go.id untuk dapat API key gratis
      2. Tambahkan ke `.env`: `BPS_API_KEY=xxxxxxxx`
      3. Jalankan `python main.py macro` dan cek log baris `id_trade_balance`
      4. Kalau muncul error parsing (field JSON beda dari dugaan), kirim
         pesan error-nya balik supaya driver-nya disesuaikan

- [x] **Scaffold migrasi ke Turso** — modul baru `atheric/storage/turso.py`
      (writer opsional, non-breaking). Nonaktif secara default
      (`storage.turso.enabled: false` di config.yaml). Cara aktifkan:
      1. `pip install libsql-client` (sudah ditambahkan ke `requirements.txt`)
      2. Set env `TURSO_DATABASE_URL` & `TURSO_AUTH_TOKEN`
      3. Ubah `storage.turso.enabled: true` di `config/config.yaml`
      4. Panggil `push_datasets(cfg)` di akhir `atheric/pipeline/align.py::run()`
      CSV lokal tetap jadi output utama — Turso murni sink tambahan supaya
      tidak ada breaking change kalau migrasinya ditunda.

- [x] **GitHub Action pipeline utama** — jalan otomatis tiap hari jam
      **18:00 WIB** (`.github/workflows/pipeline-daily.yml`)
- [x] **GitHub Action sentimen** — jalan otomatis setiap **4 jam sekali**
      (`.github/workflows/sentiment-4h.yml`)

## Belum bisa (dan kenapa)

- PMI Manufaktur Indonesia, PMI China, Indeks Keyakinan Konsumen BI — tidak
  ada sumber gratis+programatik (konsisten dengan temuan awal di README).
- Kalau nanti muncul API baru untuk ini, tinggal tambah entri baru di
  `macro.series` pada `config/config.yaml` — kode scraper & feature engineering
  sudah generic, tidak perlu ubah Python.

## Otomatisasi (GitHub Actions)

- [x] **Pipeline utama** — jalan otomatis setiap hari jam **18:00 WIB**
      (`.github/workflows/pipeline-daily.yml`)
- [x] **Sentimen** — jalan otomatis setiap **4 jam sekali**
      (`.github/workflows/sentiment-4h.yml`)
