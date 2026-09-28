"""Ringkasan zona untuk kartu dashboard. NO_STANDARD dikecualikan dari total dan persen."""
from collections.abc import Mapping
from dataclasses import dataclass

STANDARD_ZONES = ("OK", "WARNING", "NG")


@dataclass(frozen=True)
class ZoneSummary:
    total: int                     # jumlah pengukuran ber-standar (OK + WARNING + NG)
    counts: dict[str, int]
    percents: dict[str, float]     # 0-100, terhadap total
    no_standard: int


def summarize_zones(counts: Mapping[str, int]) -> ZoneSummary:
    per_zone = {z: int(counts.get(z, 0)) for z in STANDARD_ZONES}
    total = sum(per_zone.values())
    percents = {z: (n * 100.0 / total if total else 0.0) for z, n in per_zone.items()}
    return ZoneSummary(total, per_zone, percents, int(counts.get("NO_STANDARD", 0)))
