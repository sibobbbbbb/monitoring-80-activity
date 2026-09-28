"""Test halaman upload (app/upload.py) lewat streamlit.testing AppTest, terhadap Postgres sungguhan.

st.file_uploader diganti file palsu; get_engine diarahkan ke schema tes sementara.
"""
import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st
from sqlalchemy import select
from streamlit.testing.v1 import AppTest

from app.queries import Filters, zone_counts
from app.timeutil import day_range_utc
from db.tables import measurements

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "app" / "upload.py"
SAMPLE = ROOT / "data" / "sample_measurements.csv"
HEADER = "Machine,Unit,Item,Measured At,Value,Nominal,USL,LSL\n"
CONFIRM = "confirm_upload"


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
        return c.execute(select(measurements).order_by(measurements.c.unit_id)).mappings().all()


def csv(*rows):
    return (HEADER + "".join(r + "\n" for r in rows)).encode()


def test_valid_file_preview_then_confirm_upserts(page):
    at = page.open(SAMPLE.read_bytes(), "sample_measurements.csv")
    assert not at.exception

    assert metrics(at) == {"Baris sukses": "6", "Baris gagal": "0", "Baris duplikat": "0"}
    preview = frame_with(at, "Rasio")
    assert len(preview) == 6
    assert frame_with(at, "Alasan") is None and frame_with(at, "Baris ditimpa") is None
    assert db_rows(page.engine) == []                    # preview belum menyimpan apa pun
    assert not at.success and len(confirm_buttons(at)) == 1

    at = click_confirm(at)
    assert not at.exception
    assert metrics(at) == {"Baris sukses": "6", "Baris gagal": "0", "Baris duplikat": "0",
                           "Baris baru masuk": "6", "Ter-update (nilai berubah)": "0", "Tidak berubah": "0"}
    assert len(at.success) == 1
    assert confirm_buttons(at) == []                     # tidak bisa konfirmasi dua kali
    assert page.cleared == [True]                        # cache dashboard dikosongkan

    rows = db_rows(page.engine)
    assert len(rows) == 6
    assert rows[0]["source_batch"].startswith("sample_measurements.csv#")
    start, end = day_range_utc(date(2026, 1, 5), date(2026, 1, 5))
    assert zone_counts(page.engine, Filters(start=start, end=end)) == {
        "OK": 1, "WARNING": 3, "NG": 1, "NO_STANDARD": 1}   # dashboard membaca data yang sama


def test_reupload_reports_unchanged_and_updated(page):
    click_confirm(page.open(SAMPLE.read_bytes(), "s.csv", file_id="f1"))

    at = click_confirm(page.open(SAMPLE.read_bytes(), "s.csv", file_id="f2"))
    assert metrics(at)["Baris baru masuk"] == "0" and metrics(at)["Tidak berubah"] == "6"
    assert len(db_rows(page.engine)) == 6

    edited = SAMPLE.read_text().replace("M01,U001,Diameter,2026-01-05 08:00:00,10.20",
                                        "M01,U001,Diameter,2026-01-05 08:00:00,10.45")
    at = click_confirm(page.open(edited.encode(), "s.csv", file_id="f3"))
    m = metrics(at)
    assert (m["Baris baru masuk"], m["Ter-update (nilai berubah)"], m["Tidak berubah"]) == ("0", "1", "5")
    updated = [r for r in db_rows(page.engine) if r["unit_id"] == "U001" and r["item_ukur"] == "Diameter"][0]
    assert updated["value"] == Decimal("10.45") and updated["zone"] == "WARNING"


def test_failed_rows_are_reported_and_do_not_stop_the_rest(page):
    data = csv(
        "M1,U1,D,2026-01-05 08:00:00,10.2,10,10.5,9.5",     # baris 2 valid
        "M1,,D,2026-01-05 08:01:00,10.2,10,10.5,9.5",       # baris 3: unit kosong
        "M1,U3,D,2026-01-05 08:02:00,abc,10,10.5,9.5",      # baris 4: value bukan angka
        "M1,U4,D,2026-01-05 08:03:00,10.2,10,10,9.5",       # baris 5: usl == nominal (ValueError core)
        "M1,U5,D,2026-01-05 08:04:00,10.3,10,10.5,9.5",     # baris 6 valid
    )
    at = page.open(data)
    assert not at.exception
    assert metrics(at) == {"Baris sukses": "2", "Baris gagal": "3", "Baris duplikat": "0"}

    failures = frame_with(at, "Alasan")
    assert list(failures["Baris"]) == [3, 4, 5]
    reasons = list(failures["Alasan"])
    assert "Unit" in reasons[0] and "value" in reasons[1] and "usl" in reasons[2]

    at = click_confirm(at)
    assert metrics(at)["Baris baru masuk"] == "2"
    assert [r["unit_id"] for r in db_rows(page.engine)] == ["U1", "U5"]
    assert any("3 baris gagal dilewati" in c.value for c in at.caption)


def test_duplicates_show_detail_and_last_row_wins_in_db(page):
    data = csv(
        "M1,U1,D,2026-01-05 08:00:00,10.1,10,10.5,9.5",     # baris 2 (ditimpa)
        "M1,U2,D,2026-01-05 08:00:00,10.2,10,10.5,9.5",     # baris 3
        "M1,U1,D,2026-01-05 08:00:00,10.3,10,10.5,9.5",     # baris 4 (menimpa baris 2)
    )
    at = page.open(data)
    assert not at.exception
    assert metrics(at) == {"Baris sukses": "2", "Baris gagal": "0", "Baris duplikat": "1"}

    dups = frame_with(at, "Baris ditimpa")
    assert len(dups) == 1
    assert dups.iloc[0]["Baris ditimpa"] == 2 and dups.iloc[0]["Ditimpa oleh baris"] == 4
    assert "unit_id=U1" in dups.iloc[0]["Keterangan"]

    at = click_confirm(at)
    assert metrics(at)["Baris baru masuk"] == "2"
    rows = db_rows(page.engine)
    assert len(rows) == 2
    assert rows[0]["unit_id"] == "U1" and rows[0]["value"] == Decimal("10.3")


def test_missing_required_column_shows_error_and_no_preview(page):
    data = (b"Machine,Unit,Item,Measured At,Nominal,USL,LSL\n"
            b"M1,U1,D,2026-01-05 08:00:00,10,10.5,9.5\n")
    at = page.open(data)
    assert not at.exception
    assert len(at.error) == 1 and "Value" in at.error[0].value
    assert metrics(at) == {} and confirm_buttons(at) == []


def test_no_file_selected_shows_nothing(page):
    at = page.open(None)
    assert not at.exception and metrics(at) == {} and not at.error and confirm_buttons(at) == []


def test_no_valid_rows_disables_confirm(page):
    at = page.open(csv("M1,,D,2026-01-05 08:00:00,10.2,10,10.5,9.5"))
    assert metrics(at)["Baris sukses"] == "0" and metrics(at)["Baris gagal"] == "1"
    assert confirm_buttons(at)[0].disabled is True
    assert len(at.info) == 1


def test_database_error_is_shown_and_cache_not_cleared(page, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("db.ingestion.upsert_measurements", boom)
    at = click_confirm(page.open(SAMPLE.read_bytes(), "s.csv"))
    assert not at.exception
    assert any("Gagal menyimpan" in e.value for e in at.error)
    assert len(confirm_buttons(at)) == 1                 # bisa dicoba lagi
    assert page.cleared == [] and db_rows(page.engine) == []
