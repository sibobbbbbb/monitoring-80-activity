"""Ingestion: upsert hasil adapter ke tabel measurements (idempoten)."""
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from sqlalchemy.engine import Engine

from db.tables import MEASUREMENT_KEY, measurements

CHUNK_SIZE = 500
_UPDATE_COLUMNS = ("value", "nominal", "usl", "lsl", "ratio", "zone", "source_batch", "ingested_at")


def _insert_for(engine: Engine):
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif engine.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise NotImplementedError(f"Upsert belum didukung untuk dialect {engine.dialect.name}")
    return insert


def _none_if_na(v):
    return None if v is None or (not isinstance(v, str) and pd.isna(v)) else v


def _to_records(df: pd.DataFrame, ingested_at: datetime) -> list[dict]:
    records: dict[tuple, dict] = {}
    for rec in df.to_dict("records"):
        rec = {k: _none_if_na(v) for k, v in rec.items()}
        rec["measured_at"] = pd.Timestamp(rec["measured_at"]).to_pydatetime()
        rec["ingested_at"] = ingested_at
        # Kunci ganda dalam satu file: baris terakhir menang (Postgres menolak
        # ON CONFLICT DO UPDATE yang menyentuh baris yang sama dua kali dalam satu statement).
        records[tuple(rec[k] for k in MEASUREMENT_KEY)] = rec
    return list(records.values())


def upsert_measurements(
    engine: Engine,
    df: pd.DataFrame,
    ingested_at: Optional[datetime] = None,
) -> int:
    """Upsert baris ke measurements, kunci unik (machine_id, unit_id, item_ukur, measured_at).

    Baris baru di-insert; baris dengan kunci sama diperbarui (value, standar, ratio, zone,
    source_batch, ingested_at). Return jumlah baris unik yang diproses.
    Seluruh upsert berjalan dalam satu transaksi.
    """
    if df.empty:
        return 0

    records = _to_records(df, ingested_at or datetime.now(timezone.utc))
    insert = _insert_for(engine)

    with engine.begin() as conn:
        for i in range(0, len(records), CHUNK_SIZE):
            stmt = insert(measurements).values(records[i:i + CHUNK_SIZE])
            stmt = stmt.on_conflict_do_update(
                index_elements=list(MEASUREMENT_KEY),
                set_={c: stmt.excluded[c] for c in _UPDATE_COLUMNS},
            )
            conn.execute(stmt)
    return len(records)
