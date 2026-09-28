"""CLI db.load_file (alternatif headless untuk batch besar) berbagi adapter dan ingestion dengan halaman upload."""
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select

from db.load_file import main
from db.tables import measurements
from tests.fexqms_fixture import T0, export_grid, to_csv

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample_fexqms_export.csv"


def count(engine):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(measurements)).scalar_one()


def test_loads_file_then_reports_unchanged_on_second_run(pg_engine, monkeypatch, capsys):
    monkeypatch.setattr("db.load_file.get_engine", lambda: pg_engine)
    assert main([str(SAMPLE)]) == 0
    assert "8 baru, 0 ter-update, 0 tidak berubah" in capsys.readouterr().out
    assert count(pg_engine) == 8

    assert main([str(SAMPLE)]) == 0
    assert "0 baru, 0 ter-update, 8 tidak berubah" in capsys.readouterr().out
    assert count(pg_engine) == 8


def test_prints_failures_and_warnings(pg_engine, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("db.load_file.get_engine", lambda: pg_engine)
    p = tmp_path / "in.csv"
    p.write_bytes(to_csv(export_grid([
        (T0, 0.05),                                   # baris 10 (ditimpa baris 11)
        (T0, 0.06),                                   # baris 11
        (T0 + pd.Timedelta(hours=1), "abc"),          # baris 12 gagal
    ])))
    assert main([str(p), "--batch", "cli-test"]) == 0
    out = capsys.readouterr().out
    assert "GAGAL   baris 12" in out and "WARNING baris 10" in out
    assert count(pg_engine) == 1


def test_missing_column_exits_with_code_2(tmp_path, capsys):
    grid = export_grid([(T0, 0.05)])
    grid[8][16] = "Other"                             # kolom XChart hilang
    p = tmp_path / "in.csv"
    p.write_bytes(to_csv(grid))
    assert main([str(p)]) == 2
    assert "Kolom wajib" in capsys.readouterr().err
