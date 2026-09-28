"""Query dashboard ke Postgres: plain SQL dengan parameter terikat.

Nilai dari filter tidak pernah disisipkan ke string SQL (tanpa f-string/format).
Potongan SQL yang digabung di bawah adalah konstanta statis. Filter multi-nilai memakai
`= ANY(CAST(:param AS text[]))`; array kosong berarti "semua".
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class Filters:
    start: datetime                       # UTC, inklusif
    end: datetime                         # UTC, eksklusif
    machines: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    items: tuple[str, ...] = ()
    zones: tuple[str, ...] = ()           # hanya dipakai priority_rows


_SCOPE = """
    measured_at >= :start AND measured_at < :end
    AND (cardinality(CAST(:machines AS text[])) = 0 OR machine_id = ANY(CAST(:machines AS text[])))
    AND (cardinality(CAST(:units AS text[])) = 0 OR unit_id = ANY(CAST(:units AS text[])))
    AND (cardinality(CAST(:items AS text[])) = 0 OR item_ukur = ANY(CAST(:items AS text[])))
"""

_ZONE_COUNTS_SQL = "SELECT zone, COUNT(*) AS n FROM measurements WHERE" + _SCOPE + "GROUP BY zone"

# deviasi bertanda dari ratio yang sudah tersimpan (core/rules.py tetap satu-satunya
# tempat rumus): + di sisi USL (value >= ref), - di sisi LSL.
_PRIORITY_SQL = """
    SELECT machine_id, unit_id, item_ukur, measured_at, value, nominal, usl, lsl, ratio, zone,
           CASE WHEN value >= COALESCE(nominal, 0) THEN ratio ELSE -ratio END AS deviation
    FROM measurements
    WHERE""" + _SCOPE + """
      AND zone <> 'NO_STANDARD'
      AND (cardinality(CAST(:zones AS text[])) = 0 OR zone = ANY(CAST(:zones AS text[])))
    ORDER BY ratio DESC, measured_at DESC, machine_id, unit_id, item_ukur
    LIMIT :limit
"""


def _scope_params(f: Filters) -> dict:
    return {
        "start": f.start,
        "end": f.end,
        "machines": list(f.machines),
        "units": list(f.units),
        "items": list(f.items),
    }


def last_ingested_at(engine: Engine) -> Optional[datetime]:
    with engine.connect() as conn:
        return conn.execute(text("SELECT MAX(ingested_at) FROM measurements")).scalar_one()


def time_bounds(engine: Engine) -> tuple[Optional[datetime], Optional[datetime]]:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT MIN(measured_at), MAX(measured_at) FROM measurements")).one()
    return row[0], row[1]


def filter_options(engine: Engine) -> pd.DataFrame:
    """Kombinasi (machine_id, unit_id, item_ukur) yang punya standar, untuk pilihan filter."""
    sql = text("""
        SELECT DISTINCT machine_id, unit_id, item_ukur
        FROM measurements
        WHERE zone <> 'NO_STANDARD'
        ORDER BY machine_id, unit_id, item_ukur
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


def zone_counts(engine: Engine, f: Filters) -> dict[str, int]:
    """Jumlah pengukuran per zona (termasuk NO_STANDARD) untuk filter scope; zona diabaikan."""
    with engine.connect() as conn:
        rows = conn.execute(text(_ZONE_COUNTS_SQL), _scope_params(f)).all()
    return {zone: int(n) for zone, n in rows}


def priority_rows(engine: Engine, f: Filters, limit: int = 200) -> pd.DataFrame:
    """Pengukuran ber-standar, ratio tertinggi lebih dulu. zones kosong = semua zona."""
    params = {**_scope_params(f), "zones": list(f.zones), "limit": limit}
    with engine.connect() as conn:
        # coerce_float=False: NUMERIC tetap Decimal (default pandas mengubahnya ke float64)
        return pd.read_sql(text(_PRIORITY_SQL), conn, params=params, coerce_float=False)
