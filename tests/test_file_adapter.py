from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from adapters.file_adapter import MissingColumnsError, load_measurements, load_measurements_from_bytes

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample_measurements.csv"
HEADER = "Machine,Unit,Item,Measured At,Value,Nominal,USL,LSL\n"


def write_csv(tmp_path, body, header=HEADER):
    p = tmp_path / "in.csv"
    p.write_text(header + body, encoding="utf-8")
    return p


class TestValidFile:
    def test_sample_file_parses_all_rows(self):
        df, failures, _ = load_measurements(SAMPLE)
        assert failures == []
        assert len(df) == 6
        assert set(df["source_batch"]) == {"sample_measurements.csv"}

    def test_ratio_and_zone_computed(self):
        df, _, _ = load_measurements(SAMPLE)
        by = {(r.unit_id, r.item_ukur): r for r in df.itertuples()}
        assert by[("U001", "Diameter")].ratio == Decimal("0.4")
        assert by[("U001", "Diameter")].zone == "OK"
        assert by[("U001", "Length")].zone == "WARNING"      # 0.44/0.5 = 0.88
        assert by[("U002", "Diameter")].zone == "NG"         # 0.6/0.5 = 1.2
        assert by[("U010", "Flatness")].zone == "WARNING"    # atas saja tanpa nominal: 0.09/0.1
        assert by[("U010", "Depth")].zone == "WARNING"       # bawah saja: 0.9/1
        assert by[("U011", "Note")].zone == "NO_STANDARD"
        assert by[("U011", "Note")].ratio is None

    def test_values_are_decimal(self):
        df, _, _ = load_measurements(SAMPLE)
        assert all(isinstance(v, Decimal) for v in df["value"])

    def test_time_converted_wib_to_utc(self):
        df, _, _ = load_measurements(SAMPLE)
        first = df.iloc[0]["measured_at"]
        assert first == pd.Timestamp(datetime(2026, 1, 5, 1, 0, 0, tzinfo=timezone.utc))

    def test_custom_column_map(self, tmp_path):
        p = tmp_path / "x.csv"
        p.write_text("mc,u,itm,ts,val,nom,hi,lo\nM1,U1,D,2026-01-05 08:00:00,10.2,10,10.5,9.5\n")
        cmap = {"machine_id": "mc", "unit_id": "u", "item_ukur": "itm", "measured_at": "ts",
                "value": "val", "nominal": "nom", "usl": "hi", "lsl": "lo"}
        df, failures, _ = load_measurements(p, column_map=cmap)
        assert failures == [] and len(df) == 1

    def test_xlsx(self, tmp_path):
        p = tmp_path / "x.xlsx"
        pd.read_csv(SAMPLE, dtype=str).to_excel(p, index=False)
        df, failures, _ = load_measurements(p)
        assert failures == [] and len(df) == 6


class TestDuplicateKeys:
    def test_duplicate_reported_as_warning_last_row_wins(self, tmp_path):
        p = write_csv(tmp_path,
                      "M1,U1,D,2026-01-05 08:00:00,10.1,10,10.5,9.5\n"     # baris 2 (ditimpa)
                      "M1,U2,D,2026-01-05 08:00:00,10.2,10,10.5,9.5\n"     # baris 3
                      "M1,U1,D,2026-01-05 08:00:00,10.3,10,10.5,9.5\n")    # baris 4 (menang)
        df, failures, warnings = load_measurements(p)
        assert failures == []
        assert len(df) == 2
        assert list(df["unit_id"]) == ["U2", "U1"]           # urut sesuai baris yang bertahan
        assert df.iloc[1]["value"] == Decimal("10.3")
        assert len(warnings) == 1
        assert warnings[0].row_number == 2
        assert "baris 4" in warnings[0].reason
        assert warnings[0].overwritten_by == 4
        assert "unit_id=U1" in warnings[0].reason

    def test_three_way_duplicate_reports_every_overwritten_row(self, tmp_path):
        p = write_csv(tmp_path,
                      "M1,U1,D,2026-01-05 08:00:00,10.1,10,10.5,9.5\n"
                      "M1,U1,D,2026-01-05 08:00:00,10.2,10,10.5,9.5\n"
                      "M1,U1,D,2026-01-05 08:00:00,10.3,10,10.5,9.5\n")
        df, _, warnings = load_measurements(p)
        assert len(df) == 1 and df.iloc[0]["value"] == Decimal("10.3")
        assert [w.row_number for w in warnings] == [2, 3]
        assert all("baris 4" in w.reason for w in warnings)
        assert [w.overwritten_by for w in warnings] == [4, 4]

    def test_same_instant_in_different_timezone_notation_is_duplicate(self, tmp_path):
        p = write_csv(tmp_path,
                      "M1,U1,D,2026-01-05 08:00:00,10.1,10,10.5,9.5\n"
                      "M1,U1,D,2026-01-05 01:00:00+00:00,10.2,10,10.5,9.5\n")
        df, _, warnings = load_measurements(p)
        assert len(df) == 1 and [w.row_number for w in warnings] == [2]

    def test_failed_row_does_not_overwrite(self, tmp_path):
        p = write_csv(tmp_path,
                      "M1,U1,D,2026-01-05 08:00:00,10.1,10,10.5,9.5\n"
                      "M1,U1,D,2026-01-05 08:00:00,abc,10,10.5,9.5\n")     # gagal validasi
        df, failures, warnings = load_measurements(p)
        assert len(df) == 1 and df.iloc[0]["value"] == Decimal("10.1")
        assert len(failures) == 1 and warnings == []

    def test_no_duplicates_no_warnings(self):
        _, _, warnings = load_measurements(SAMPLE)
        assert warnings == []


class TestFromBytes:
    def test_same_result_as_file_path(self):
        data = SAMPLE.read_bytes()
        from_bytes = load_measurements_from_bytes(data, "upload.csv")
        from_path = load_measurements(SAMPLE)
        pd.testing.assert_frame_equal(
            from_bytes.data.drop(columns="source_batch"), from_path.data.drop(columns="source_batch"))
        assert set(from_bytes.data["source_batch"]) == {"upload.csv"}

    def test_explicit_source_batch(self):
        res = load_measurements_from_bytes(SAMPLE.read_bytes(), "a.csv", source_batch="batch-1")
        assert set(res.data["source_batch"]) == {"batch-1"}

    def test_csv_with_utf8_bom_from_excel(self):
        data = b"\xef\xbb\xbf" + SAMPLE.read_bytes()
        res = load_measurements_from_bytes(data, "excel.csv")
        assert len(res.data) == 6 and res.failures == []

    def test_xlsx_bytes(self, tmp_path):
        p = tmp_path / "x.xlsx"
        pd.read_csv(SAMPLE, dtype=str).to_excel(p, index=False)
        res = load_measurements_from_bytes(p.read_bytes(), "x.xlsx")
        assert len(res.data) == 6 and res.failures == []

    def test_missing_required_column(self):
        data = b"Machine,Unit,Item,Measured At,Nominal,USL,LSL\nM1,U1,D,2026-01-05 08:00:00,10,10.5,9.5\n"
        with pytest.raises(MissingColumnsError, match="Value"):
            load_measurements_from_bytes(data, "x.csv")

    def test_unsupported_extension(self):
        with pytest.raises(ValueError, match="tidak didukung"):
            load_measurements_from_bytes(b"a", "x.txt")


class TestFailures:
    def test_missing_cell_reported_others_processed(self, tmp_path):
        p = write_csv(tmp_path,
                      "M1,U1,D,2026-01-05 08:00:00,10.2,10,10.5,9.5\n"
                      "M1,,D,2026-01-05 08:01:00,10.2,10,10.5,9.5\n"      # unit kosong
                      "M1,U3,D,2026-01-05 08:02:00,10.3,10,10.5,9.5\n")
        df, failures, _ = load_measurements(p)
        assert list(df["unit_id"]) == ["U1", "U3"]
        assert len(failures) == 1
        assert failures[0].row_number == 3
        assert "Unit" in failures[0].reason

    def test_non_numeric_value(self, tmp_path):
        p = write_csv(tmp_path,
                      "M1,U1,D,2026-01-05 08:00:00,abc,10,10.5,9.5\n"
                      "M1,U2,D,2026-01-05 08:01:00,10.2,10,10.5,9.5\n")
        df, failures, _ = load_measurements(p)
        assert len(df) == 1
        assert failures[0].row_number == 2
        assert "value" in failures[0].reason

    def test_non_numeric_standard(self, tmp_path):
        p = write_csv(tmp_path, "M1,U1,D,2026-01-05 08:00:00,10.2,10,x,9.5\n")
        df, failures, _ = load_measurements(p)
        assert df.empty and "usl" in failures[0].reason

    def test_nan_value_rejected(self, tmp_path):
        p = write_csv(tmp_path, "M1,U1,D,2026-01-05 08:00:00,NaN,10,10.5,9.5\n")
        df, failures, _ = load_measurements(p)
        assert df.empty and len(failures) == 1

    def test_invalid_datetime(self, tmp_path):
        p = write_csv(tmp_path, "M1,U1,D,not-a-date,10.2,10,10.5,9.5\n")
        df, failures, _ = load_measurements(p)
        assert df.empty and "measured_at" in failures[0].reason

    def test_usl_not_above_ref_caught_not_crash(self, tmp_path):
        p = write_csv(tmp_path,
                      "M1,U1,D,2026-01-05 08:00:00,10.2,10,10,9.5\n"      # usl == nominal
                      "M1,U2,D,2026-01-05 08:01:00,10.2,10,10.5,9.5\n")
        df, failures, _ = load_measurements(p)
        assert list(df["unit_id"]) == ["U2"]
        assert len(failures) == 1
        assert failures[0].row_number == 2
        assert "usl" in failures[0].reason

    def test_empty_standard_is_no_standard_not_failure(self, tmp_path):
        p = write_csv(tmp_path, "M1,U1,D,2026-01-05 08:00:00,10.2,,,\n")
        df, failures, _ = load_measurements(p)
        assert failures == []
        assert df.iloc[0]["zone"] == "NO_STANDARD"

    def test_missing_required_column_in_file(self, tmp_path):
        p = write_csv(tmp_path, "M1,U1,D,2026-01-05 08:00:00,10.2,10,10.5\n",
                      header="Machine,Unit,Item,Measured At,Nominal,USL,LSL\n")
        with pytest.raises(MissingColumnsError, match="Value"):
            load_measurements(p)

    def test_unsupported_extension(self, tmp_path):
        p = tmp_path / "x.txt"
        p.write_text("a")
        with pytest.raises(ValueError):
            load_measurements(p)
