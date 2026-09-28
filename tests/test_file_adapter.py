from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from adapters.column_mapping import ExportMapping
from adapters.file_adapter import (
    InvalidFileError, MissingColumnsError, load_measurements, load_measurements_from_bytes,
)
from tests.fexqms_fixture import (
    CHARACTERISTICS, FIRST_DATA_ROW, MACHINE, OPERATION, PART, T0, export_grid, times, to_csv, to_xlsx,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample_fexqms_export.csv"
REAL_EXPORT = ROOT / "data" / "CONTROL_CHART.xlsx"      # export asli, di-ignore git; test dilewati bila tidak ada

# USL 0.2, LSL 0, ref 0 (satu sisi tanpa nominal): ratio = x / 0.2
XCHARTS = [0.05, 0.09, 0.12, 0.16, 0.17, 0.21, 0.03, 0.10]
EXPECTED_ZONES = ["OK", "OK", "OK", "WARNING", "WARNING", "NG", "OK", "OK"]


def samples(values):
    return list(zip(times(len(values)), values))


def load_csv(grid, name="in.csv", **kw):
    return load_measurements_from_bytes(to_csv(grid), name, **kw)


class TestValidFile:
    def test_sample_file_parses_all_rows(self):
        res = load_measurements(SAMPLE)
        assert res.failures == [] and res.warnings == []
        assert len(res.data) == 8
        assert set(res.data["source_batch"]) == {"sample_fexqms_export.csv"}

    def test_identity_and_limits_come_from_header(self):
        d = load_measurements(SAMPLE).data
        assert set(d["part"]) == {PART} and set(d["operation"]) == {OPERATION}
        assert set(d["machine"]) == {MACHINE} and set(d["characteristics"]) == {CHARACTERISTICS}
        assert set(d["usl"]) == {Decimal("0.2")}
        assert set(d["lsl"]) == {Decimal("0")}            # LSL=0 literal, bukan kosong
        assert set(d["nominal"]) == {None}

    def test_value_is_xchart_not_raw_measurement(self):
        d = load_measurements(SAMPLE).data
        assert list(d["value"]) == [Decimal(str(x)) for x in XCHARTS]   # kolom 'Mea. Data : 1' = 2x, tidak dipakai

    def test_ratio_and_zone_from_xchart(self):
        d = load_measurements(SAMPLE).data
        assert list(d["zone"]) == EXPECTED_ZONES
        assert d.iloc[3]["ratio"] == Decimal("0.8")       # tepat batas WARNING
        assert d.iloc[5]["ratio"] == Decimal("1.05")

    def test_time_converted_wib_to_utc(self):
        d = load_measurements(SAMPLE).data
        assert d.iloc[0]["measured_at"] == pd.Timestamp(datetime(2026, 1, 5, 1, 0, 0, tzinfo=timezone.utc))

    def test_time_offset_is_configurable(self):
        res = load_csv(export_grid(samples([0.05])), source_utc_offset_hours=0)
        assert res.data.iloc[0]["measured_at"] == pd.Timestamp(datetime(2026, 1, 5, 8, 0, 0, tzinfo=timezone.utc))

    def test_xlsx_gives_same_result_as_csv(self):
        grid = export_grid(samples(XCHARTS))
        from_xlsx = load_measurements_from_bytes(to_xlsx(grid), "x.xlsx")
        from_csv = load_csv(grid)
        assert from_xlsx.failures == [] and len(from_xlsx.data) == 8
        pd.testing.assert_frame_equal(from_xlsx.data.drop(columns="source_batch"),
                                      from_csv.data.drop(columns="source_batch"))

    def test_data_found_when_it_is_the_only_sheet(self):
        res = load_measurements_from_bytes(to_xlsx(export_grid(samples(XCHARTS)), data_sheet_index=0), "x.xlsx")
        assert len(res.data) == 8

    def test_csv_with_bom_and_semicolon_delimiter(self):
        data = to_csv(export_grid(samples(XCHARTS)), delimiter=";", bom=True)
        res = load_measurements_from_bytes(data, "excel_id.csv")
        assert res.failures == [] and len(res.data) == 8 and set(res.data["part"]) == {PART}

    def test_decimal_comma_accepted(self):
        res = load_csv(export_grid([(T0, "0,16")]))
        assert res.data.iloc[0]["value"] == Decimal("0.16")

    def test_explicit_source_batch(self):
        res = load_csv(export_grid(samples([0.05])), source_batch="batch-1")
        assert set(res.data["source_batch"]) == {"batch-1"}

    def test_blank_rows_inside_and_after_table_are_ignored(self):
        grid = export_grid(samples([0.05, 0.06]))
        grid.insert(len(grid) - 1, [None] * len(grid[0]))
        grid.append([None] * len(grid[0]))
        res = load_csv(grid)
        assert len(res.data) == 2 and res.failures == []


class TestLimitsFromHeader:
    def test_usl_blank_does_not_swallow_lsl_label(self):
        res = load_csv(export_grid(samples([0.05, 0.5]), usl=None, lsl=0))
        d = res.data
        assert set(d["usl"]) == {None} and set(d["lsl"]) == {Decimal("0")}
        assert list(d["ratio"]) == [Decimal("0"), Decimal("0")]      # sisi USL tanpa batas -> ratio 0
        assert set(d["zone"]) == {"OK"}

    def test_both_limits_blank_is_no_standard(self):
        d = load_csv(export_grid(samples([0.05]), usl=None, lsl=None)).data
        assert d.iloc[0]["zone"] == "NO_STANDARD" and d.iloc[0]["ratio"] is None

    def test_two_sided_with_nominal_label(self):
        d = load_csv(export_grid([(T0, 10.4), (T0 + pd.Timedelta(hours=1), 9.6)],
                                 usl=10.5, lsl=9.5, nominal=10)).data
        assert set(d["nominal"]) == {Decimal("10")}
        assert list(d["ratio"]) == [Decimal("0.8"), Decimal("0.8")]
        assert set(d["zone"]) == {"WARNING"}

    def test_upper_only_with_nominal(self):
        d = load_csv(export_grid([(T0, 5.9), (T0 + pd.Timedelta(hours=1), 4.0)], usl=6, lsl=None, nominal=5)).data
        assert list(d["ratio"]) == [Decimal("0.9"), Decimal("0")]

    def test_non_numeric_limit_rejects_file(self):
        with pytest.raises(InvalidFileError, match="USL"):
            load_csv(export_grid(samples([0.05]), usl="abc"))


class TestIdentity:
    def test_per_row_identity_overrides_header(self):
        grid = export_grid(samples([0.05, 0.06]))
        grid[10][5] = "MC-2"                    # baris data ke-2: Machine berbeda dari header
        d = load_csv(grid).data
        assert list(d["machine"]) == [MACHINE, "MC-2"]

    def test_identity_falls_back_to_header_when_row_columns_empty(self):
        d = load_csv(export_grid(samples([0.05]), per_row_identity=False)).data
        assert d.iloc[0]["machine"] == MACHINE and d.iloc[0]["characteristics"] == CHARACTERISTICS

    def test_mapping_is_configuration_not_hardcoded(self):
        raw_mapping = ExportMapping(table_columns={**ExportMapping().table_columns, "value": "Mea. Data : 1"})
        res = load_csv(export_grid(samples([0.05])), mapping=raw_mapping)
        assert res.data.iloc[0]["value"] == Decimal("0.1")     # 2 x 0.05, dari kolom yang dipetakan


class TestDuplicateKeys:
    def grid(self):
        # baris 10 dan 12 kunci sama (waktu sama); baris 11 lain
        return export_grid([(T0, 0.05), (T0 + pd.Timedelta(hours=1), 0.06), (T0, 0.07)])

    def test_duplicate_reported_as_warning_last_row_wins(self):
        res = load_csv(self.grid())
        assert res.failures == []
        assert len(res.data) == 2
        assert list(res.data["value"]) == [Decimal("0.06"), Decimal("0.07")]   # urut baris yang bertahan
        assert len(res.warnings) == 1
        w = res.warnings[0]
        assert (w.row_number, w.overwritten_by) == (FIRST_DATA_ROW, FIRST_DATA_ROW + 2)
        assert f"baris {FIRST_DATA_ROW + 2}" in w.reason and f"machine={MACHINE}" in w.reason

    def test_three_way_duplicate_reports_every_overwritten_row(self):
        res = load_csv(export_grid([(T0, 0.05), (T0, 0.06), (T0, 0.07)]))
        assert len(res.data) == 1 and res.data.iloc[0]["value"] == Decimal("0.07")
        assert [w.row_number for w in res.warnings] == [FIRST_DATA_ROW, FIRST_DATA_ROW + 1]
        assert {w.overwritten_by for w in res.warnings} == {FIRST_DATA_ROW + 2}

    def test_same_instant_in_different_timezone_notation_is_duplicate(self):
        res = load_csv(export_grid([(T0, 0.05), ("2026-01-05 01:00:00+00:00", 0.06)]))
        assert len(res.data) == 1 and len(res.warnings) == 1

    def test_failed_row_does_not_overwrite(self):
        res = load_csv(export_grid([(T0, 0.05), (T0, "abc")]))
        assert len(res.data) == 1 and res.data.iloc[0]["value"] == Decimal("0.05")
        assert len(res.failures) == 1 and res.warnings == []

    def test_no_duplicates_no_warnings(self):
        assert load_measurements(SAMPLE).warnings == []


class TestRowFailures:
    def test_failed_rows_reported_with_excel_row_numbers_others_processed(self):
        grid = export_grid([
            (T0, 0.05),                                        # baris 10 valid
            (T0 + pd.Timedelta(hours=1), None),                # baris 11: XChart kosong
            (T0 + pd.Timedelta(hours=2), "abc"),               # baris 12: bukan angka
            ("bukan-tanggal", 0.05),                           # baris 13: waktu invalid
            (None, 0.05),                                      # baris 14: waktu kosong
            (T0 + pd.Timedelta(hours=5), 0.06),                # baris 15 valid
        ])
        res = load_csv(grid)
        assert list(res.data["value"]) == [Decimal("0.05"), Decimal("0.06")]
        assert [f.row_number for f in res.failures] == [11, 12, 13, 14]
        reasons = [f.reason for f in res.failures]
        assert "XChart" in reasons[0] and "bukan angka" in reasons[1]
        assert "Sample Date Time" in reasons[2] and "Sample Date Time" in reasons[3]

    def test_nan_and_infinity_rejected(self):
        res = load_csv(export_grid([(T0, "NaN"), (T0 + pd.Timedelta(hours=1), "Infinity")]))
        assert res.data.empty and len(res.failures) == 2

    def test_invalid_spec_from_core_caught_per_row_not_crash(self):
        # USL == nominal -> ValueError dari core/rules.py untuk baris di sisi atas nominal
        res = load_csv(export_grid([(T0, 10.2), (T0 + pd.Timedelta(hours=1), 9.8)], usl=10, lsl=9.5, nominal=10))
        assert list(res.data["value"]) == [Decimal("9.8")]
        assert len(res.failures) == 1 and "usl" in res.failures[0].reason


class TestFileLevelErrors:
    def test_missing_xchart_column(self):
        grid = export_grid(samples([0.05]))
        grid[8][16] = "Other"
        with pytest.raises(MissingColumnsError, match="XChart"):
            load_csv(grid)

    def test_missing_required_header_value(self):
        grid = export_grid(samples([0.05]), operation=None)
        with pytest.raises(MissingColumnsError, match="Operation:"):
            load_csv(grid)

    def test_missing_header_block_entirely(self):
        with pytest.raises(MissingColumnsError, match="Part:"):
            load_csv(export_grid(samples([0.05]))[8:])

    def test_unrelated_file(self):
        with pytest.raises(MissingColumnsError):
            load_measurements_from_bytes(b"a,b\n1,2\n", "x.csv")

    def test_unsupported_extension(self):
        with pytest.raises(InvalidFileError, match="tidak didukung"):
            load_measurements_from_bytes(b"a", "x.txt")


@pytest.mark.skipif(not REAL_EXPORT.exists(), reason="export FEXQMS asli tidak ada di data/")
class TestRealExport:
    def test_real_export_parses_with_header_limits_and_xchart_values(self):
        res = load_measurements(REAL_EXPORT)
        d = res.data
        assert res.failures == [] and len(d) > 0
        assert len({(r.part, r.operation, r.machine, r.characteristics) for r in d.itertuples()}) == 1
        assert set(d["usl"]) == {Decimal("0.2")} and set(d["lsl"]) == {Decimal("0")}

    def test_our_warning_rows_equal_fexqms_uwl_violations(self):
        """CLAUDE.md: UWL FEXQMS = logika 80% Activity kita. Bandingkan dengan kolom Violation (XChart)."""
        ws = openpyxl.load_workbook(REAL_EXPORT, data_only=True)["Sheet2"]
        rows = list(ws.iter_rows(values_only=True))
        header = next(r for r in rows if r and "Violation (XChart)" in r)
        flag_col, time_col = header.index("Violation (XChart)"), header.index("Sample Date Time")
        uwl_times = {r[time_col] for r in rows if r[flag_col] == "UWL"}

        d = load_measurements(REAL_EXPORT).data
        ours = {ts.tz_convert("Asia/Jakarta").tz_localize(None).to_pydatetime()
                for ts in d.loc[d["zone"] == "WARNING", "measured_at"]}
        assert ours == uwl_times
