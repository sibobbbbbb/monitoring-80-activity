"""Koneksi database. URL dibaca dari env DATABASE_URL (di-set oleh docker-compose)."""
import os
from functools import lru_cache
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@lru_cache(maxsize=1)
def get_engine(url: Optional[str] = None) -> Engine:
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL belum di-set")
    return create_engine(url, pool_pre_ping=True)
