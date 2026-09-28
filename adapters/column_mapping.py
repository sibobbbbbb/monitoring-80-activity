"""Konfigurasi mapping export FEXQMS (Control Chart) -> field internal.

Layout export (Sheet2): blok header di atas tabel berisi pasangan label -> nilai
(mis. "Part:" ... "Cyl.Head Continous NR"; "USL:" ... 0.2), lalu tabel data dengan baris judul
kolom ("Sample Date Time", "XChart", ...). USL/LSL ada di header, bukan per baris.

Ganti/buat ExportMapping baru bila format export berubah; kode adapter tidak perlu diubah.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExportMapping:
    # field internal -> label di blok header. Nilai = sel non-kosong pertama di kanan label.
    header_labels: dict[str, str] = field(default_factory=lambda: {
        "part": "Part:",
        "operation": "Operation:",
        "machine": "Machine:",
        "characteristics": "Characteristics:",
        "usl": "USL:",
        "lsl": "LSL:",
        "nominal": "Nominal:",   # tidak ada di export FEXQMS saat ini; dipakai bila muncul
    })
    # field internal -> judul kolom di tabel data
    table_columns: dict[str, str] = field(default_factory=lambda: {
        "measured_at": "Sample Date Time",
        "value": "XChart",       # nilai yang dibandingkan ke USL/LSL di FEXQMS (BUKAN "Mea. Data : 1")
        "part": "Part",
        "machine": "Machine",
        "characteristics": "Characteristics",
    })
    # wajib ada di header (identitas item cek)
    required_header: tuple[str, ...] = ("part", "operation", "machine", "characteristics")
    # wajib ada sebagai kolom tabel
    required_table: tuple[str, ...] = ("measured_at", "value")
    # kolom tabel opsional; bila ada dan terisi, nilainya mengalahkan header untuk baris itu
    per_row_identity: tuple[str, ...] = ("part", "machine", "characteristics")


DEFAULT_MAPPING = ExportMapping()

# Kunci unik pengukuran (sejajar dengan db.tables.MEASUREMENT_KEY)
KEY_FIELDS = ("part", "operation", "machine", "characteristics", "measured_at")

# Waktu di file export diasumsikan WIB (UTC+7) bila tanpa zona; disimpan sebagai UTC.
DEFAULT_SOURCE_UTC_OFFSET_HOURS = 7
