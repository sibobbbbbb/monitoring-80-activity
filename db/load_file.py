"""CLI: muat file CSV/XLSX ke database (sementara sampai halaman upload tersedia).

Pemakaian:  python -m db.load_file data/sample_measurements.csv [--batch NAMA]
"""
import argparse
import sys

from adapters.file_adapter import MissingColumnsError, load_measurements
from db.connection import get_engine
from db.ingestion import upsert_measurements


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--batch", help="source_batch (default: nama file)")
    args = parser.parse_args(argv)

    try:
        result = load_measurements(args.path, source_batch=args.batch)
    except MissingColumnsError as exc:
        print(f"GAGAL: {exc}", file=sys.stderr)
        return 2

    print(f"Baris valid: {len(result.data)} | gagal: {len(result.failures)} "
          f"| warning: {len(result.warnings)}")
    for f in result.failures:
        print(f"  GAGAL   baris {f.row_number}: {f.reason}")
    for w in result.warnings:
        print(f"  WARNING baris {w.row_number}: {w.reason}")

    n = upsert_measurements(get_engine(), result.data)
    print(f"Upsert selesai: {n} baris")
    return 0


if __name__ == "__main__":
    sys.exit(main())
