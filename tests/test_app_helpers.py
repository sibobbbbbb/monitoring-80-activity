from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd

from app.charts import MACHINE_PALETTE, ZONE_COLORS, item_chart, limits_of, machine_colors
from app.summary import summarize_zones
from app.timeutil import day_range_utc, format_wib
from core.rules import compute_ratio

D = Decimal


class TestSummary:
    def test_counts_and_percents(self):
        s = summarize_zones({"OK": 6, "WARNING": 3, "NG": 1})
        assert s.total == 10
        assert s.percents == {"OK": 60.0, "WARNING": 30.0, "NG": 10.0}

    def test_no_standard_excluded_from_total(self):
        s = summarize_zones({"OK": 1, "NO_STANDARD": 9})
        assert s.total == 1 and s.no_standard == 9 and s.percents["OK"] == 100.0

    def test_empty_no_division_by_zero(self):
        s = summarize_zones({})
        assert s.total == 0 and all(p == 0.0 for p in s.percents.values())


class TestTime:
    def test_day_range_is_wib_midnight_in_utc(self):
        lo, hi = day_range_utc(date(2026, 1, 5), date(2026, 1, 5))
        assert lo == datetime(2026, 1, 4, 17, 0, tzinfo=timezone.utc)
        assert hi == datetime(2026, 1, 5, 17, 0, tzinfo=timezone.utc)

    def test_multi_day_range_inclusive_end(self):
        lo, hi = day_range_utc(date(2026, 1, 5), date(2026, 1, 7))
        assert (hi - lo).days == 3

    def test_format_wib(self):
        assert format_wib(datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc)) == "2026-01-05 08:00:00 WIB"


class TestLimits:
    def test_one_sided_without_nominal_lsl_zero(self):
        lim = limits_of(None, D("0.2"), D("0"))
        assert (lim.usl, lim.lsl, lim.uwl, lim.lwl) == (0.2, 0.0, 0.16, None)   # LSL=ref -> tidak ada batas 80% bawah

    def test_two_sided_with_nominal(self):
        lim = limits_of(D("10"), D("10.5"), D("9.5"))
        assert (lim.uwl, lim.lwl) == (10.4, 9.6)

    def test_asymmetric_uses_each_side_span(self):
        lim = limits_of(D("10"), D("10.2"), D("9.0"))
        assert (round(lim.uwl, 6), round(lim.lwl, 6)) == (10.16, 9.2)

    def test_upper_only_with_nominal(self):
        lim = limits_of(D("5"), D("6"), None)
        assert (lim.usl, lim.lsl, lim.uwl, lim.lwl) == (6.0, None, 5.8, None)

    def test_lower_only_with_nominal(self):
        lim = limits_of(D("10"), None, D("9"))
        assert (lim.usl, lim.uwl, lim.lsl, lim.lwl) == (None, None, 9.0, 9.2)

    def test_no_standard_has_no_lines(self):
        assert limits_of(None, None, None).values() == []

    def test_accepts_none_and_nan_from_dataframes(self):
        assert limits_of(float("nan"), D("0.2"), None).uwl == 0.16

    def test_warning_line_is_exactly_the_80_percent_boundary(self):
        """Garis 80% harus jatuh tepat di nilai yang menghasilkan ratio 0.8 menurut core.rules."""
        lim = limits_of(None, D("0.2"), D("0"))
        assert compute_ratio(D(str(lim.uwl)), None, D("0.2"), D("0")).zone.value == "WARNING"


def rows(machine, values, usl="0.2", lsl="0", nominal=None, start="2026-01-05 01:00"):
    times = pd.date_range(start, periods=len(values), freq="h", tz="UTC")
    ratios = [D(str(v)) / D(usl) for v in values]
    zones = ["OK" if r < D("0.8") else "WARNING" if r <= 1 else "NG" for r in ratios]
    return pd.DataFrame({
        "part": "P", "operation": "O", "machine": machine, "characteristics": "C",
        "measured_at": times, "value": [D(str(v)) for v in values], "nominal": nominal,
        "usl": D(usl), "lsl": D(lsl), "ratio": ratios, "zone": zones,
    })


class TestItemChart:
    colors = machine_colors(["M1", "M2", "M3"])

    def lines(self, fig):
        return [t for t in fig.data if t.mode == "lines+markers"]

    def test_one_line_per_machine_named_after_machine(self):
        df = pd.concat([rows("M1", [0.05, 0.06]), rows("M2", [0.07, 0.08])])
        fig = item_chart(df, self.colors)
        assert [t.name for t in self.lines(fig)] == ["M1", "M2"]
        assert fig.layout.showlegend is True

    def test_single_machine_has_no_legend(self):
        fig = item_chart(rows("M1", [0.05, 0.06]), self.colors)
        assert len(self.lines(fig)) == 1 and fig.layout.showlegend is False

    def test_reference_lines_usl_lsl_and_80_percent_with_labels(self):
        fig = item_chart(rows("M1", [0.05, 0.06]), self.colors)
        ys = sorted(round(s.y0, 6) for s in fig.layout.shapes)
        assert ys == [0.0, 0.16, 0.2]                                            # LSL, batas 80%, USL
        labels = sorted(a.text for a in fig.layout.annotations)
        assert labels == ["80% 0.16", "LSL 0", "USL 0.2"]

    def test_two_sided_has_four_reference_lines(self):
        df = rows("M1", [10.1, 10.2], usl="10.5", lsl="9.5", nominal=D("10"))
        fig = item_chart(df, self.colors)
        assert sorted(round(s.y0, 6) for s in fig.layout.shapes) == [9.5, 9.6, 10.4, 10.5]

    def test_shared_limits_drawn_once_for_multiple_machines(self):
        df = pd.concat([rows("M1", [0.05, 0.06]), rows("M2", [0.07, 0.08])])
        assert len(item_chart(df, self.colors).layout.shapes) == 3

    def test_different_limits_per_machine_draw_per_machine_lines_in_machine_color(self):
        df = pd.concat([rows("M1", [0.05, 0.06], usl="0.2"), rows("M2", [0.07, 0.08], usl="0.3")])
        fig = item_chart(df, self.colors)
        assert len(fig.layout.shapes) == 6 and len(fig.layout.annotations) == 0     # tanpa label, warna mesin
        assert {s.line.color for s in fig.layout.shapes} == {self.colors["M1"], self.colors["M2"]}

    def test_y_range_always_contains_all_limits_and_data(self):
        lo, hi = item_chart(rows("M1", [0.02, 0.03]), self.colors).layout.yaxis.range
        assert lo < 0.0 and hi > 0.2                                              # LSL=0 dan USL=0.2 terlihat

    def test_y_range_grows_to_include_data_beyond_limit(self):
        lo, hi = item_chart(rows("M1", [0.05, 0.31]), self.colors).layout.yaxis.range
        assert hi > 0.31

    def test_time_axis_is_wib(self):
        fig = item_chart(rows("M1", [0.05], start="2026-01-05 01:00"), self.colors)
        assert pd.Timestamp(self.lines(fig)[0].x[0]) == pd.Timestamp("2026-01-05 08:00")

    def test_non_ok_points_get_zone_colored_rings(self):
        fig = item_chart(rows("M1", [0.05, 0.17, 0.25]), self.colors)              # OK, WARNING, NG
        ring = [t for t in fig.data if t.mode == "markers"][0]
        assert list(ring.marker.line.color) == [ZONE_COLORS["WARNING"], ZONE_COLORS["NG"]]

    def test_no_ring_trace_when_all_ok(self):
        fig = item_chart(rows("M1", [0.05, 0.06]), self.colors)
        assert all(t.mode == "lines+markers" for t in fig.data)

    def test_line_uses_consistent_machine_color(self):
        fig = item_chart(rows("M2", [0.05, 0.06]), self.colors)
        assert self.lines(fig)[0].line.color == self.colors["M2"]

    def test_hover_shows_ratio(self):
        fig = item_chart(rows("M1", [0.05]), self.colors)
        assert "Rasio" in self.lines(fig)[0].hovertemplate and "XChart" in self.lines(fig)[0].hovertemplate


class TestMachineColors:
    def test_stable_and_sorted_by_name(self):
        assert machine_colors(["B", "A", "B"]) == machine_colors(["A", "B"])
        assert machine_colors(["A", "B"])["A"] == MACHINE_PALETTE[0]

    def test_wraps_around_palette(self):
        many = [f"M{i:02d}" for i in range(len(MACHINE_PALETTE) + 2)]
        colors = machine_colors(many)
        assert colors[many[len(MACHINE_PALETTE)]] == MACHINE_PALETTE[0]

    def test_never_uses_zone_colors(self):
        assert not set(MACHINE_PALETTE) & set(ZONE_COLORS.values())
