"""Turso (libSQL) writer — opsional, dipakai setelah semua sumber data gratis
yang memungkinkan sudah dieksplorasi habis (lihat NOTES.md poin migrasi).

Kenapa terpisah dari pipeline/align.py:
- CSV lokal (data/processed/*.csv) tetap jadi output utama & tidak berubah.
- Modul ini hanya MENAMBAHKAN tulis-ulang ke Turso sebagai sink kedua,
  supaya tidak ada breaking change kalau migrasi ditunda/dibatalkan.

Cara pakai:
    export TURSO_DATABASE_URL="libsql://<db>-<org>.turso.io"
    export TURSO_AUTH_TOKEN="..."
    pip install libsql-client

    python -c "
from atheric.config import Config
from atheric.storage.turso import push_datasets
push_datasets(Config.load('config/config.yaml'))
"

Atau panggil push_datasets(cfg) di akhir atheric/pipeline/align.py::run()
setelah MainDataset.csv & sentimen.csv ditulis, di belakang flag config
`storage.turso.enabled: true`.
"""

from __future__ import annotations

import os

import pandas as pd

from ..config import Config
from ..utils.logging_utils import get_logger

log = get_logger(__name__)

# Tabel yang di-mirror ke Turso -> (config output-key, nama tabel SQL)
_TABLE_MAP = [
    ("main_dataset", "main_dataset"),
    ("sentimen", "sentimen"),
    ("dataset_macro", "dataset_macro"),
]


def _get_client(cfg: Config):
    try:
        import libsql_client  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "libsql-client belum terinstall. Jalankan: pip install libsql-client"
        ) from exc

    url = os.environ.get(str(cfg.get("storage.turso.url_env", "TURSO_DATABASE_URL")), "").strip()
    token = os.environ.get(str(cfg.get("storage.turso.token_env", "TURSO_AUTH_TOKEN")), "").strip()
    if not url or not token:
        raise RuntimeError(
            "TURSO_DATABASE_URL / TURSO_AUTH_TOKEN belum di-set sebagai environment variable"
        )
    return libsql_client.create_client_sync(url=url, auth_token=token)


def _write_table(client, table: str, df: pd.DataFrame, chunk_size: int = 2000) -> None:
    """Replace-all write: drop & recreate table, lalu insert per-chunk.

    Sederhana & idempotent untuk dataset yang di-refresh penuh tiap run
    (cocok dengan gaya pipeline ini: `python main.py all` menulis ulang
    seluruh CSV, bukan append incremental).
    """
    if df.empty:
        log.warning("turso: %s kosong, dilewati", table)
        return

    cols_sql = ", ".join(f'"{c}" TEXT' for c in df.columns)
    client.execute(f'DROP TABLE IF EXISTS "{table}"')
    client.execute(f'CREATE TABLE "{table}" ({cols_sql})')

    placeholders = ", ".join(["?"] * len(df.columns))
    insert_sql = f'INSERT INTO "{table}" VALUES ({placeholders})'

    rows = df.astype(object).where(pd.notnull(df), None).values.tolist()
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        client.batch([(insert_sql, tuple(r)) for r in chunk])
    log.info("turso: %s -> %d baris ditulis", table, len(df))


def push_datasets(cfg: Config) -> None:
    """Tulis MainDataset, sentimen, dan DatasetMacro ke Turso.

    Aman dipanggil berulang (drop+recreate tiap kali). Tidak melempar error
    fatal ke pipeline utama kalau Turso tidak terkonfigurasi — hanya warning,
    supaya `python main.py all` tetap berhasil biar Turso belum di-setup.
    """
    if not bool(cfg.get("storage.turso.enabled", False)):
        log.info("turso: storage.turso.enabled=false, dilewati")
        return

    try:
        client = _get_client(cfg)
    except RuntimeError as exc:
        log.warning("turso: tidak bisa konek (%s) — lewati, CSV tetap jadi output utama", exc)
        return

    for output_key, table in _TABLE_MAP:
        try:
            path = cfg.output_path(output_key)
            if not path.exists():
                log.warning("turso: %s tidak ditemukan (%s), dilewati", output_key, path)
                continue
            df = pd.read_csv(path)
            _write_table(client, table, df)
        except Exception:  # noqa: BLE001 - satu tabel gagal jangan gagalkan semua
            log.exception("turso: gagal menulis tabel %s", table)

    client.close()
