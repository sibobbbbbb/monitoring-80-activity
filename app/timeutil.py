"""Waktu disimpan UTC di database; dashboard menampilkan dan memfilter dalam WIB (UTC+7)."""
from datetime import date, datetime, time, timedelta, timezone

WIB = timezone(timedelta(hours=7))


def format_wib(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return f"{dt.astimezone(WIB).strftime(fmt)} WIB"


def day_range_utc(start: date, end: date) -> tuple[datetime, datetime]:
    """Rentang tanggal WIB inklusif [start, end] -> [start 00:00 WIB, end+1 00:00 WIB) dalam UTC."""
    lo = datetime.combine(start, time.min, tzinfo=WIB)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=WIB)
    return lo.astimezone(timezone.utc), hi.astimezone(timezone.utc)
