"""Adapter file export FEXQMS (CSV/XLSX) -> skema internal measurements.

Satu file = satu item cek pada satu mesin (batasan export FEXQMS). Identitas (Part, Operation,
Machine, Characteristics) dan USL/LSL dibaca dari blok header; setiap baris tabel menyumbang
Sample Date Time dan XChart. Baris yang gagal dilaporkan (nomor baris di file + alasan) tanpa
menghentikan baris lain.
"""
import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple, Optional, Union

import openpyxl
import pandas as pd

from adapters.column_mapping import (
    DEFAULT_MAPPING,
    DEFAULT_SOURCE_UTC_OFFSET_HOURS,
    KEY_FIELDS,
    ExportMapping,
)
from core.rules import compute_ratio

OUTPUT_COLUMNS = [
    "part", "operation", "machine", "characteristics", "measured_at", "value",
    "nominal", "usl", "lsl", "ratio", "zone", "source_batch",
]

Grid = list[list[object]]


class InvalidFileError(ValueError):
    """File tidak bisa diproses sama sekali (struktur tidak dikenali / header tidak valid)."""


class MissingColumnsError(InvalidFileError):
    """Kolom atau label wajib tidak ada di file."""


@dataclass(frozen=True)
class RowFailure:
    row_number: int  # nomor baris di file (sama dengan nomor baris di Excel, mulai 1)
    reason: str


@dataclass(frozen=True)
class RowWarning:
    """Baris valid tapi tidak dipakai (mis. ditimpa baris lain dengan kunci sama)."""
    row_number: int
    reason: str
    overwritten_by: Optional[int] = None  # nomor baris yang menimpa (duplikat kunci)


class ImportResult(NamedTuple):
    data: pd.DataFrame
    failures: list[RowFailure]
    warnings: list[RowWarning]


# ---- membaca file menjadi grid sel --------------------------------------------------------------

def _grids_from_csv(raw: bytes) -> list[Grid]:
    text = raw.decode("utf-8-sig")  # BOM dari Excel merusak sel pertama bila tidak dibuang
    delimiter = max((",", ";", "\t"), key=lambda d: text[:4096].count(d))
    return [list(csv.reader(io.StringIO(text), delimiter=delimiter))]


def _grids_from_xlsx(source) -> list[Grid]:
    wb = openpyxl.load_workbook(source, data_only=True)
    return [[list(row) for row in ws.iter_rows(values_only=True)] for ws in wb.worksheets]


def _read_grids(raw: bytes, filename: str) -> list[Grid]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return _grids_from_csv(raw)
    if suffix in (".xlsx", ".xlsm"):
        return _grids_from_xlsx(io.BytesIO(raw))
    raise InvalidFileError(f"Format file tidak didukung: {suffix}")


# ---- utilitas sel ---------------------------------------------------------------------------------

def _text(v) -> Optional[str]:
    """Teks sel dirapikan; None untuk sel kosong. Angka 0 BUKAN kosong (mis. LSL=0)."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_decimal(v, label: str) -> Optional[Decimal]:
    s = _text(v)
    if s is None:
        return None
    try:
        d = Decimal(s.replace(",", "."))
    except InvalidOperation:
        raise ValueError(f"{label} bukan angka: {s!r}")
    if not d.is_finite():
        raise ValueError(f"{label} bukan angka hingga: {s!r}")
    return d


def _to_utc(v, source_tz: timezone) -> pd.Timestamp:
    if isinstance(v, datetime):
        ts = pd.Timestamp(v)
    else:
        s = _text(v)
        try:
            ts = pd.Timestamp(s)
        except (ValueError, TypeError):
            raise ValueError(f"Sample Date Time bukan tanggal/waktu valid: {s!r}")
    if pd.isna(ts):
        raise ValueError(f"Sample Date Time bukan tanggal/waktu valid: {v!r}")
    ts = ts.tz_localize(source_tz) if ts.tzinfo is None else ts
    return ts.tz_convert("UTC")


# ---- struktur file --------------------------------------------------------------------------------

def _find_table(grids: list[Grid], mapping: ExportMapping) -> tuple[Grid, int, dict[str, int]]:
    """Cari sheet dan baris judul tabel yang memuat semua kolom wajib."""
    need = {f: mapping.table_columns[f] for f in mapping.required_table}
    seen: set[str] = set()
    for grid in grids:
        for i, row in enumerate(grid):
            titles = {_text(c): j for j, c in reversed(list(enumerate(row))) if _text(c)}
            seen.update(titles)
            if all(name in titles for name in need.values()):
                cols = {f: titles[name] for f, name in mapping.table_columns.items() if name in titles}
                return grid, i, cols
    missing = [name for name in need.values() if name not in seen]
    raise MissingColumnsError(f"Kolom wajib tidak ada di file: {', '.join(missing or need.values())}")


def _read_header(grid: Grid, header_end: int, mapping: ExportMapping) -> dict[str, object]:
    """Ambil nilai untuk tiap label di blok header (baris sebelum judul tabel)."""
    wanted = {label.strip().lower(): f for f, label in mapping.header_labels.items()}
    found: dict[str, object] = {}
    for row in grid[:header_end]:
        for j, cell in enumerate(row):
            field_name = wanted.get((_text(cell) or "").lower())
            if field_name is None or field_name in found:
                continue
            value = None
            for nxt in row[j + 1:]:
                t = _text(nxt)
                if t is None:
                    continue
                if t.endswith(":"):      # ketemu label berikutnya: nilai untuk label ini kosong
                    break
                value = nxt
                break
            found[field_name] = value
    return found


# ---- parsing --------------------------------------------------------------------------------------

def parse_grids(
    grids: list[Grid],
    mapping: ExportMapping = DEFAULT_MAPPING,
    source_batch: Optional[str] = None,
    source_utc_offset_hours: int = DEFAULT_SOURCE_UTC_OFFSET_HOURS,
) -> ImportResult:
    grid, header_row, cols = _find_table(grids, mapping)
    header = _read_header(grid, header_row, mapping)

    missing = [mapping.header_labels[f] for f in mapping.required_header if _text(header.get(f)) is None]
    if missing:
        raise MissingColumnsError(f"Header file tidak lengkap, nilai kosong atau label tidak ada: {', '.join(missing)}")

    try:
        usl = _to_decimal(header.get("usl"), "USL di header")
        lsl = _to_decimal(header.get("lsl"), "LSL di header")
        nominal = _to_decimal(header.get("nominal"), "Nominal di header")
    except ValueError as exc:
        raise InvalidFileError(str(exc))

    source_tz = timezone(timedelta(hours=source_utc_offset_hours))
    identity = {f: _text(header[f]) for f in mapping.required_header}

    # Kunci unik -> baris yang bertahan; baris terakhir menang, yang ditimpa dilaporkan.
    survivors: dict[tuple, tuple[int, dict]] = {}
    overwritten: dict[tuple, list[int]] = {}
    failures: list[RowFailure] = []

    for i in range(header_row + 1, len(grid)):
        row = grid[i]
        row_number = i + 1
        if not any(_text(c) for c in row):
            continue  # baris kosong di bawah/antara tabel
        try:
            def cell(field_name):
                j = cols.get(field_name)
                return row[j] if j is not None and j < len(row) else None

            raw_time, raw_value = cell("measured_at"), cell("value")
            empty = [mapping.table_columns[f] for f, v in (("measured_at", raw_time), ("value", raw_value))
                     if _text(v) is None]
            if empty:
                raise ValueError(f"Kolom wajib kosong: {', '.join(empty)}")

            rec_identity = dict(identity)
            for f in mapping.per_row_identity:  # nilai per baris (bila terisi) mengalahkan header
                override = _text(cell(f))
                if override is not None:
                    rec_identity[f] = override

            value = _to_decimal(raw_value, f"value ({mapping.table_columns['value']})")
            measured_at = _to_utc(raw_time, source_tz)

            # Standar kosong (usl dan lsl tidak ada) -> NO_STANDARD, ratio None.
            # ValueError dari spesifikasi tidak valid (usl <= ref dst.) ditangkap di bawah.
            result = compute_ratio(value, nominal, usl, lsl)

            record = {
                **rec_identity,
                "measured_at": measured_at,
                "value": value,
                "nominal": nominal,
                "usl": usl,
                "lsl": lsl,
                "ratio": result.ratio,
                "zone": result.zone.value,
                "source_batch": source_batch,
            }
            key = tuple(record[k] for k in KEY_FIELDS)
            if key in survivors:
                overwritten.setdefault(key, []).append(survivors[key][0])
            survivors[key] = (row_number, record)
        except ValueError as exc:
            failures.append(RowFailure(row_number, str(exc)))

    warnings: list[RowWarning] = []
    for key, earlier_rows in overwritten.items():
        final_row = survivors[key][0]
        label = ", ".join(f"{k}={v}" for k, v in zip(KEY_FIELDS, key) if k in ("machine", "characteristics", "measured_at"))
        for r in earlier_rows:
            warnings.append(RowWarning(
                r, f"Baris ditimpa oleh baris {final_row} (kunci sama: {label})",
                overwritten_by=final_row,
            ))
    warnings.sort(key=lambda w: w.row_number)

    rows = [rec for _, rec in sorted(survivors.values(), key=lambda t: t[0])]
    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    for c in ("value", "nominal", "usl", "lsl", "ratio"):
        df[c] = df[c].astype(object)
    return ImportResult(df, failures, warnings)


def load_measurements_from_bytes(
    data: bytes,
    filename: str,
    mapping: ExportMapping = DEFAULT_MAPPING,
    source_batch: Optional[str] = None,
    source_utc_offset_hours: int = DEFAULT_SOURCE_UTC_OFFSET_HOURS,
) -> ImportResult:
    """Baca isi file export di memori (halaman upload), validasi, hitung ratio/zone.

    Return ImportResult(data, failures, warnings): baris sukses, baris gagal (tidak diimpor),
    dan warning (mis. baris duplikat yang ditimpa).
    """
    return parse_grids(
        _read_grids(data, filename),
        mapping=mapping,
        source_batch=source_batch or filename,
        source_utc_offset_hours=source_utc_offset_hours,
    )


def load_measurements(
    path: Union[str, Path],
    mapping: ExportMapping = DEFAULT_MAPPING,
    source_batch: Optional[str] = None,
    source_utc_offset_hours: int = DEFAULT_SOURCE_UTC_OFFSET_HOURS,
) -> ImportResult:
    path = Path(path)
    return load_measurements_from_bytes(
        path.read_bytes(), path.name, mapping=mapping, source_batch=source_batch,
        source_utc_offset_hours=source_utc_offset_hours,
    )
