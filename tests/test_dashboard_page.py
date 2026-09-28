"""Test halaman dashboard (app/dashboard.py) lewat streamlit.testing AppTest, terhadap Postgres sungguhan
dengan data dummy yang masuk lewat db/ingestion.py.

Catatan: jangan meng-import app.dashboard dari test; itu skrip Streamlit yang langsung berjalan saat di-import.
"""
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from app.timeutil import WIB
from db.ingestion import upsert_measurements
from scripts.generate_dummy_data import build_dummy_frame
from tests.test_ingestion import frame, row

PAGE = Path(__file__).resolve().parent.parent / "app" / "dashboard.py"
END = datetime(2026, 9, 28, tzinfo=UTC)
SEVERITY = {"NG": 2, "WARNING": 1, "OK": 0}
BADGE = re.compile(r"-badge\[(NG|WARNING|OK) · (\d+)%\]")


@pytest.fixture
def dash(pg_engine, monkeypatch):
    st.cache_data.clear()                                 # cache antar test tidak boleh bocor
    monkeypatch.setattr("db.connection.get_engine", lambda *a, **k: pg_engine)

    def run(seed=True):
        if seed:
            upsert_measurements(pg_engine, build_dummy_frame(END))
        return AppTest.from_file(str(PAGE), default_timeout=90).run()

    yield SimpleNamespace(run=run, engine=pg_engine)
    st.cache_data.clear()


def n_charts(at):
    return len(at.get("plotly_chart"))


def badges(at):
    """[(zona, persen)] sesuai urutan tampil."""
    found = [BADGE.search(m.value) for m in at.markdown]
    return [(m.group(1), int(m.group(2))) for m in found if m]


def metrics(at):
    return {m.label: m.value for m in at.metric}


def refresh_button(at):
    return next(b for b in at.button if b.label == "Refresh Sekarang")


def test_renders_one_chart_per_item_cek_without_errors(dash):
    at = dash.run()
    assert not at.exception and not at.error
    assert n_charts(at) == 10                             # 12 item cek - 2 tanpa standar
    m = metrics(at)
    assert m["Item cek"] == "10" and int(m["NG"]) + int(m["WARNING"]) + int(m["OK"]) == 10
    assert all(int(m[z]) >= 1 for z in ("NG", "WARNING", "OK"))
    assert any("2 item cek tanpa standar" in c.value for c in at.caption)


def test_default_order_is_ng_then_warning_then_ok_by_descending_ratio(dash):
    at = dash.run()
    seq = badges(at)
    assert len(seq) == 10
    severities = [SEVERITY[z] for z, _ in seq]
    assert severities == sorted(severities, reverse=True)
    assert seq[0][0] == "NG" and seq[-1][0] == "OK"
    pcts = [p for _, p in seq]
    assert pcts == sorted(pcts, reverse=True)


def test_ascending_toggle_reverses_the_order(dash):
    at = dash.run()
    desc = badges(at)
    order = at.radio(key="f_order")
    order.set_value(order.options[1]).run()               # opsi kedua = rasio rendah -> tinggi
    asc = badges(at)
    assert not at.exception
    assert [SEVERITY[z] for z, _ in asc] == sorted(SEVERITY[z] for z, _ in asc)
    assert asc[0][0] == "OK" and asc[-1][0] == "NG"
    assert sorted(asc) == sorted(desc)                    # item yang sama, hanya urutan berbeda


def test_peak_basis_orders_by_highest_ratio_in_range(dash):
    at = dash.run()
    basis = at.radio(key="f_basis")
    basis.set_value(basis.options[1]).run()               # opsi kedua = rasio tertinggi dalam rentang
    pcts = [p for _, p in badges(at)]
    assert not at.exception and pcts == sorted(pcts, reverse=True) and len(pcts) == 10


def test_filter_by_jenis_item_cek(dash):
    at = dash.run()
    at.multiselect(key="f_jenis").select("Demo Conrod · Op 30 Honing").run()
    assert not at.exception
    assert n_charts(at) == 3 and metrics(at)["Item cek"] == "3"
    assert "Weight (info)" not in " ".join(m.value for m in at.markdown)


def test_filter_by_item_cek_narrows_machine_options(dash):
    at = dash.run()
    at.multiselect(key="f_chars").select("Runout").run()
    assert n_charts(at) == 1
    assert at.multiselect(key="f_machines").options == ["GR-1", "GR-2", "GR-3"]


def test_filter_by_machine_keeps_only_items_that_machine_works_on(dash):
    at = dash.run()
    at.multiselect(key="f_machines").select("CH-C").run()
    assert not at.exception and n_charts(at) == 1         # hanya True Pos Hole 1 dikerjakan CH-C


def range_caption(at):
    return next(c.value for c in at.caption if c.value.startswith("Rentang tanggal:"))


def test_default_range_is_all_data(dash):
    at = dash.run()
    assert at.button_group(key="f_preset").value == "Semua data"
    assert range_caption(at) == "Rentang tanggal: 31 Aug 2026 – 28 Sep 2026 (WIB)"
    assert not at.date_input                          # pemilih tanggal hanya muncul pada mode Kustom


@pytest.mark.parametrize(("preset", "first_day"), [
    ("7 hari", "22 Sep 2026"), ("14 hari", "15 Sep 2026"), ("30 hari", "30 Aug 2026"), ("90 hari", "01 Jul 2026"),
])
def test_quick_ranges_count_back_from_the_latest_data_day(dash, preset, first_day):
    at = dash.run()
    at.button_group(key="f_preset").set_value(preset).run()
    assert not at.exception
    assert range_caption(at) == f"Rentang tanggal: {first_day} – 28 Sep 2026 (WIB)"
    assert n_charts(at) == 10                         # data dummy padat: semua item cek punya data di rentang ini


def test_custom_range_can_go_back_before_the_first_data_day(dash):
    at = dash.run()
    at.button_group(key="f_preset").set_value("Kustom").run()
    picker = at.date_input(key="f_dates")
    assert picker.min <= date(2026, 7, 1)             # tidak lagi dikunci ke tanggal data tertua (31 Agustus)
    assert picker.max >= datetime.now(WIB).date()     # batas akhir tetap sampai hari ini atau data terbaru

    picker.set_value((date(2026, 7, 1), date(2026, 7, 31))).run()      # sebelum ada data
    assert not at.exception and n_charts(at) == 0
    assert range_caption(at) == "Rentang tanggal: 01 Jul 2026 – 31 Jul 2026 (WIB)"
    assert any("Tidak ada item cek" in i.value for i in at.info)

    at.date_input(key="f_dates").set_value((date(2026, 9, 20), date(2026, 9, 28))).run()
    assert not at.exception and n_charts(at) == 10


def test_incomplete_custom_range_waits_for_the_end_date(dash):
    at = dash.run()
    at.button_group(key="f_preset").set_value("Kustom").run()
    at.date_input(key="f_dates").set_value((date(2026, 9, 20),)).run()
    assert not at.exception and n_charts(at) == 0
    assert any("Pilih tanggal akhir" in i.value for i in at.info)


def test_pagination_splits_grid_and_keeps_global_order(dash):
    at = dash.run()
    full = badges(at)
    at.selectbox(key="f_pagesize").set_value(6).run()
    assert n_charts(at) == 6 and badges(at) == full[:6]
    at.selectbox(key="f_page").set_value(2).run()
    assert not at.exception and n_charts(at) == 4 and badges(at) == full[6:]


def test_grid_defaults_to_two_wide_columns(dash):
    assert dash.run().selectbox(key="f_cols").value == 2


def test_grid_columns_option_does_not_change_content(dash):
    at = dash.run()
    before = badges(at)
    for cols in (1, 3, 4):
        at.selectbox(key="f_cols").set_value(cols).run()
        assert not at.exception and badges(at) == before and n_charts(at) == 10


def test_single_item_still_renders_when_fewer_items_than_columns(dash):
    at = dash.run()
    at.multiselect(key="f_chars").select("Runout").run()
    at.selectbox(key="f_cols").set_value(4).run()
    assert not at.exception and n_charts(at) == 1


def test_shows_last_data_time(dash):
    at = dash.run()
    assert any("Data terakhir:" in m.value and "WIB" in m.value for m in at.markdown)


def test_empty_database_shows_guidance_and_no_charts(dash):
    at = dash.run(seed=False)
    assert not at.exception and n_charts(at) == 0
    assert any("Belum ada data" in i.value and "generate_dummy_data" in i.value for i in at.info)


def test_database_failure_shows_message_instead_of_crashing(dash, monkeypatch):
    def down(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("db.connection.get_engine", down)
    at = dash.run(seed=False)
    assert not at.exception
    assert any("Tidak dapat membaca database" in e.value for e in at.error)


def test_refresh_button_clears_cache_so_new_data_appears(dash):
    at = dash.run()
    assert n_charts(at) == 10

    # data baru masuk: item cek ke-11. Tanpa refresh, cache (TTL 60 dtk) masih menyajikan 10.
    new = ("Demo New", "Op X", "MC-9", "Fresh Item", END - timedelta(hours=2), *row()[5:])
    upsert_measurements(dash.engine, frame(new))
    at.run()
    assert n_charts(at) == 10

    refresh_button(at).click().run()
    assert not at.exception and n_charts(at) == 11
