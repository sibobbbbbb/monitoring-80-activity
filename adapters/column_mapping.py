"""Konfigurasi mapping kolom file export -> field internal.

Key = nama field internal, value = nama kolom di file sumber.
Ganti/buat dict baru bila format export FEXQMS berbeda; kode adapter tidak perlu diubah.
"""

REQUIRED_FIELDS = ("machine_id", "unit_id", "item_ukur", "measured_at", "value")
STANDARD_FIELDS = ("nominal", "usl", "lsl")

DEFAULT_COLUMN_MAP = {
    "machine_id": "Machine",
    "unit_id": "Unit",
    "item_ukur": "Item",
    "measured_at": "Measured At",
    "value": "Value",
    "nominal": "Nominal",
    "usl": "USL",
    "lsl": "LSL",
}

# Waktu di file export diasumsikan WIB (UTC+7) bila tanpa zona; disimpan sebagai UTC.
DEFAULT_SOURCE_UTC_OFFSET_HOURS = 7
