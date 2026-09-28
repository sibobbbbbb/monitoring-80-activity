"""Test ingestion terhadap Postgres sungguhan (service db di docker-compose.yml).

Sama seperti test_ingestion.py (SQLite), tetapi assertion Decimal dibandingkan persis,
bukan approx, dan sintaks ON CONFLICT diverifikasi lewat SQL yang benar-benar dikirim ke Postgres.
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError

from db.ingestion import CHUNK_SIZE, upsert_measurements
from db.tables import MEASUREMENT_KEY, measurements
from tests.test_ingestion import T0, T1, all_rows, count, frame, row

D = Decimal


def test_schema_sql_matches_table_definition(pg_engine):
    cols = {c["name"]: c["nullable"] for c in inspect(pg_engine).get_columns("measurements")}
    assert cols == {c.name: c.nullable for c in measurements.c}


def test_insert_new_rows_exact_decimal(pg_engine):
    n = upsert_measurements(pg_engine, frame(row("U1"), row("U2", T1)))
    assert n == 2
    rows = all_rows(pg_engine)
    assert [r["unit_id"] for r in rows] == ["U1", "U2"]
    r = rows[0]
    assert type(r["value"]) is D and r["value"] == D("10.2")
    assert r["nominal"] == D("10") and r["usl"] == D("10.5") and r["lsl"] == D("9.5")
    assert r["ratio"] == D("0.4")
    assert r["zone"] == "OK" and r["source_batch"] == "b1"
    assert r["ingested_at"] is not None


def test_high_precision_decimal_roundtrip_is_exact(pg_engine):
    value = D("10.123456789012345678901234567890")
    ratio = D("0.333333333333333333333333333333")
    df = frame(row("U1", value="10.123456789012345678901234567890",
                   ratio="0.333333333333333333333333333333"))
    upsert_measurements(pg_engine, df)
    got = all_rows(pg_engine)[0]
    assert got["value"] == value and got["ratio"] == ratio
    assert str(got["value"]) == str(value)          # scale ikut terjaga
    # float akan merusak nilai ini; memastikan tidak ada float di jalur simpan/baca
    assert got["value"] != D(float(value))


def test_timestamp_roundtrip_same_instant(pg_engine):
    upsert_measurements(pg_engine, frame(row("U1", ts=T0)))
    got = all_rows(pg_engine)[0]["measured_at"]
    assert got == T0.to_pydatetime()
    assert got.utcoffset() is not None


def test_same_file_twice_is_idempotent(pg_engine):
    df = frame(row("U1"), row("U2", T1))
    upsert_measurements(pg_engine, df)
    upsert_measurements(pg_engine, df)
    assert count(pg_engine) == 2


def test_reupload_updates_changed_value_exact(pg_engine):
    upsert_measurements(pg_engine, frame(row("U1", value="10.2", ratio="0.4", zone="OK", batch="b1")),
                        ingested_at=datetime(2026, 1, 5, tzinfo=timezone.utc))
    upsert_measurements(pg_engine, frame(row("U1", value="10.45", ratio="0.9", zone="WARNING", batch="b2")),
                        ingested_at=datetime(2026, 1, 6, tzinfo=timezone.utc))
    rows = all_rows(pg_engine)
    assert len(rows) == 1
    r = rows[0]
    assert r["value"] == D("10.45") and r["ratio"] == D("0.9")
    assert r["zone"] == "WARNING" and r["source_batch"] == "b2"
    assert r["ingested_at"] == datetime(2026, 1, 6, tzinfo=timezone.utc)


def test_update_keeps_row_id(pg_engine):
    upsert_measurements(pg_engine, frame(row("U1", value="10.2")))
    first_id = all_rows(pg_engine)[0]["id"]
    upsert_measurements(pg_engine, frame(row("U1", value="10.3")))
    assert all_rows(pg_engine)[0]["id"] == first_id


def test_duplicate_keys_within_one_frame_last_wins(pg_engine):
    upsert_measurements(pg_engine, frame(row("U1", value="10.2"), row("U1", value="10.3")))
    rows = all_rows(pg_engine)
    assert len(rows) == 1 and rows[0]["value"] == D("10.3")


def test_null_ratio_for_no_standard(pg_engine):
    r = ("M1", "U1", "Note", T0, D("3.3"), None, None, None, None, "NO_STANDARD", "b1")
    upsert_measurements(pg_engine, frame(r))
    got = all_rows(pg_engine)[0]
    assert got["ratio"] is None and got["nominal"] is None and got["zone"] == "NO_STANDARD"


def test_empty_frame_is_noop(pg_engine):
    assert upsert_measurements(pg_engine, frame()) == 0
    assert count(pg_engine) == 0


def test_more_rows_than_chunk_size(pg_engine):
    n = CHUNK_SIZE * 2 + 100
    df = frame(*[row(f"U{i:05d}") for i in range(n)])
    assert upsert_measurements(pg_engine, df) == n
    assert count(pg_engine) == n
    upsert_measurements(pg_engine, df)          # idempoten juga lintas chunk
    assert count(pg_engine) == n


def test_emitted_sql_uses_native_postgres_on_conflict(pg_engine):
    seen = []

    @event.listens_for(pg_engine, "before_cursor_execute")
    def capture(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    upsert_measurements(pg_engine, frame(row("U1")))
    inserts = [s for s in seen if s.lstrip().upper().startswith("INSERT INTO MEASUREMENTS")]
    assert len(inserts) == 1
    sql = " ".join(inserts[0].split())
    assert "ON CONFLICT (machine_id, unit_id, item_ukur, measured_at) DO UPDATE SET" in sql
    for col in ("value = excluded.value", "ratio = excluded.ratio", "zone = excluded.zone",
                "source_batch = excluded.source_batch", "ingested_at = excluded.ingested_at"):
        assert col in sql


def test_db_enforces_unique_key_without_upsert(pg_engine):
    rec = dict(machine_id="M1", unit_id="U1", item_ukur="Dia", measured_at=T0.to_pydatetime(),
               value=D("1"), zone="OK")
    with pg_engine.begin() as c:
        c.execute(measurements.insert().values(**rec))
    with pytest.raises(IntegrityError):
        with pg_engine.begin() as c:
            c.execute(measurements.insert().values(**rec))


def test_db_rejects_invalid_zone(pg_engine):
    rec = dict(machine_id="M1", unit_id="U1", item_ukur="Dia", measured_at=T0.to_pydatetime(),
               value=D("1"), zone="BOGUS")
    with pytest.raises(IntegrityError):
        with pg_engine.begin() as c:
            c.execute(measurements.insert().values(**rec))


def test_failed_upsert_rolls_back_whole_batch(pg_engine):
    df = frame(row("U1"), row("U2", T1, zone="BOGUS"))
    with pytest.raises(IntegrityError):
        upsert_measurements(pg_engine, df)
    assert count(pg_engine) == 0


def test_raw_multirow_upsert_with_duplicate_keys_is_rejected_by_postgres(pg_engine):
    """Alasan ingestion melakukan dedup sendiri: Postgres menolak ON CONFLICT DO UPDATE
    yang menyentuh baris sama dua kali dalam satu statement."""
    base = dict(machine_id="M1", unit_id="U1", item_ukur="Dia", measured_at=T0.to_pydatetime(),
                zone="OK")
    stmt = pg_insert(measurements).values([dict(base, value=D("1")), dict(base, value=D("2"))])
    stmt = stmt.on_conflict_do_update(index_elements=list(MEASUREMENT_KEY),
                                      set_={"value": stmt.excluded.value})
    with pytest.raises(DBAPIError):
        with pg_engine.begin() as c:
            c.execute(stmt)
