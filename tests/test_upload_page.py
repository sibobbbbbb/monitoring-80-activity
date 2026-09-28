"""Test halaman upload fallback (app/upload.py) lewat streamlit.testing AppTest, terhadap Postgres sungguhan.

st.file_uploader diganti file palsu; get_engine diarahkan ke schema tes sementara.
File yang diunggah berformat export FEXQMS (header + tabel, nilai dari XChart).
"""
import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import streamlit as st
from sqlalchemy import select
from streamlit.testing.v1 import AppTest

from app.queries import Filters, item_summary
from app.timeutil import day_range_utc
from db.tables import measurements
from tests.fexqms_fixture import FIRST_DATA_ROW, MACHINE, T0, export_grid, times, to_csv, to_xlsx

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "app" / "upload.py"
SAMPLE = ROOT / "data" / "sample_fexqms_export.csv"
CONFIRM = "confirm_upload"
HOUR = pd.Timedelta(hours=1)


class FakeUpload(io.BytesIO):
    def __init__(self, data: bytes, name: str, file_id: str):
        super().__init__(data)
        self.name, self.file_id, self.size = name, file_id, len(data)


@pytest.fixture
def page(pg_engine, monkeypatch):
    monkeypatch.setattr("db.connection.get_engine", lambda *a, **k: pg_engine)
    cleared = []
    monkeypatch.setattr(st.cache_data, "clear", lambda *a, **k: cleared.append(True))

    def open_page(data, name="data.csv", file_id="f1"):
        monkeypatch.setattr(
            st, "file_uploader",
            lambda *a, **k: None if data is None else FakeUpload(data, name, file_id))
        return AppTest.from_file(str(PAGE), default_timeout=30).run()

    return SimpleNamespace(open=open_page, cleared=cleared, engine=pg_engine)


def metrics(at):
    return {m.label: m.value for m in at.metric}


def frame_with(at, column):
    for d in at.dataframe:
        if column in d.value.columns:
            return d.value
    return None


def confirm_buttons(at):
    return [b for b in at.button if b.key == CONFIRM]


def click_confirm(at):
    return at.button(key=CONFIRM).click().run()


def db_rows(engine):
    with engine.connect() as c:
        return c.execute(select(measurements).order_by(measurements.c.measured_at)).mappings().all()


def export_csv(samples, **kw):
    return to_csv(export_grid(samples, **kw))


def test_page_states_it_is_a_fallback(page):
    at = page.open(None)
    assert not at.exception
    assert any("fallback" in i.value.lower() for i in at.info)


def test_valid_export_preview_then_confirm_upserts(page):
    at = page.open(SAMPLE.read_bytes(), "sample_fexqms_export.csv")
    assert not at.exception

    assert metrics(at) == {"Baris sukses": "8", "Baris gagal": "0", "Baris duplikat": "0"}
    preview = frame_with(at, "XChart")
    assert len(preview) == 8
    assert list(preview.columns[:5]) == ["Part", "Operation", "Mesin", "Characteristics", "Waktu (WIB)"]
    assert frame_with(at, "Alasan") is None and frame_with(at, "Baris ditimpa") is None
    assert db_rows(page.engine) == []                    # preview belum menyimpan apa pun
    assert not at.success and len(confirm_buttons(at)) == 1

    at = click_confirm(at)
    assert not at.exception
    assert metrics(at) == {"Baris sukses": "8", "Baris gagal": "0", "Baris duplikat": "0",
                           "Baris baru masuk": "8", "Ter-update (nilai berubah)": "0", "Tidak berubah": "0"}
    assert len(at.success) == 1
    assert confirm_buttons(at) == []                     # tidak bisa konfirmasi dua kali
    assert page.cleared == [True]                        # cache dashboard dikosongkan

    rows = db_rows(page.engine)
    assert len(rows) == 8
    assert rows[0]["source_batch"].startswith("sample_fexqms_export.csv#")
    assert (rows[0]["part"], rows[0]["operation"], rows[0]["machine"]) == ("Demo Part", "Demo Op 10", MACHINE)
    assert rows[3]["value"] == Decimal("0.16") and rows[3]["ratio"] == Decimal("0.8")
    assert rows[3]["usl"] == Decimal("0.2") and rows[3]["lsl"] == Decimal("0")

    # dashboard membaca data yang sama: satu item cek, rasio terakhir 0.5, tertinggi 1.05
    start, end = day_range_utc(date(2026, 1, 5), date(2026, 1, 5))
    summary = item_summary(page.engine, Filters(start=start, end=end))
    assert len(summary) == 1
    assert summary.iloc[0]["latest_ratio"] == Decimal("0.5") and summary.iloc[0]["max_ratio"] == Decimal("1.05")


def test_xlsx_export_with_summary_sheet_is_accepted(page):
    grid = export_grid(list(zip(times(3), [0.05, 0.09, 0.12])))
    at = click_confirm(page.open(to_xlsx(grid), "CONTROL_CHART.xlsx"))
    assert not at.exception
    assert metrics(at)["Baris baru masuk"] == "3" and len(db_rows(page.engine)) == 3


def test_reupload_reports_unchanged_and_updated(page):
    click_confirm(page.open(SAMPLE.read_bytes(), "s.csv", file_id="f1"))

    at = click_confirm(page.open(SAMPLE.read_bytes(), "s.csv", file_id="f2"))
    assert metrics(at)["Baris baru masuk"] == "0" and metrics(at)["Tidak berubah"] == "8"
    assert len(db_rows(page.engine)) == 8

    edited = SAMPLE.read_text().replace(",0.1,,0.05,", ",0.1,,0.17,")        # XChart baris pertama 0.05 -> 0.17
    assert edited != SAMPLE.read_text()
    at = click_confirm(page.open(edited.encode(), "s.csv", file_id="f3"))
    m = metrics(at)
    assert (m["Baris baru masuk"], m["Ter-update (nilai berubah)"], m["Tidak berubah"]) == ("0", "1", "7")
    first = db_rows(page.engine)[0]
    assert first["value"] == Decimal("0.17") and first["zone"] == "WARNING"


def test_failed_rows_are_reported_and_do_not_stop_the_rest(page):
    data = export_csv([
        (T0, 0.05),                       # baris 10 valid
        (T0 + HOUR, None),                # baris 11: XChart kosong
        (T0 + 2 * HOUR, "abc"),           # baris 12: XChart bukan angka
        ("bukan-tanggal", 0.05),          # baris 13: waktu invalid
        (T0 + 4 * HOUR, 0.06),            # baris 14 valid
    ])
    at = page.open(data)
    assert not at.exception
    assert metrics(at) == {"Baris sukses": "2", "Baris gagal": "3", "Baris duplikat": "0"}

    failures = frame_with(at, "Alasan")
    assert list(failures["Baris"]) == [11, 12, 13]
    reasons = list(failures["Alasan"])
    assert "XChart" in reasons[0] and "bukan angka" in reasons[1] and "Sample Date Time" in reasons[2]

    at = click_confirm(at)
    assert metrics(at)["Baris baru masuk"] == "2"
    assert [r["value"] for r in db_rows(page.engine)] == [Decimal("0.05"), Decimal("0.06")]
    assert any("3 baris gagal dilewati" in c.value for c in at.caption)


def test_invalid_spec_row_is_reported_not_crash(page):
    data = export_csv([(T0, 10.2), (T0 + HOUR, 9.8)], usl=10, lsl=9.5, nominal=10)   # USL == nominal
    at = page.open(data)
    assert not at.exception
    assert metrics(at)["Baris sukses"] == "1" and metrics(at)["Baris gagal"] == "1"
    assert "usl" in frame_with(at, "Alasan").iloc[0]["Alasan"]
    click_confirm(at)
    assert [r["value"] for r in db_rows(page.engine)] == [Decimal("9.8")]


def test_duplicates_show_detail_and_last_row_wins_in_db(page):
    data = export_csv([
        (T0, 0.05),                       # baris 10 (ditimpa)
        (T0 + HOUR, 0.06),                # baris 11
        (T0, 0.07),                       # baris 12 (menimpa baris 10)
    ])
    at = page.open(data)
    assert not at.exception
    assert metrics(at) == {"Baris sukses": "2", "Baris gagal": "0", "Baris duplikat": "1"}

    dups = frame_with(at, "Baris ditimpa")
    assert len(dups) == 1
    assert dups.iloc[0]["Baris ditimpa"] == FIRST_DATA_ROW and dups.iloc[0]["Ditimpa oleh baris"] == FIRST_DATA_ROW + 2
    assert f"machine={MACHINE}" in dups.iloc[0]["Keterangan"]

    at = click_confirm(at)
    assert metrics(at)["Baris baru masuk"] == "2"
    rows = db_rows(page.engine)                            # urut waktu: T0 lalu T0+1 jam
    assert [r["value"] for r in rows] == [Decimal("0.07"), Decimal("0.06")]   # T0 berisi nilai baris terakhir


def test_missing_required_column_shows_error_and_no_preview(page):
    grid = export_grid([(T0, 0.05)])
    grid[8][16] = "Other"                                  # kolom XChart hilang
    at = page.open(to_csv(grid))
    assert not at.exception
    assert len(at.error) == 1 and "XChart" in at.error[0].value
    assert metrics(at) == {} and confirm_buttons(at) == []


def test_missing_header_value_shows_error(page):
    at = page.open(export_csv([(T0, 0.05)], operation=None))
    assert len(at.error) == 1 and "Operation:" in at.error[0].value
    assert metrics(at) == {} and confirm_buttons(at) == []


def test_no_file_selected_shows_no_preview(page):
    at = page.open(None)
    assert not at.exception and metrics(at) == {} and not at.error and confirm_buttons(at) == []


def test_no_valid_rows_disables_confirm(page):
    at = page.open(export_csv([(T0, "abc")]))
    assert metrics(at)["Baris sukses"] == "0" and metrics(at)["Baris gagal"] == "1"
    assert confirm_buttons(at)[0].disabled is True
    assert any("Tidak ada baris valid" in i.value for i in at.info)


def test_database_error_is_shown_and_cache_not_cleared(page, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("db.ingestion.upsert_measurements", boom)
    at = click_confirm(page.open(SAMPLE.read_bytes(), "s.csv"))
    assert not at.exception
    assert any("Gagal menyimpan" in e.value for e in at.error)
    assert len(confirm_buttons(at)) == 1                 # bisa dicoba lagi
    assert page.cleared == [] and db_rows(page.engine) == []
