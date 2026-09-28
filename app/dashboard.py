"""Halaman dashboard: grid chart kecil, 1 chart = 1 item cek (Part + Operation + Characteristics).

Urutan grid mengikuti rasio per item cek (NG dulu, lalu WARNING, lalu OK); tiap chart menampilkan
XChart per mesin dengan garis USL, LSL, dan batas 80%. Dijalankan lewat router app/main.py.
"""
from collections import Counter
from datetime import date, datetime, timedelta
from math import ceil

import streamlit as st

from app.charts import item_chart, machine_colors
from app.queries import (
    Filters,
    filter_options,
    item_summary,
    last_ingested_at,
    no_standard_item_count,
    series_rows,
    time_bounds,
)
from app.summary import summarize_zones
from app.timeutil import WIB, day_range_utc, format_wib
from core.rules import classify
from db.connection import get_engine

CACHE_TTL_SECONDS = 60
ORDER_DESC = "Rasio tinggi → rendah (NG dulu)"
ORDER_ASC = "Rasio rendah → tinggi (OK dulu)"
BASIS_LATEST = "Rasio terakhir"
BASIS_PEAK = "Rasio tertinggi dalam rentang"
BADGE_COLORS = {"NG": "red", "WARNING": "orange", "OK": "green"}
RANGE_PRESETS = {"7 hari": 7, "14 hari": 14, "30 hari": 30, "90 hari": 90}
RANGE_ALL, RANGE_CUSTOM = "Semua data", "Kustom"
EARLIEST_DAY = date(2000, 1, 1)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def load_last_ingested():
    return last_ingested_at(get_engine())


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def load_time_bounds():
    return time_bounds(get_engine())


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def load_options():
    return filter_options(get_engine())


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def load_item_summary(f: Filters):
    return item_summary(get_engine(), f)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def load_no_standard_items(f: Filters):
    return no_standard_item_count(get_engine(), f)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def load_series(f: Filters, items: tuple):
    return series_rows(get_engine(), f, list(items))


title_col, last_col, refresh_col = st.columns([5, 3, 2], vertical_alignment="center")
title_col.title("Monitoring 80% Activity")

try:
    last = load_last_ingested()
    bounds = load_time_bounds()
except Exception as exc:  # DB mati / belum siap: tampilkan pesan, jangan crash
    st.error("Tidak dapat membaca database. Pastikan service `db` berjalan.")
    st.caption(f"Detail: {type(exc).__name__}")
    st.stop()

last_col.markdown(f"**Data terakhir:** {format_wib(last) if last else 'belum ada data'}")
if refresh_col.button("Refresh Sekarang", type="primary", width="stretch"):
    st.cache_data.clear()
    st.rerun()

if bounds[0] is None:
    st.info("Belum ada data pengukuran. Data dummy: "
            "`docker compose run --rm app python -m scripts.generate_dummy_data`. "
            "Fallback: unggah export FEXQMS lewat halaman **Upload Data** di sidebar.")
    st.stop()

options = load_options()
options["jenis"] = options["part"] + " · " + options["operation"]
min_day, max_day = bounds[0].astimezone(WIB).date(), bounds[1].astimezone(WIB).date()
today = datetime.now(WIB).date()

with st.sidebar:
    st.header("Filter")
    preset = st.segmented_control(
        "Rentang waktu (WIB)", [*RANGE_PRESETS, RANGE_ALL, RANGE_CUSTOM], default=RANGE_ALL, key="f_preset",
        help="N hari terakhir dihitung mundur dari tanggal data terbaru. Kustom: pilih tanggal mulai dan akhir bebas.",
    ) or RANGE_ALL                                    # None bila pilihan dibatalkan -> semua data
    if preset == RANGE_CUSTOM:
        # batas awal dibuka lebar (tidak dikunci ke data tertua) agar bisa mundur sejauh apa pun
        picked = st.date_input("Tanggal (mulai – akhir)", value=(min_day, max_day),
                               min_value=EARLIEST_DAY, max_value=max(max_day, today), key="f_dates")
    elif preset in RANGE_PRESETS:
        picked = (max_day - timedelta(days=RANGE_PRESETS[preset] - 1), max_day)
    else:
        picked = (min_day, max_day)
    jenis_sel = st.multiselect("Jenis item cek (Part · Operation)", sorted(options["jenis"].unique()), key="f_jenis")
    scoped = options[options["jenis"].isin(jenis_sel)] if jenis_sel else options
    chars_sel = st.multiselect("Item cek (Characteristics)", sorted(scoped["characteristics"].unique()), key="f_chars")
    if chars_sel:
        scoped = scoped[scoped["characteristics"].isin(chars_sel)]
    machines_sel = st.multiselect("Mesin", sorted(scoped["machine"].unique()), key="f_machines")

    st.header("Urutan dan tampilan")
    order = st.radio("Urutan chart", [ORDER_DESC, ORDER_ASC], key="f_order")
    basis = st.radio("Dasar urutan", [BASIS_LATEST, BASIS_PEAK], key="f_basis",
                     help="Terakhir: rasio terburuk dari pengukuran terakhir tiap mesin. "
                          "Tertinggi: rasio maksimum di seluruh rentang tanggal.")
    n_cols = st.selectbox("Kolom grid", [1, 2, 3, 4], index=1, key="f_cols",
                          help="Makin sedikit kolom, makin lebar chart. 1 = lebar penuh.")
    page_size = st.selectbox("Chart per halaman", [6, 12, 24, 48], index=1, key="f_pagesize")

if len(picked) != 2:
    st.info("Pilih tanggal akhir pada rentang tanggal.")
    st.stop()

st.caption(f"Rentang tanggal: {picked[0]:%d %b %Y} – {picked[1]:%d %b %Y} (WIB)")
start, end = day_range_utc(picked[0], picked[1])
jenis_pairs = tuple(sorted({(r.part, r.operation) for r in options[options["jenis"].isin(jenis_sel)].itertuples()}))
f = Filters(start=start, end=end, jenis=jenis_pairs, characteristics=tuple(chars_sel), machines=tuple(machines_sel))

summary = load_item_summary(f)
if summary.empty:
    st.info("Tidak ada item cek ber-standar untuk filter ini.")
    st.stop()

sort_col = "latest_ratio" if basis == BASIS_LATEST else "max_ratio"
summary = summary.assign(sort_ratio=summary[sort_col])
summary["zone"] = summary["sort_ratio"].map(lambda r: classify(r).value)
summary = summary.sort_values(["sort_ratio", "part", "operation", "characteristics"],
                              ascending=[order == ORDER_ASC, True, True, True]).reset_index(drop=True)

counts = summarize_zones(Counter(summary["zone"]))
cards = st.columns(4)
cards[0].metric("Item cek", f"{counts.total:,}")
for col, zone in zip(cards[1:], ("NG", "WARNING", "OK"), strict=True):
    col.metric(zone, f"{counts.counts[zone]:,}")
    col.caption(f"{counts.percents[zone]:.1f}% dari item cek")
skipped = load_no_standard_items(f)
if skipped:
    st.caption(f"{skipped:,} item cek tanpa standar (NO_STANDARD) tidak ditampilkan. "
               f"Status memakai {basis.lower()}; waktu dalam WIB.")
else:
    st.caption(f"Status memakai {basis.lower()}; waktu dalam WIB.")

pages = ceil(len(summary) / page_size)
page = 1
if pages > 1:
    page = st.selectbox(f"Halaman (dari {pages})", list(range(1, pages + 1)), key="f_page")
first, last_idx = (page - 1) * page_size, min(page * page_size, len(summary))
st.caption(f"Menampilkan item cek {first + 1}–{last_idx} dari {len(summary)}.")

shown = summary.iloc[first:last_idx]
keys = tuple((r.part, r.operation, r.characteristics) for r in shown.itertuples())
series = load_series(f, keys)
colors = machine_colors(options["machine"])

width = min(n_cols, len(shown))     # sedikit item -> kartu memakai lebar penuh, bukan separuh kosong
for i in range(0, len(shown), width):
    for cell, item in zip(st.columns(width), list(shown.iloc[i:i + width].itertuples()), strict=False):
        rows = series[(series["part"] == item.part) & (series["operation"] == item.operation)
                      & (series["characteristics"] == item.characteristics)]
        with cell, st.container(border=True):
            st.markdown(f"**{item.characteristics}**")
            st.caption(f"{item.part} · {item.operation}")
            st.markdown(f":{BADGE_COLORS[item.zone]}-badge[{item.zone} · {float(item.sort_ratio):.0%}]")
            st.plotly_chart(item_chart(rows, colors), key=f"chart|{item.part}|{item.operation}|{item.characteristics}",
                            config={"displayModeBar": False})
            st.caption(f"Terakhir {float(item.latest_ratio):.0%} · Tertinggi {float(item.max_ratio):.0%} "
                       f"· {item.machine_count} mesin")
