from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select

from db.ingestion import upsert_measurements
from db.tables import measurements, metadata

# SQLite menyimpan Numeric sebagai float; di Postgres tetap Decimal.
pytestmark = pytest.mark.filterwarnings("ignore:Dialect sqlite.*Decimal")

T0 = pd.Timestamp(datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc))
T1 = pd.Timestamp(datetime(2026, 1, 5, 1, 1, tzinfo=timezone.utc))


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    return eng


def frame(*rows):
    cols = ["machine_id", "unit_id", "item_ukur", "measured_at", "value",
            "nominal", "usl", "lsl", "ratio", "zone", "source_batch"]
    df = pd.DataFrame(rows, columns=cols)
    for c in ("value", "nominal", "usl", "lsl", "ratio"):
        df[c] = df[c].astype(object)
    return df


def row(unit="U1", ts=T0, value="10.2", ratio="0.4", zone="OK", batch="b1"):
    return ("M1", unit, "Dia", ts, Decimal(value), Decimal("10"), Decimal("10.5"),
            Decimal("9.5"), Decimal(ratio), zone, batch)


def all_rows(engine):
    with engine.connect() as c:
        return c.execute(select(measurements).order_by(measurements.c.unit_id)).mappings().all()


def count(engine):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(measurements)).scalar_one()


def test_insert_new_rows(engine):
    n = upsert_measurements(engine, frame(row("U1"), row("U2", T1)))
    assert n == 2
    rows = all_rows(engine)
    assert [r["unit_id"] for r in rows] == ["U1", "U2"]
    assert rows[0]["zone"] == "OK"
    assert rows[0]["source_batch"] == "b1"
    assert rows[0]["ingested_at"] is not None
    assert float(rows[0]["value"]) == pytest.approx(10.2)


def test_same_file_twice_is_idempotent(engine):
    df = frame(row("U1"), row("U2", T1))
    upsert_measurements(engine, df)
    upsert_measurements(engine, df)
    assert count(engine) == 2


def test_reupload_updates_changed_value(engine):
    upsert_measurements(engine, frame(row("U1", value="10.2", ratio="0.4", zone="OK", batch="b1")),
                        ingested_at=datetime(2026, 1, 5, tzinfo=timezone.utc))
    upsert_measurements(engine, frame(row("U1", value="10.45", ratio="0.9", zone="WARNING", batch="b2")),
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
    upsert_measurements(engine, frame(row("U1", value="10.2"), row("U1", value="10.3")))
    rows = all_rows(engine)
    assert len(rows) == 1
    assert float(rows[0]["value"]) == pytest.approx(10.3)


def test_null_ratio_for_no_standard(engine):
    r = ("M1", "U1", "Note", T0, Decimal("3.3"), None, None, None, None, "NO_STANDARD", "b1")
    upsert_measurements(engine, frame(r))
    got = all_rows(engine)[0]
    assert got["ratio"] is None and got["zone"] == "NO_STANDARD"


def test_empty_frame_is_noop(engine):
    assert upsert_measurements(engine, frame()) == 0
    assert count(engine) == 0
