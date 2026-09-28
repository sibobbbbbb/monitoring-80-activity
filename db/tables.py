"""Definisi tabel SQLAlchemy Core. Harus sejajar dengan db/schema.sql."""
from sqlalchemy import (
    BigInteger, CheckConstraint, Column, DateTime, Integer, MetaData, Numeric,
    Table, Text, UniqueConstraint, func,
)

metadata = MetaData()

MEASUREMENT_KEY = ("machine_id", "unit_id", "item_ukur", "measured_at")

measurements = Table(
    "measurements",
    metadata,
    # BigInteger tidak autoincrement di SQLite; varian Integer dipakai untuk test.
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("machine_id", Text, nullable=False),
    Column("unit_id", Text, nullable=False),
    Column("item_ukur", Text, nullable=False),
    Column("measured_at", DateTime(timezone=True), nullable=False),
    Column("value", Numeric, nullable=False),
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
