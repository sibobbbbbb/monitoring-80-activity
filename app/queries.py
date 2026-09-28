"""Query dashboard ke Postgres: plain SQL dengan parameter terikat.

Nilai dari filter tidak pernah disisipkan ke string SQL (tanpa f-string/format).
Potongan SQL yang digabung di bawah adalah konstanta statis. Filter multi-nilai memakai
`= ANY(CAST(:param AS text[]))`; array kosong berarti "semua".

Item cek = kombinasi (part, operation, characteristics); satu item cek bisa dikerjakan beberapa mesin.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

ItemKey = tuple[str, str, str]  # (part, operation, characteristics)


@dataclass(frozen=True)
class Filters:
    start: datetime                                   # UTC, inklusif
    end: datetime                                     # UTC, eksklusif
    jenis: tuple[tuple[str, str], ...] = ()           # jenis item cek = (part, operation)
    characteristics: tuple[str, ...] = ()
    machines: tuple[str, ...] = ()


_SCOPE = """
    measured_at >= :start AND measured_at < :end
    AND (cardinality(CAST(:jenis_part AS text[])) = 0 OR (part, operation) IN (
            SELECT * FROM unnest(CAST(:jenis_part AS text[]), CAST(:jenis_op AS text[]))))
    AND (cardinality(CAST(:chars AS text[])) = 0 OR characteristics = ANY(CAST(:chars AS text[])))
    AND (cardinality(CAST(:machines AS text[])) = 0 OR machine = ANY(CAST(:machines AS text[])))
"""

# Satu baris per item cek: rasio terburuk di antara pengukuran TERAKHIR tiap mesin, dan rasio tertinggi
# di seluruh rentang. NO_STANDARD dikecualikan.
_ITEM_SUMMARY_SQL = """
    WITH scoped AS (
        SELECT part, operation, machine, characteristics, measured_at, ratio
        FROM measurements
        WHERE""" + _SCOPE + """ AND zone <> 'NO_STANDARD'
    ),
    latest AS (
        SELECT DISTINCT ON (part, operation, characteristics, machine)
               part, operation, characteristics, machine, measured_at, ratio
        FROM scoped
        ORDER BY part, operation, characteristics, machine, measured_at DESC
    ),
    peak AS (
        SELECT part, operation, characteristics, MAX(ratio) AS max_ratio
        FROM scoped
        GROUP BY part, operation, characteristics
    )
    SELECT l.part, l.operation, l.characteristics,
           MAX(l.ratio) AS latest_ratio,
           MAX(p.max_ratio) AS max_ratio,
           MAX(l.measured_at) AS last_at,
           COUNT(*) AS machine_count
    FROM latest l
    JOIN peak p USING (part, operation, characteristics)
    GROUP BY l.part, l.operation, l.characteristics
"""

_NO_STANDARD_ITEMS_SQL = """
    SELECT COUNT(*) FROM (
        SELECT DISTINCT part, operation, characteristics
        FROM measurements
        WHERE""" + _SCOPE + """ AND zone = 'NO_STANDARD'
    ) t
"""

_SERIES_SQL = """
    SELECT part, operation, machine, characteristics, measured_at, value, nominal, usl, lsl, ratio, zone
    FROM measurements
    JOIN unnest(CAST(:k_part AS text[]), CAST(:k_op AS text[]), CAST(:k_char AS text[])) AS k(kp, ko, kc)
      ON part = kp AND operation = ko AND characteristics = kc
    WHERE""" + _SCOPE + """ AND zone <> 'NO_STANDARD'
    ORDER BY part, operation, characteristics, machine, measured_at
"""


def _scope_params(f: Filters) -> dict:
    return {
        "start": f.start,
        "end": f.end,
        "jenis_part": [p for p, _ in f.jenis],
        "jenis_op": [o for _, o in f.jenis],
        "chars": list(f.characteristics),
        "machines": list(f.machines),
    }


def last_ingested_at(engine: Engine) -> Optional[datetime]:
    with engine.connect() as conn:
        return conn.execute(text("SELECT MAX(ingested_at) FROM measurements")).scalar_one()


def time_bounds(engine: Engine) -> tuple[Optional[datetime], Optional[datetime]]:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT MIN(measured_at), MAX(measured_at) FROM measurements")).one()
    return row[0], row[1]


def filter_options(engine: Engine) -> pd.DataFrame:
    """Kombinasi (part, operation, machine, characteristics) yang punya standar, untuk pilihan filter."""
    sql = text("""
        SELECT DISTINCT part, operation, machine, characteristics
        FROM measurements
        WHERE zone <> 'NO_STANDARD'
        ORDER BY part, operation, characteristics, machine
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


def item_summary(engine: Engine, f: Filters) -> pd.DataFrame:
    """Ringkasan per item cek: latest_ratio, max_ratio, last_at, machine_count (Decimal tetap Decimal)."""
    with engine.connect() as conn:
        return pd.read_sql(text(_ITEM_SUMMARY_SQL), conn, params=_scope_params(f), coerce_float=False)


def no_standard_item_count(engine: Engine, f: Filters) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(_NO_STANDARD_ITEMS_SQL), _scope_params(f)).scalar_one())


def series_rows(engine: Engine, f: Filters, items: list[ItemKey]) -> pd.DataFrame:
    """Deret waktu semua mesin untuk item cek yang diminta (satu halaman grid), urut waktu."""
    if not items:
        return pd.DataFrame(columns=["part", "operation", "machine", "characteristics", "measured_at",
                                     "value", "nominal", "usl", "lsl", "ratio", "zone"])
    params = {**_scope_params(f),
              "k_part": [k[0] for k in items], "k_op": [k[1] for k in items], "k_char": [k[2] for k in items]}
    with engine.connect() as conn:
        # coerce_float=False: NUMERIC tetap Decimal (default pandas mengubahnya ke float64)
        return pd.read_sql(text(_SERIES_SQL), conn, params=params, coerce_float=False)
