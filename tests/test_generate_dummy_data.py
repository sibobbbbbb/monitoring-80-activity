from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import func, select

from core.rules import classify, compute_ratio
from db.tables import measurements
from scripts.generate_dummy_data import DUMMY_BATCH, build_dummy_frame, main

END = datetime(2026, 9, 28, tzinfo=timezone.utc)
ITEM = ["part", "operation", "characteristics"]


@pytest.fixture(scope="module")
def frame():
    return build_dummy_frame(END)


def latest_item_zones(df):
    """Zona dari rasio terburuk di antara pengukuran terakhir tiap mesin, per item cek (tanpa NO_STANDARD)."""
    std = df[df["zone"] != "NO_STANDARD"]
    last = std.sort_values("measured_at").groupby(ITEM + ["machine"]).tail(1)
    return last.groupby(ITEM)["ratio"].max().map(classify)


class TestStructure:
    def test_at_least_three_jenis_item_cek_each_with_2_to_4_characteristics(self, frame):
        per_jenis = frame.groupby(["part", "operation"])["characteristics"].nunique()
        assert len(per_jenis) >= 3
        assert per_jenis.between(2, 4).all()

    def test_each_item_cek_has_1_to_3_machines_and_some_have_several(self, frame):
        machines = frame.groupby(ITEM)["machine"].nunique()
        assert machines.between(1, 3).all()
        assert (machines >= 2).sum() >= 3 and (machines == 3).sum() >= 1     # bahan uji multi-line per chart

    def test_no_duplicate_keys(self, frame):
        key = ["part", "operation", "machine", "characteristics", "measured_at"]
        assert not frame.duplicated(key).any()

    def test_time_span_and_density_make_a_visible_trend(self, frame):
        assert (frame["measured_at"].max() - frame["measured_at"].min()).days >= 26
        per_series = frame.groupby(ITEM + ["machine"]).size()
        assert per_series.min() >= 60
        assert frame["measured_at"].max() <= pd.Timestamp(END)

    def test_source_batch_marks_dummy_rows(self, frame):
        assert set(frame["source_batch"]) == {DUMMY_BATCH}


class TestCoverage:
    def test_all_zones_present(self, frame):
        assert set(frame["zone"]) == {"OK", "WARNING", "NG", "NO_STANDARD"}

    def test_no_standard_rows_have_no_limits_and_null_ratio(self, frame):
        ns = frame[frame["zone"] == "NO_STANDARD"]
        assert len(ns) > 0 and ns["ratio"].isna().all() and ns["usl"].isna().all() and ns["lsl"].isna().all()
        assert ns.groupby(ITEM).ngroups >= 2                                 # beberapa item tanpa standar

    def test_limit_patterns_are_varied(self, frame):
        spec = frame.drop_duplicates(ITEM)
        two_sided = spec[spec["usl"].notna() & spec["lsl"].notna() & spec["nominal"].notna()]
        upper_nominal = spec[spec["usl"].notna() & spec["lsl"].isna() & spec["nominal"].notna()]
        lower_nominal = spec[spec["usl"].isna() & spec["lsl"].notna() & spec["nominal"].notna()]
        upper_no_nominal = spec[spec["usl"].notna() & spec["nominal"].isna()]
        assert len(two_sided) and len(upper_nominal) and len(lower_nominal) and len(upper_no_nominal)
        assert (upper_no_nominal["lsl"] == Decimal("0")).all()               # LSL=0 seperti sample asli
        asym = two_sided[(two_sided["usl"] - two_sided["nominal"]) != (two_sided["nominal"] - two_sided["lsl"])]
        assert len(asym) >= 1

    def test_latest_state_spans_ng_warning_and_ok_items(self, frame):
        assert set(latest_item_zones(frame)) == {"NG", "WARNING", "OK"}

    def test_stored_ratio_and_zone_match_core_rules(self, frame):
        for r in frame.sample(200, random_state=1).itertuples():
            res = compute_ratio(r.value, r.nominal, r.usl, r.lsl)
            assert (r.ratio, r.zone) == (res.ratio, res.zone.value)

    def test_values_respect_physical_lower_bound_for_lsl_zero(self, frame):
        pos = frame[(frame["lsl"] == Decimal("0")) & frame["nominal"].isna()]
        assert (pos["value"] >= 0).all()


class TestDeterminism:
    def test_same_arguments_same_frame(self, frame):
        pd.testing.assert_frame_equal(frame, build_dummy_frame(END))

    def test_different_seed_differs(self, frame):
        assert not frame["value"].equals(build_dummy_frame(END, seed=7)["value"])


def count(engine, where=None):
    with engine.connect() as c:
        stmt = select(func.count()).select_from(measurements)
        return c.execute(stmt.where(where) if where is not None else stmt).scalar_one()


def test_main_inserts_via_ingestion_and_is_idempotent(pg_engine, monkeypatch, capsys):
    monkeypatch.setattr("db.connection.get_engine", lambda *a, **k: pg_engine)
    assert main(["--end", "2026-09-28", "--reset"]) == 0
    out = capsys.readouterr().out
    n = count(pg_engine)
    assert n > 1500 and "12 item cek" in out and "baru" in out

    assert main(["--end", "2026-09-28"]) == 0
    assert count(pg_engine) == n                                              # upsert, bukan duplikasi
    assert f"0 baru, 0 ter-update, {n} tidak berubah" in capsys.readouterr().out


def test_reset_removes_only_dummy_rows(pg_engine, monkeypatch, capsys):
    monkeypatch.setattr("db.connection.get_engine", lambda *a, **k: pg_engine)
    with pg_engine.begin() as c:
        c.execute(measurements.insert().values(
            part="Real Part", operation="Op", machine="M", characteristics="C",
            measured_at=END, value=Decimal("1"), zone="OK", source_batch="upload.xlsx"))
    main(["--end", "2026-09-28", "--reset"])
    main(["--end", "2026-09-28", "--reset"])                                  # reset kedua tidak menambah baris
    real = measurements.c.source_batch == "upload.xlsx"
    assert count(pg_engine, real) == 1
    assert count(pg_engine, measurements.c.source_batch == DUMMY_BATCH) == count(pg_engine) - 1
