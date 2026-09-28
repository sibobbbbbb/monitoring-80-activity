"""Generator data dummy mengikuti struktur real FEXQMS; dimasukkan lewat db/ingestion.py.

Hierarki: Part + Operation ("Jenis Item Cek") -> Characteristics ("Item Cek") -> Machine -> pengukuran.
Semua data sintetis (Part berawalan "Demo", source_batch "dummy-generator") sehingga mudah dibersihkan
dengan --reset. Hasil deterministik untuk seed dan --end yang sama.

Pemakaian:
    python -m scripts.generate_dummy_data [--weeks 4] [--per-day 3] [--seed 42] [--end 2026-09-28] [--reset]
"""
import argparse
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

import pandas as pd

from core.rules import compute_ratio

DUMMY_BATCH = "dummy-generator"
D = Decimal


@dataclass(frozen=True)
class MachineSeries:
    """Satu garis pada chart. mu = rata-rata deviasi ternormalisasi bertanda (+1 = USL, -1 = LSL),
    bergerak linear dari mu_start ke mu_end sepanjang rentang waktu; sigma = sebaran acak."""
    machine: str
    mu_start: float
    mu_end: float
    sigma: float


@dataclass(frozen=True)
class ItemSpec:
    part: str
    operation: str
    characteristics: str
    usl: Optional[Decimal]
    lsl: Optional[Decimal]
    nominal: Optional[Decimal]
    machines: tuple[MachineSeries, ...]
    decimals: int = 3
    info_base: Optional[float] = None   # hanya item tanpa standar: nilai dasar


def _m(machine, mu_start, mu_end=None, sigma=0.12) -> MachineSeries:
    return MachineSeries(machine, mu_start, mu_start if mu_end is None else mu_end, sigma)


CH, CS, CR = ("Demo Cyl.Head", "Op 140-180"), ("Demo Crankshaft", "Op 20 Grinding"), ("Demo Conrod", "Op 30 Honing")

CATALOG: tuple[ItemSpec, ...] = (
    # ---- Demo Cyl.Head / Op 140-180 : satu sisi tanpa nominal (LSL=0, ref=0) dan dua sisi ----
    ItemSpec(*CH, "True Pos Hole 1", D("0.2"), D("0"), None,
             (_m("CH-A", 0.35, 1.15, 0.07), _m("CH-B", 0.85, sigma=0.07), _m("CH-C", 0.25, sigma=0.10))),
    ItemSpec(*CH, "True Pos Hole 2", D("0.2"), D("0"), None,
             (_m("CH-A", 0.30, sigma=0.12), _m("CH-B", 0.50, 0.95, 0.10))),
    ItemSpec(*CH, "Flatness Face", D("0.05"), D("0"), None,
             (_m("CH-A", 0.60, sigma=0.15),)),
    ItemSpec(*CH, "Bore Dia", D("30.025"), D("29.975"), D("30.000"),
             (_m("CH-A", 0.0, sigma=0.35), _m("CH-B", -0.30, -1.10, 0.08)), decimals=4),
    # ---- Demo Crankshaft / Op 20 Grinding : dua sisi simetris, asimetris, satu sisi dengan nominal ----
    ItemSpec(*CS, "Journal Dia 1", D("52.010"), D("51.990"), D("52.000"),
             (_m("GR-1", 0.10, sigma=0.30), _m("GR-2", 0.70, sigma=0.15)), decimals=4),
    ItemSpec(*CS, "Journal Dia 2", D("52.010"), D("51.995"), D("52.000"),
             (_m("GR-1", -0.60, sigma=0.25),), decimals=4),
    ItemSpec(*CS, "Runout", D("0.030"), None, D("0.010"),
             (_m("GR-1", 0.30, sigma=0.30), _m("GR-2", 0.50, 0.75, 0.10), _m("GR-3", 0.20, sigma=0.10)),
             decimals=4),
    ItemSpec(*CS, "Coolant Temp (info)", None, None, None,
             (_m("GR-1", 0.0), _m("GR-2", 0.0)), info_base=24.0),
    # ---- Demo Conrod / Op 30 Honing : dua sisi, satu sisi bawah dengan nominal, satu sisi tanpa nominal ----
    ItemSpec(*CR, "Big End Bore", D("55.015"), D("54.985"), D("55.000"),
             (_m("HN-1", 0.0, sigma=0.40), _m("HN-2", 0.40, 0.90, 0.10)), decimals=4),
    ItemSpec(*CR, "Small End Bore", None, D("19.990"), D("20.000"),
             (_m("HN-1", -0.50, -0.98, 0.06),), decimals=4),
    ItemSpec(*CR, "Surface Roughness", D("1.6"), D("0"), None,
             (_m("HN-1", 0.40, sigma=0.15), _m("HN-2", 0.80, sigma=0.10))),
    ItemSpec(*CR, "Weight (info)", None, None, None,
             (_m("HN-1", 0.0),), info_base=350.0),
)


def _measure(spec: ItemSpec, s: float, rng: random.Random) -> Decimal:
    """Nilai XChart dari deviasi ternormalisasi bertanda s (+1 = USL, -1 = LSL)."""
    quantum = D(1).scaleb(-spec.decimals)
    if spec.usl is None and spec.lsl is None:               # tanpa standar: nilai informasi saja
        return (D(str(spec.info_base)) + D(str(round(rng.gauss(0, 2.0), 3)))).quantize(quantum, ROUND_HALF_UP)

    ref = spec.nominal if spec.nominal is not None else D(0)
    up = spec.usl - ref if spec.usl is not None else None
    down = ref - spec.lsl if spec.lsl is not None else None
    span_up = up if up and up > 0 else (down if down and down > 0 else D(1))
    span_down = down if down and down > 0 else span_up
    if spec.lsl is not None and spec.lsl >= ref:            # LSL = ref (mis. 0): nilai tidak bisa di bawah ref
        s = abs(s)
    span = span_up if s >= 0 else span_down
    return (ref + D(str(round(s, 6))) * span).quantize(quantum, ROUND_HALF_UP)


def _series_rows(spec: ItemSpec, ms: MachineSeries, end: datetime, weeks: int, per_day: int, seed: int) -> list[dict]:
    rng = random.Random(f"{seed}|{spec.part}|{spec.operation}|{spec.characteristics}|{ms.machine}")
    n = weeks * 7 * per_day
    step = timedelta(days=1) / per_day
    start = end - timedelta(weeks=weeks)
    rows = []
    for i in range(n):
        if rng.random() < 0.08:                              # sesekali tidak ada sampel
            continue
        when = start + step * (i + 1) + timedelta(minutes=rng.uniform(-60, 60))
        progress = i / max(n - 1, 1)
        s = ms.mu_start + (ms.mu_end - ms.mu_start) * progress + rng.gauss(0, ms.sigma)
        value = _measure(spec, s, rng)
        result = compute_ratio(value, spec.nominal, spec.usl, spec.lsl)
        rows.append({
            "part": spec.part, "operation": spec.operation, "machine": ms.machine,
            "characteristics": spec.characteristics,
            "measured_at": min(pd.Timestamp(when).tz_convert("UTC").floor("s"), pd.Timestamp(end)),
            "value": value, "nominal": spec.nominal, "usl": spec.usl, "lsl": spec.lsl,
            "ratio": result.ratio, "zone": result.zone.value, "source_batch": DUMMY_BATCH,
        })
    return rows


def build_dummy_frame(end: datetime, weeks: int = 4, per_day: int = 3, seed: int = 42) -> pd.DataFrame:
    """DataFrame siap upsert (kolom sama dengan output adapters.file_adapter)."""
    end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
    rows = [r for spec in CATALOG for ms in spec.machines
            for r in _series_rows(spec, ms, end, weeks, per_day, seed)]
    df = pd.DataFrame(rows)
    for c in ("value", "nominal", "usl", "lsl", "ratio"):
        df[c] = df[c].astype(object)
    return df.sort_values("measured_at", kind="stable").reset_index(drop=True)


def main(argv=None) -> int:
    from sqlalchemy import delete

    from db.connection import get_engine
    from db.ingestion import upsert_measurements
    from db.tables import measurements

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--weeks", type=int, default=4)
    p.add_argument("--per-day", type=int, default=3, help="sampel per hari per mesin")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--end", help="akhir rentang (ISO, UTC); default: jam penuh terakhir")
    p.add_argument("--reset", action="store_true", help="hapus dulu data dummy sebelumnya (source_batch dummy-generator)")
    args = p.parse_args(argv)

    end = (datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc) if args.end
           else datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0))
    df = build_dummy_frame(end, args.weeks, args.per_day, args.seed)

    engine = get_engine()
    if args.reset:
        with engine.begin() as conn:
            removed = conn.execute(delete(measurements).where(measurements.c.source_batch == DUMMY_BATCH)).rowcount
        print(f"Reset: {removed} baris dummy lama dihapus")

    res = upsert_measurements(engine, df)
    items = df[["part", "operation", "characteristics"]].drop_duplicates()
    series = df[["part", "operation", "characteristics", "machine"]].drop_duplicates()
    print(f"Dummy: {len(items)} item cek, {len(series)} garis mesin, {len(df)} baris, "
          f"{args.weeks} minggu sampai {end:%Y-%m-%d %H:%M} UTC")
    print(f"Zona: {df['zone'].value_counts().to_dict()}")
    print(f"Upsert selesai: {res.inserted} baru, {res.updated} ter-update, {res.unchanged} tidak berubah")
    return 0


if __name__ == "__main__":
    sys.exit(main())
