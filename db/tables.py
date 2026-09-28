"""Definisi tabel SQLAlchemy Core. Harus sejajar dengan db/schema.sql."""
from sqlalchemy import (
    BigInteger, CheckConstraint, Column, DateTime, Integer, MetaData, Numeric,
    Table, Text, UniqueConstraint, func,
)

metadata = MetaData()

MEASUREMENT_KEY = ("part", "operation", "machine", "characteristics", "measured_at")


def _id_column() -> Column:
    # BigInteger tidak autoincrement di SQLite; varian Integer dipakai untuk test.
    return Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)


measurements = Table(
    "measurements",
    metadata,
    _id_column(),
    Column("part", Text, nullable=False),
    Column("operation", Text, nullable=False),
    Column("machine", Text, nullable=False),
    Column("characteristics", Text, nullable=False),
    Column("measured_at", DateTime(timezone=True), nullable=False),
    Column("value", Numeric, nullable=False),   # XChart
    Column("nominal", Numeric),
    Column("usl", Numeric),
    Column("lsl", Numeric),
    Column("ratio", Numeric),
    Column("zone", Text, nullable=False),
    Column("source_batch", Text),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("zone IN ('OK', 'WARNING', 'NG', 'NO_STANDARD')", name="ck_measurements_zone"),
    UniqueConstraint(*MEASUREMENT_KEY, name="uq_measurements_key"),
)

spec_master = Table(
    "spec_master",
    metadata,
    _id_column(),
    Column("part", Text, nullable=False),
    Column("operation", Text, nullable=False),
    Column("machine", Text, nullable=False),
    Column("characteristics", Text, nullable=False),
    Column("nominal", Numeric),
    Column("usl", Numeric),
    Column("lsl", Numeric),
    Column("berlaku_sejak", DateTime(timezone=True), nullable=False),
    UniqueConstraint("part", "operation", "machine", "characteristics", "berlaku_sejak",
                     name="uq_spec_master_key"),
)
