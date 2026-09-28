"""Rule engine: hitung rasio terhadap toleransi dan tentukan zona.

Modul ini murni (tanpa dependensi DB atau UI) dan memakai Decimal, bukan float.
"""
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

type Number = Decimal | int | float | str

WARNING_THRESHOLD = Decimal("0.8")
NG_THRESHOLD = Decimal("1.0")


class Zone(StrEnum):
    OK = "OK"
    WARNING = "WARNING"
    NG = "NG"
    NO_STANDARD = "NO_STANDARD"


@dataclass(frozen=True)
class RatioResult:
    ratio: Decimal | None  # None jika zone == NO_STANDARD
    zone: Zone


def to_decimal(v: Number) -> Decimal:
    """Konversi ke Decimal lewat str() agar float tidak membawa error biner (0.1 -> 0.1)."""
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def classify(ratio: Decimal) -> Zone:
    if ratio > NG_THRESHOLD:
        return Zone.NG
    if ratio >= WARNING_THRESHOLD:
        return Zone.WARNING
    return Zone.OK


def compute_ratio(
    value: Number,
    nominal: Number | None,
    usl: Number | None,
    lsl: Number | None,
) -> RatioResult:
    """Hitung rasio (x - ref) / (batas - ref) dan zona.

    ref = nominal jika ada, jika tidak 0.
    Tanpa usl dan lsl -> NO_STANDARD (ratio None).
    Sisi relevan tanpa batas -> ratio 0.
    Batas yang berada di sisi salah dari ref (usl <= ref atau lsl >= ref)
    adalah spesifikasi tidak valid -> ValueError.
    """
    if usl is None and lsl is None:
        return RatioResult(None, Zone.NO_STANDARD)

    x = to_decimal(value)
    ref = to_decimal(nominal) if nominal is not None else Decimal(0)

    if x >= ref:
        if usl is None:
            ratio = Decimal(0)
        else:
            upper = to_decimal(usl)
            if upper <= ref:
                raise ValueError(f"usl ({upper}) harus lebih besar dari ref ({ref})")
            ratio = (x - ref) / (upper - ref)
    else:
        if lsl is None:
            ratio = Decimal(0)
        else:
            lower = to_decimal(lsl)
            if lower >= ref:
                raise ValueError(f"lsl ({lower}) harus lebih kecil dari ref ({ref})")
            ratio = (ref - x) / (ref - lower)

    return RatioResult(ratio, classify(ratio))
