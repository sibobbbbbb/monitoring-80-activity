"""Query dashboard terhadap Postgres sungguhan, dengan data dari pipeline asli
(file CSV -> file_adapter -> upsert_measurements)."""
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from adapters.file_adapter import load_measurements
from app.queries import Filters, filter_options, last_ingested_at, priority_rows, time_bounds, zone_counts
from app.timeutil import day_range_utc
from db.ingestion import upsert_measurements

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample_measurements.csv"
INGESTED = datetime(2026, 1, 6, 3, 0, tzinfo=timezone.utc)
D = Decimal


@pytest.fixture
def seeded(pg_engine):
    result = load_measurements(SAMPLE)
    upsert_measurements(pg_engine, result.data, ingested_at=INGESTED)
    return pg_engine


def everything():
    start, end = day_range_utc(date(2026, 1, 1), date(2026, 1, 31))
    return Filters(start=start, end=end)


def test_empty_database(pg_engine):
    assert last_ingested_at(pg_engine) is None
    assert time_bounds(pg_engine) == (None, None)
    assert zone_counts(pg_engine, everything()) == {}
    assert priority_rows(pg_engine, everything()).empty


def test_last_ingested_and_bounds(seeded):
    assert last_ingested_at(seeded) == INGESTED
    lo, hi = time_bounds(seeded)
    assert lo == datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc)
    assert hi == datetime(2026, 1, 5, 1, 3, tzinfo=timezone.utc)


def test_zone_counts_include_no_standard(seeded):
    assert zone_counts(seeded, everything()) == {"OK": 1, "WARNING": 3, "NG": 1, "NO_STANDARD": 1}


def test_priority_default_warning_ordered_by_ratio_desc(seeded):
    df = priority_rows(seeded, Filters(**{**everything().__dict__, "zones": ("WARNING",)}))
    assert list(df["item_ukur"]) == ["Depth", "Flatness", "Length"]   # 0.9, 0.9 (lebih baru dulu), 0.88
    assert list(df["ratio"]) == [D("0.9"), D("0.9"), D("0.88")]
    assert set(df["zone"]) == {"WARNING"}


def test_deviation_is_signed_by_side(seeded):
    df = priority_rows(seeded, everything()).set_index("item_ukur")
    assert df.loc["Depth", "deviation"] == D("-0.9")        # di bawah ref, sisi LSL
    assert df.loc["Flatness", "deviation"] == D("0.9")
    assert df.loc["Diameter"].shape[0] == 2                 # dua baris Diameter (OK dan NG)


def test_empty_zone_filter_means_all_standard_zones(seeded):
    df = priority_rows(seeded, everything())
    assert len(df) == 5 and "NO_STANDARD" not in set(df["zone"])
    assert df.iloc[0]["zone"] == "NG"                       # ratio 1.2 paling atas


def test_limit(seeded):
    assert len(priority_rows(seeded, everything(), limit=2)) == 2


def test_machine_unit_item_filters(seeded):
    f = Filters(**{**everything().__dict__, "machines": ("M01",)})
    assert set(priority_rows(seeded, f)["machine_id"]) == {"M01"}
    f = Filters(**{**everything().__dict__, "machines": ("M01",), "units": ("U001",), "items": ("Length",)})
    assert list(priority_rows(seeded, f)["item_ukur"]) == ["Length"]
    assert zone_counts(seeded, f) == {"WARNING": 1}


def test_time_range_filter_in_wib(seeded):
    # semua data 2026-01-05 08:00-08:03 WIB; rentang 2026-01-04 tidak boleh menangkapnya
    start, end = day_range_utc(date(2026, 1, 4), date(2026, 1, 4))
    assert zone_counts(seeded, Filters(start=start, end=end)) == {}
    start, end = day_range_utc(date(2026, 1, 5), date(2026, 1, 5))
    assert sum(zone_counts(seeded, Filters(start=start, end=end)).values()) == 6


def test_filter_options_exclude_no_standard(seeded):
    opts = filter_options(seeded)
    assert set(opts["machine_id"]) == {"M01", "M02"}
    assert "Note" not in set(opts["item_ukur"])


def test_values_are_bound_parameters_not_sql(seeded):
    evil = "M01'; DROP TABLE measurements; --"
    f = Filters(**{**everything().__dict__, "machines": (evil,)})
    assert priority_rows(seeded, f).empty
    with seeded.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM measurements")).scalar_one() == 6
