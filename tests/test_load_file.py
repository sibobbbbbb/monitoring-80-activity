"""CLI db.load_file (alternatif headless untuk batch besar) berbagi adapter dan ingestion dengan halaman upload."""
from pathlib import Path

from sqlalchemy import func, select

from db.load_file import main
from db.tables import measurements

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample_measurements.csv"


def count(engine):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(measurements)).scalar_one()


def test_loads_file_then_reports_unchanged_on_second_run(pg_engine, monkeypatch, capsys):
    monkeypatch.setattr("db.load_file.get_engine", lambda: pg_engine)
    assert main([str(SAMPLE)]) == 0
    assert "6 baru, 0 ter-update, 0 tidak berubah" in capsys.readouterr().out
    assert count(pg_engine) == 6

    assert main([str(SAMPLE)]) == 0
    assert "0 baru, 0 ter-update, 6 tidak berubah" in capsys.readouterr().out
    assert count(pg_engine) == 6


def test_prints_failures_and_warnings(pg_engine, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("db.load_file.get_engine", lambda: pg_engine)
    p = tmp_path / "in.csv"
    p.write_text("Machine,Unit,Item,Measured At,Value,Nominal,USL,LSL\n"
                 "M1,U1,D,2026-01-05 08:00:00,10.1,10,10.5,9.5\n"
                 "M1,U1,D,2026-01-05 08:00:00,10.3,10,10.5,9.5\n"
                 "M1,U2,D,2026-01-05 08:01:00,abc,10,10.5,9.5\n")
    assert main([str(p), "--batch", "cli-test"]) == 0
    out = capsys.readouterr().out
    assert "GAGAL   baris 4" in out and "WARNING baris 2" in out
    assert count(pg_engine) == 1


def test_missing_column_exits_with_code_2(tmp_path, capsys):
    p = tmp_path / "in.csv"
    p.write_text("Machine,Unit,Item,Measured At,Nominal,USL,LSL\nM1,U1,D,2026-01-05 08:00:00,10,10.5,9.5\n")
    assert main([str(p)]) == 2
    assert "Kolom wajib" in capsys.readouterr().err
