from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd

from app.charts import AXIS_LIMIT, bullet_chart
from app.summary import summarize_zones
from app.timeutil import WIB, day_range_utc, format_wib


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


def _priority(rows):
    df = pd.DataFrame(rows, columns=["machine_id", "unit_id", "item_ukur", "measured_at",
                                     "value", "ratio", "zone", "deviation"])
    df["measured_at"] = pd.to_datetime(df["measured_at"], utc=True)
    return df


class TestBulletChart:
    rows = [
        ("M1", "U1", "Dia", "2026-01-05 01:00", Decimal("10.6"), Decimal("1.2"), "NG", Decimal("1.2")),
        ("M1", "U2", "Dep", "2026-01-05 01:01", Decimal("9.1"), Decimal("0.9"), "WARNING", Decimal("-0.9")),
        ("M2", "U3", "Len", "2026-01-05 01:02", Decimal("50.1"), Decimal("0.2"), "OK", Decimal("0.2")),
    ]

    def test_one_bar_per_row_highest_ratio_on_top(self):
        fig = bullet_chart(_priority(self.rows))
        bar = fig.data[0]
        assert len(bar.y) == 3
        assert "U1" in bar.y[-1]                    # baris pertama (ratio tertinggi) paling atas

    def test_signed_deviation_and_axis_range(self):
        fig = bullet_chart(_priority(self.rows))
        xs = dict(zip([y.split(" · ")[1] for y in fig.data[0].y], fig.data[0].x))
        assert xs["U2"] == -0.9 and xs["U1"] == 1.2
        assert tuple(fig.layout.xaxis.range) == (-AXIS_LIMIT, AXIS_LIMIT)

    def test_extreme_deviation_clipped_but_true_ratio_kept(self):
        rows = [("M1", "U1", "Dia", "2026-01-05 01:00", Decimal("99"), Decimal("9.5"), "NG", Decimal("9.5"))]
        bar = bullet_chart(_priority(rows)).data[0]
        assert bar.x[0] == AXIS_LIMIT
        assert bar.customdata[0][0] == 9.5

    def test_max_rows_limits_bars(self):
        assert len(bullet_chart(_priority(self.rows), max_rows=2).data[0].y) == 2

    def test_label_uses_wib(self):
        y = bullet_chart(_priority(self.rows)).data[0].y
        assert any("05/01 08:00" in label for label in y)   # 01:00 UTC = 08:00 WIB
