from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select

from db.ingestion import UpsertResult, upsert_measurements
from db.tables import measurements, metadata

# SQLite menyimpan Numeric sebagai float; di Postgres tetap Decimal.
pytestmark = pytest.mark.filterwarnings("ignore:Dialect sqlite.*Decimal")

T0 = pd.Timestamp(datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc))
T1 = pd.Timestamp(datetime(2026, 1, 5, 1, 1, tzinfo=timezone.utc))

COLUMNS = ["part", "operation", "machine", "characteristics", "measured_at", "value",
           "nominal", "usl", "lsl", "ratio", "zone", "source_batch"]


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    return eng


def frame(*rows):
    df = pd.DataFrame(rows, columns=COLUMNS)
    for c in ("value", "nominal", "usl", "lsl", "ratio"):
        df[c] = df[c].astype(object)
    return df


def row(char="C1", ts=T0, value="10.2", ratio="0.4", zone="OK", batch="b1", machine="M1"):
    return ("Part A", "Op 10", machine, char, ts, Decimal(value), Decimal("10"), Decimal("10.5"),
            Decimal("9.5"), Decimal(ratio), zone, batch)


def all_rows(engine):
    with engine.connect() as c:
        stmt = select(measurements).order_by(measurements.c.characteristics, measurements.c.machine)
        return c.execute(stmt).mappings().all()


def count(engine):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(measurements)).scalar_one()


def test_insert_new_rows(engine):
    res = upsert_measurements(engine, frame(row("C1"), row("C2", T1)))
    assert res == UpsertResult(inserted=2, updated=0, unchanged=0)
    rows = all_rows(engine)
    assert [r["characteristics"] for r in rows] == ["C1", "C2"]
    assert rows[0]["part"] == "Part A" and rows[0]["operation"] == "Op 10" and rows[0]["machine"] == "M1"
    assert rows[0]["zone"] == "OK"
    assert rows[0]["source_batch"] == "b1"
    assert rows[0]["ingested_at"] is not None
    assert float(rows[0]["value"]) == pytest.approx(10.2)


def test_same_file_twice_is_idempotent(engine):
    df = frame(row("C1"), row("C2", T1))
    upsert_measurements(engine, df)
    upsert_measurements(engine, df)
    assert count(engine) == 2


def test_reupload_updates_changed_value(engine):
    upsert_measurements(engine, frame(row("C1", value="10.2", ratio="0.4", zone="OK", batch="b1")),
                        ingested_at=datetime(2026, 1, 5, tzinfo=timezone.utc))
    upsert_measurements(engine, frame(row("C1", value="10.45", ratio="0.9", zone="WARNING", batch="b2")),
                        ingested_at=datetime(2026, 1, 6, tzinfo=timezone.utc))
    rows = all_rows(engine)
    assert len(rows) == 1
    r = rows[0]
    assert float(r["value"]) == pytest.approx(10.45)
    assert float(r["ratio"]) == pytest.approx(0.9)
    assert r["zone"] == "WARNING"
    assert r["source_batch"] == "b2"
    assert r["ingested_at"].day == 6


def test_duplicate_keys_within_one_frame_last_wins(engine):
    upsert_measurements(engine, frame(row("C1", value="10.2"), row("C1", value="10.3")))
    rows = all_rows(engine)
    assert len(rows) == 1
    assert float(rows[0]["value"]) == pytest.approx(10.3)


def test_machine_is_part_of_the_key(engine):
    """Item cek yang sama di dua mesin pada waktu yang sama = dua baris berbeda."""
    res = upsert_measurements(engine, frame(row("C1", machine="M1"), row("C1", machine="M2")))
    assert res == UpsertResult(2, 0, 0)
    assert [r["machine"] for r in all_rows(engine)] == ["M1", "M2"]


def test_null_ratio_for_no_standard(engine):
    r = ("Part A", "Op 10", "M1", "Note", T0, Decimal("3.3"), None, None, None, None, "NO_STANDARD", "b1")
    upsert_measurements(engine, frame(r))
    got = all_rows(engine)[0]
    assert got["ratio"] is None and got["zone"] == "NO_STANDARD"


def test_empty_frame_is_noop(engine):
    assert upsert_measurements(engine, frame()) == UpsertResult(0, 0, 0)
    assert count(engine) == 0


def test_result_counts_new_updated_unchanged(engine):
    first = upsert_measurements(engine, frame(row("C1"), row("C2", T1), row("C3", T1)))
    assert first == UpsertResult(3, 0, 0)
    second = upsert_measurements(engine, frame(
        row("C1"),                                    # identik
        row("C2", T1, value="10.3", ratio="0.6"),     # value berubah
        row("C4", T1),                                # baru
    ))
    assert second == UpsertResult(inserted=1, updated=1, unchanged=1)
    assert second.total == 3
    assert count(engine) == 4


def test_only_source_batch_change_counts_as_unchanged_but_batch_is_refreshed(engine):
    upsert_measurements(engine, frame(row("C1", batch="b1")))
    res = upsert_measurements(engine, frame(row("C1", batch="b2")))
    assert res == UpsertResult(0, 0, 1)
    assert all_rows(engine)[0]["source_batch"] == "b2"


def test_changed_standard_counts_as_updated(engine):
    upsert_measurements(engine, frame(row("C1")))
    changed = ("Part A", "Op 10", "M1", "C1", T0, Decimal("10.2"), Decimal("10"), Decimal("11"),
               Decimal("9.5"), Decimal("0.2"), "OK", "b1")           # usl 10.5 -> 11
    assert upsert_measurements(engine, frame(changed)) == UpsertResult(0, 1, 0)


def test_null_standards_compare_as_unchanged(engine):
    r = ("Part A", "Op 10", "M1", "Note", T0, Decimal("3.3"), None, None, None, None, "NO_STANDARD", "b1")
    upsert_measurements(engine, frame(r))
    assert upsert_measurements(engine, frame(r)) == UpsertResult(0, 0, 1)


def test_duplicate_keys_in_frame_count_once(engine):
    res = upsert_measurements(engine, frame(row("C1", value="10.2"), row("C1", value="10.3")))
    assert res == UpsertResult(1, 0, 0)
