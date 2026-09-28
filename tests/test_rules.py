from decimal import Decimal

import pytest

from core.rules import Zone, compute_ratio

D = Decimal


def check(result, ratio, zone):
    assert result.zone == zone
    assert result.ratio == (D(ratio) if ratio is not None else None)


class TestTwoSided:
    # nominal 10, usl 10.5, lsl 9.5 (simetris)
    def test_upper_side(self):
        check(compute_ratio("10.2", "10", "10.5", "9.5"), "0.4", Zone.OK)

    def test_lower_side(self):
        check(compute_ratio("9.7", "10", "10.5", "9.5"), "0.6", Zone.OK)

    def test_at_nominal(self):
        check(compute_ratio("10", "10", "10.5", "9.5"), "0", Zone.OK)

    def test_ng_upper(self):
        check(compute_ratio("10.6", "10", "10.5", "9.5"), "1.2", Zone.NG)

    def test_ng_lower(self):
        check(compute_ratio("9.4", "10", "10.5", "9.5"), "1.2", Zone.NG)


class TestAsymmetric:
    # nominal 10, usl 10.2 (+0.2), lsl 9.0 (-1.0)
    def test_upper_uses_upper_span(self):
        check(compute_ratio("10.18", "10", "10.2", "9.0"), "0.9", Zone.WARNING)

    def test_lower_uses_lower_span(self):
        # deviasi 0.18 sisi bawah -> 0.18 / 1.0, bukan / 0.2
        check(compute_ratio("9.82", "10", "10.2", "9.0"), "0.18", Zone.OK)

    def test_lower_warning(self):
        check(compute_ratio("9.1", "10", "10.2", "9.0"), "0.9", Zone.WARNING)


class TestUpperOnlyWithNominal:
    def test_above_nominal(self):
        check(compute_ratio("5.9", "5", "6", None), "0.9", Zone.WARNING)

    def test_below_nominal_has_no_lower_limit(self):
        check(compute_ratio("4", "5", "6", None), "0", Zone.OK)

    def test_ng(self):
        check(compute_ratio("6.5", "5", "6", None), "1.5", Zone.NG)


class TestUpperOnlyWithoutNominal:
    # ref = 0
    def test_warning(self):
        check(compute_ratio("0.09", None, "0.1", None), "0.9", Zone.WARNING)

    def test_ok(self):
        check(compute_ratio("0.05", None, "0.1", None), "0.5", Zone.OK)

    def test_negative_value_is_zero_ratio(self):
        check(compute_ratio("-0.05", None, "0.1", None), "0", Zone.OK)

    def test_ng(self):
        check(compute_ratio("0.15", None, "0.1", None), "1.5", Zone.NG)


class TestLowerOnly:
    def test_below_nominal(self):
        check(compute_ratio("9.1", "10", None, "9"), "0.9", Zone.WARNING)

    def test_above_nominal_has_no_upper_limit(self):
        check(compute_ratio("12", "10", None, "9"), "0", Zone.OK)

    def test_ng(self):
        check(compute_ratio("8.5", "10", None, "9"), "1.5", Zone.NG)

    def test_without_nominal_ref_is_zero(self):
        # ref = 0, lsl = -1, x = -0.9
        check(compute_ratio("-0.9", None, None, "-1"), "0.9", Zone.WARNING)


class TestNoStandard:
    def test_no_limits(self):
        check(compute_ratio("10", "10", None, None), None, Zone.NO_STANDARD)

    def test_no_limits_no_nominal(self):
        check(compute_ratio("3.3", None, None, None), None, Zone.NO_STANDARD)


class TestBoundaries:
    def test_exactly_0_8_is_warning(self):
        check(compute_ratio("0.8", "0", "1", "-1"), "0.8", Zone.WARNING)

    def test_just_below_0_8_is_ok(self):
        check(compute_ratio("0.799", "0", "1", "-1"), "0.799", Zone.OK)

    def test_exactly_1_0_is_warning(self):
        check(compute_ratio("1", "0", "1", "-1"), "1", Zone.WARNING)

    def test_just_above_1_0_is_ng(self):
        check(compute_ratio("1.001", "0", "1", "-1"), "1.001", Zone.NG)

    def test_exactly_0_8_lower_side(self):
        check(compute_ratio("-0.8", "0", "1", "-1"), "0.8", Zone.WARNING)

    def test_exactly_1_0_lower_side(self):
        check(compute_ratio("-1", "0", "1", "-1"), "1", Zone.WARNING)


class TestFloatingPoint:
    def test_float_input_at_0_8_boundary(self):
        # Dengan float murni: 10.4 - 10.0 = 0.40000000000000036 -> ratio > 0.8 (salah).
        # Lewat Decimal(str(x)) hasilnya tepat 0.8.
        check(compute_ratio(10.4, 10.0, 10.5, 9.5), "0.8", Zone.WARNING)

    def test_float_input_at_1_0_boundary(self):
        check(compute_ratio(10.5, 10.0, 10.5, 9.5), "1", Zone.WARNING)

    def test_classic_0_1_plus_0_2(self):
        # 0.1 + 0.2 = 0.30000000000000004 di float; input sudah dibulatkan oleh sumber data
        check(compute_ratio(0.3, None, 0.375, None), "0.8", Zone.WARNING)

    def test_result_is_decimal(self):
        result = compute_ratio(0.1, None, 1.0, None)
        assert isinstance(result.ratio, Decimal)

    def test_mixed_input_types(self):
        check(compute_ratio(D("9.7"), 10, "10.5", 9.5), "0.6", Zone.OK)


class TestInvalidSpec:
    def test_usl_not_above_ref(self):
        with pytest.raises(ValueError):
            compute_ratio("5", "5", "5", None)

    def test_usl_below_ref(self):
        with pytest.raises(ValueError):
            compute_ratio("6", "5", "4", None)

    def test_lsl_not_below_ref(self):
        with pytest.raises(ValueError):
            compute_ratio("4", "5", None, "5")
