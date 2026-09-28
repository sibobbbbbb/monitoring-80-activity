"""Adapter file (CSV/XLSX) -> skema internal measurements.

Validasi per baris; baris gagal dilaporkan (nomor baris + alasan) dan tidak membatalkan baris lain.
"""
from dataclasses import dataclass
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, NamedTuple, Optional, Union

import pandas as pd

from adapters.column_mapping import (
    DEFAULT_COLUMN_MAP,
    DEFAULT_SOURCE_UTC_OFFSET_HOURS,
    REQUIRED_FIELDS,
    STANDARD_FIELDS,
)
from core.rules import compute_ratio

OUTPUT_COLUMNS = [
    "machine_id", "unit_id", "item_ukur", "measured_at", "value",
    "nominal", "usl", "lsl", "ratio", "zone", "source_batch",
]


class MissingColumnsError(ValueError):
    """Kolom wajib tidak ada di file sama sekali (seluruh file tidak bisa diproses)."""


@dataclass(frozen=True)
class RowFailure:
    row_number: int  # nomor baris di file (header = baris 1, data mulai baris 2)
    reason: str


@dataclass(frozen=True)
class RowWarning:
    """Baris valid tapi tidak dipakai (mis. ditimpa baris lain dengan kunci sama)."""
    row_number: int
    reason: str


class ImportResult(NamedTuple):
    data: pd.DataFrame
    failures: list[RowFailure]
    warnings: list[RowWarning]


def read_file(path: Union[str, Path]) -> pd.DataFrame:
    """Baca CSV/XLSX sebagai string mentah agar angka tidak lewat float."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    if suffix in (".xlsx", ".xlsm"):
        return pd.read_excel(path, dtype=str, keep_default_na=False)
    raise ValueError(f"Format file tidak didukung: {suffix}")


def _clean(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_decimal(raw: Optional[str], label: str) -> Optional[Decimal]:
    if raw is None:
        return None
    try:
        d = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        raise ValueError(f"{label} bukan angka: {raw!r}")
    if not d.is_finite():
        raise ValueError(f"{label} bukan angka hingga: {raw!r}")
    return d


def _to_utc(raw: str, source_tz: timezone) -> pd.Timestamp:
    try:
        ts = pd.Timestamp(raw)
    except (ValueError, TypeError):
        raise ValueError(f"measured_at bukan tanggal/waktu valid: {raw!r}")
    if pd.isna(ts):
        raise ValueError(f"measured_at bukan tanggal/waktu valid: {raw!r}")
    ts = ts.tz_localize(source_tz) if ts.tzinfo is None else ts
    return ts.tz_convert("UTC")


def parse_frame(
    raw: pd.DataFrame,
    column_map: Mapping[str, str] = DEFAULT_COLUMN_MAP,
    source_batch: Optional[str] = None,
    source_utc_offset_hours: int = DEFAULT_SOURCE_UTC_OFFSET_HOURS,
) -> ImportResult:
    missing = [column_map[f] for f in REQUIRED_FIELDS if column_map[f] not in raw.columns]
    if missing:
        raise MissingColumnsError(f"Kolom wajib tidak ada di file: {', '.join(missing)}")

    source_tz = timezone(timedelta(hours=source_utc_offset_hours))
    # Kolom standar opsional di file: bila kolomnya tidak ada, dianggap kosong.
    col = {f: column_map[f] for f in (*REQUIRED_FIELDS, *STANDARD_FIELDS) if column_map[f] in raw.columns}

    # Kunci unik -> baris yang bertahan; baris terakhir menang, yang ditimpa dilaporkan.
    survivors: dict[tuple, tuple[int, dict]] = {}
    overwritten: dict[tuple, list[int]] = {}
    failures: list[RowFailure] = []

    for idx, rec in enumerate(raw.to_dict("records")):
        row_number = idx + 2
        try:
            cells = {f: _clean(rec.get(c)) for f, c in col.items()}

            empty_required = [column_map[f] for f in REQUIRED_FIELDS if cells.get(f) is None]
            if empty_required:
                raise ValueError(f"Kolom wajib kosong: {', '.join(empty_required)}")

            value = _to_decimal(cells["value"], "value")
            nominal = _to_decimal(cells.get("nominal"), "nominal")
            usl = _to_decimal(cells.get("usl"), "usl")
            lsl = _to_decimal(cells.get("lsl"), "lsl")
            measured_at = _to_utc(cells["measured_at"], source_tz)

            # Standar kosong (usl dan lsl tidak ada) -> NO_STANDARD, ratio None.
            # ValueError dari spesifikasi tidak valid (usl <= ref dst.) ditangkap di bawah.
            result = compute_ratio(value, nominal, usl, lsl)

            record = {
                "machine_id": cells["machine_id"],
                "unit_id": cells["unit_id"],
                "item_ukur": cells["item_ukur"],
                "measured_at": measured_at,
                "value": value,
                "nominal": nominal,
                "usl": usl,
                "lsl": lsl,
                "ratio": result.ratio,
                "zone": result.zone.value,
                "source_batch": source_batch,
            }
            key = (record["machine_id"], record["unit_id"], record["item_ukur"], measured_at)
            if key in survivors:
                overwritten.setdefault(key, []).append(survivors[key][0])
            survivors[key] = (row_number, record)
        except ValueError as exc:
            failures.append(RowFailure(row_number, str(exc)))

    warnings: list[RowWarning] = []
    for key, earlier_rows in overwritten.items():
        final_row = survivors[key][0]
        label = f"machine_id={key[0]}, unit_id={key[1]}, item_ukur={key[2]}, measured_at={key[3]}"
        for r in earlier_rows:
            warnings.append(RowWarning(
                r, f"Baris ditimpa oleh baris {final_row} (kunci sama: {label})"
            ))
    warnings.sort(key=lambda w: w.row_number)

    rows = [rec for _, rec in sorted(survivors.values(), key=lambda t: t[0])]
    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    for c in ("value", "nominal", "usl", "lsl", "ratio"):
        df[c] = df[c].astype(object)
    return ImportResult(df, failures, warnings)


def load_measurements(
    path: Union[str, Path],
    column_map: Mapping[str, str] = DEFAULT_COLUMN_MAP,
    source_batch: Optional[str] = None,
    source_utc_offset_hours: int = DEFAULT_SOURCE_UTC_OFFSET_HOURS,
) -> ImportResult:
    """Baca file, validasi, hitung ratio/zone.

    Return ImportResult(data, failures, warnings): baris sukses, baris gagal (tidak diimpor),
    dan warning (mis. baris duplikat yang ditimpa).
    """
    path = Path(path)
    return parse_frame(
        read_file(path),
        column_map=column_map,
        source_batch=source_batch or path.name,
        source_utc_offset_hours=source_utc_offset_hours,
    )
