"""Query dashboard terhadap Postgres sungguhan, dengan data dummy yang masuk lewat db/ingestion.py.
Nilai yang diharapkan dihitung ulang di pandas secara independen dari SQL."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.queries import (
    Filters,
    filter_options,
    item_summary,
    last_ingested_at,
    no_standard_item_count,
    series_rows,
    time_bounds,
)
from app.timeutil import day_range_utc
from db.ingestion import upsert_measurements
from scripts.generate_dummy_data import build_dummy_frame

END = datetime(2026, 9, 28, tzinfo=UTC)
INGESTED = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)
ITEM = ["part", "operation", "characteristics"]
D = Decimal


@pytest.fixture
def data():
    return build_dummy_frame(END)


@pytest.fixture
def seeded(pg_engine, data):
    upsert_measurements(pg_engine, data, ingested_at=INGESTED)
    return pg_engine


def everything():
    start, end = day_range_utc(date(2026, 8, 1), date(2026, 9, 30))
    return Filters(start=start, end=end)


def expected_summary(df):
    std = df[df["zone"] != "NO_STANDARD"]
    last = std.sort_values("measured_at").groupby([*ITEM, "machine"]).tail(1)
    out = last.groupby(ITEM).agg(latest_ratio=("ratio", "max"), machine_count=("machine", "size"))
    out["max_ratio"] = std.groupby(ITEM)["ratio"].max()
    return out


def summary_by_item(df):
    return df.set_index(ITEM).sort_index()


def test_empty_database(pg_engine):
    assert last_ingested_at(pg_engine) is None
    assert time_bounds(pg_engine) == (None, None)
    assert filter_options(pg_engine).empty
    assert item_summary(pg_engine, everything()).empty
    assert no_standard_item_count(pg_engine, everything()) == 0
    assert series_rows(pg_engine, everything(), [("p", "o", "c")]).empty
    assert series_rows(pg_engine, everything(), []).empty


def test_last_ingested_and_time_bounds(seeded, data):
    assert last_ingested_at(seeded) == INGESTED
    lo, hi = time_bounds(seeded)
    assert lo == data["measured_at"].min().to_pydatetime() and hi == data["measured_at"].max().to_pydatetime()


def test_filter_options_exclude_no_standard_items(seeded):
    opts = filter_options(seeded)
    assert list(opts.columns) == ["part", "operation", "machine", "characteristics"]
    assert not opts["characteristics"].str.contains(r"\(info\)").any()
    assert len(opts[["part", "operation", "characteristics"]].drop_duplicates()) == 10    # 12 item - 2 tanpa standar
    assert len(opts) == 19                                                               # garis mesin ber-standar


def test_item_summary_matches_independent_pandas_computation(seeded, data):
    got, want = summary_by_item(item_summary(seeded, everything())), expected_summary(data).sort_index()
    assert list(got.index) == list(want.index) and len(got) == 10
    assert list(got["latest_ratio"]) == list(want["latest_ratio"])            # Decimal persis
    assert list(got["max_ratio"]) == list(want["max_ratio"])
    assert list(got["machine_count"]) == list(want["machine_count"])
    assert isinstance(got["latest_ratio"].iloc[0], D)


def test_latest_ratio_is_worst_machine_latest_not_overall_max(seeded):
    row = summary_by_item(item_summary(seeded, everything())).loc[("Demo Cyl.Head", "Op 140-180", "True Pos Hole 1")]
    assert row["machine_count"] == 3
    assert row["max_ratio"] >= row["latest_ratio"]                            # puncak dalam rentang >= kondisi terakhir
    assert row["latest_ratio"] > D("1.0")                                     # CH-A drift berakhir NG


def test_item_summary_respects_machine_filter(seeded, data):
    f = Filters(**{**everything().__dict__, "machines": ("CH-A",)})
    got = summary_by_item(item_summary(seeded, f))
    want = expected_summary(data[data["machine"] == "CH-A"]).sort_index()
    assert list(got.index) == list(want.index)
    assert list(got["latest_ratio"]) == list(want["latest_ratio"]) and set(got["machine_count"]) == {1}


def test_item_summary_respects_jenis_and_characteristics_filters(seeded):
    f = Filters(**{**everything().__dict__, "jenis": (("Demo Conrod", "Op 30 Honing"),)})
    got = item_summary(seeded, f)
    assert set(got["part"]) == {"Demo Conrod"} and len(got) == 3            # Weight (info) tidak ikut
    f = Filters(**{**everything().__dict__, "characteristics": ("Runout", "Bore Dia")})
    assert set(item_summary(seeded, f)["characteristics"]) == {"Runout", "Bore Dia"}
    two = (("Demo Conrod", "Op 30 Honing"), ("Demo Crankshaft", "Op 20 Grinding"))
    assert set(item_summary(seeded, Filters(**{**everything().__dict__, "jenis": two}))["part"]) == \
        {"Demo Conrod", "Demo Crankshaft"}


def test_date_range_filter_uses_wib_days(seeded, data):
    last_day = (data["measured_at"].max() + timedelta(hours=7)).date()
    start, end = day_range_utc(last_day - timedelta(days=6), last_day)
    week = summary_by_item(item_summary(seeded, Filters(start=start, end=end)))
    full = summary_by_item(item_summary(seeded, everything()))
    assert list(week["latest_ratio"]) == list(full["latest_ratio"])          # kondisi terakhir sama
    # puncak di rentang pendek tidak mungkin melebihi puncak rentang penuh
    assert all(w <= f for w, f in zip(week["max_ratio"], full["max_ratio"], strict=True))
    start, end = day_range_utc(date(2026, 1, 1), date(2026, 1, 2))
    assert item_summary(seeded, Filters(start=start, end=end)).empty


def test_no_standard_items_counted_separately(seeded):
    assert no_standard_item_count(seeded, everything()) == 2
    f = Filters(**{**everything().__dict__, "jenis": (("Demo Cyl.Head", "Op 140-180"),)})
    assert no_standard_item_count(seeded, f) == 0


def test_series_rows_for_requested_items_include_every_machine_in_time_order(seeded, data):
    items = [("Demo Cyl.Head", "Op 140-180", "True Pos Hole 1"), ("Demo Conrod", "Op 30 Honing", "Surface Roughness")]
    got = series_rows(seeded, everything(), items)
    assert set(zip(got["part"], got["operation"], got["characteristics"], strict=True)) == set(items)
    assert set(got[got["characteristics"] == "True Pos Hole 1"]["machine"]) == {"CH-A", "CH-B", "CH-C"}
    assert got.groupby(["characteristics", "machine"])["measured_at"].apply(lambda s: s.is_monotonic_increasing).all()
    want = data[data.set_index(ITEM).index.isin(items)]
    assert len(got) == len(want)
    assert not (got["zone"] == "NO_STANDARD").any()
    assert isinstance(got["value"].iloc[0], D)                                # Decimal, bukan float


def test_one_chart_is_one_item_cek_even_when_characteristics_names_collide(pg_engine):
    """Karakteristik bernama sama pada Part/Operation berbeda = item cek berbeda, tidak pernah dicampur
    dalam satu chart; garis dalam satu chart hanya berbeda mesin."""
    from tests.test_ingestion import T0, frame, row
    rest = row()[5:]
    upsert_measurements(pg_engine, frame(
        ("Part X", "Op 1", "M1", "Same Name", T0, *rest),
        ("Part X", "Op 1", "M2", "Same Name", T0, *rest),
        ("Part Y", "Op 2", "M3", "Same Name", T0, *rest),
    ))
    start, end = day_range_utc(date(2026, 1, 1), date(2026, 1, 31))              # T0 = 5 Januari 2026
    f = Filters(start=start, end=end)
    assert len(item_summary(pg_engine, f)) == 2                                  # dua item cek, bukan satu
    got = series_rows(pg_engine, f, [("Part X", "Op 1", "Same Name")])
    assert set(got["part"]) == {"Part X"} and set(got["operation"]) == {"Op 1"}
    assert set(got["machine"]) == {"M1", "M2"}                                   # mesin berbeda, item cek sama


def test_series_rows_never_returns_no_standard_items(seeded):
    got = series_rows(seeded, everything(), [("Demo Conrod", "Op 30 Honing", "Weight (info)")])
    assert got.empty


def test_series_rows_respects_filters(seeded):
    f = Filters(**{**everything().__dict__, "machines": ("CH-B",)})
    got = series_rows(seeded, f, [("Demo Cyl.Head", "Op 140-180", "True Pos Hole 1")])
    assert set(got["machine"]) == {"CH-B"}


def test_time_window_is_start_inclusive_end_exclusive(seeded, data):
    t = data[data["characteristics"] == "Flatness Face"]["measured_at"].iloc[10].to_pydatetime()
    item = [("Demo Cyl.Head", "Op 140-180", "Flatness Face")]
    assert len(series_rows(seeded, Filters(start=t, end=t + timedelta(seconds=1)), item)) == 1
    assert series_rows(seeded, Filters(start=t - timedelta(seconds=1), end=t), item).empty


def test_values_are_bound_parameters_not_sql(seeded):
    evil = "CH-A'; DROP TABLE measurements; --"
    f = Filters(**{**everything().__dict__, "machines": (evil,), "characteristics": (evil,),
                   "jenis": ((evil, evil),)})
    assert item_summary(seeded, f).empty
    assert series_rows(seeded, f, [(evil, evil, evil)]).empty
    with seeded.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM measurements")).scalar_one() > 1500
