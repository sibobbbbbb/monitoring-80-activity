"""Ingestion: upsert hasil adapter ke tabel measurements (idempoten)."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import NamedTuple, Optional

import pandas as pd
from sqlalchemy import select, tuple_
from sqlalchemy.engine import Connection, Engine

from db.tables import MEASUREMENT_KEY, measurements

CHUNK_SIZE = 500
_UPDATE_COLUMNS = ("value", "nominal", "usl", "lsl", "ratio", "zone", "source_batch", "ingested_at")
# Baris dengan kunci sama dianggap "berubah" bila salah satu kolom ini berbeda
# (ratio dan zone diturunkan dari kolom-kolom ini).
_COMPARE_COLUMNS = ("value", "nominal", "usl", "lsl")


class UpsertResult(NamedTuple):
    inserted: int   # kunci belum ada -> baris baru
    updated: int    # kunci sama, value/standar berubah
    unchanged: int  # kunci sama, data identik (source_batch/ingested_at tetap diperbarui)

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.unchanged


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)  # SQLite membuang tz


def _num_equal(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return Decimal(str(a)) == Decimal(str(b))


def _classify(conn: Connection, records: list[dict]) -> UpsertResult:
    """Bandingkan record dengan baris yang sudah ada (kunci sama) sebelum upsert."""
    key_cols = [measurements.c[k] for k in MEASUREMENT_KEY]
    cmp_cols = [measurements.c[c] for c in _COMPARE_COLUMNS]
    existing: dict[tuple, tuple] = {}
    for i in range(0, len(records), CHUNK_SIZE):
        keys = [tuple(r[k] for k in MEASUREMENT_KEY) for r in records[i:i + CHUNK_SIZE]]
        stmt = select(*key_cols, *cmp_cols).where(tuple_(*key_cols).in_(keys))
        for row in conn.execute(stmt):
            existing[(row[0], row[1], row[2], _utc(row[3]))] = tuple(row[4:])

    inserted = updated = unchanged = 0
    for r in records:
        old = existing.get((r["machine_id"], r["unit_id"], r["item_ukur"], _utc(r["measured_at"])))
        if old is None:
            inserted += 1
        elif all(_num_equal(o, r[c]) for o, c in zip(old, _COMPARE_COLUMNS)):
            unchanged += 1
        else:
            updated += 1
    return UpsertResult(inserted, updated, unchanged)


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
) -> UpsertResult:
    """Upsert baris ke measurements, kunci unik (machine_id, unit_id, item_ukur, measured_at).

    Baris baru di-insert; baris dengan kunci sama diperbarui (value, standar, ratio, zone,
    source_batch, ingested_at). Return UpsertResult: berapa baru, berapa berubah, berapa identik.
    Klasifikasi dan upsert berjalan dalam satu transaksi.
    """
    if df.empty:
        return UpsertResult(0, 0, 0)

    records = _to_records(df, ingested_at or datetime.now(timezone.utc))
    insert = _insert_for(engine)

    with engine.begin() as conn:
        result = _classify(conn, records)
        for i in range(0, len(records), CHUNK_SIZE):
            stmt = insert(measurements).values(records[i:i + CHUNK_SIZE])
            stmt = stmt.on_conflict_do_update(
                index_elements=list(MEASUREMENT_KEY),
                set_={c: stmt.excluded[c] for c in _UPDATE_COLUMNS},
            )
            conn.execute(stmt)
    return result
