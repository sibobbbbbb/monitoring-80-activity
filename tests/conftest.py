import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

SCHEMA_SQL = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


@pytest.fixture
def pg_engine():
    """Engine ke Postgres sungguhan, terisolasi di schema sementara.

    Schema dibuat dari db/schema.sql yang asli (sekaligus memverifikasi DDL-nya),
    lalu dihapus setelah test. Data di schema public tidak tersentuh.
    Skip bila DATABASE_URL tidak di-set (mis. pytest lokal tanpa docker compose);
    bila di-set tapi DB tidak terjangkau, test gagal (tidak di-skip diam-diam).
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL tidak di-set; test Postgres jalan lewat docker compose")

    schema = f"test_{uuid.uuid4().hex[:12]}"  # hex internal, aman untuk identifier DDL
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))

    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(SCHEMA_SQL.read_text(encoding="utf-8"))
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()
