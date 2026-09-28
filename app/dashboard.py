"""Halaman dashboard Monitoring 80% Activity (baca-saja). Dijalankan lewat router app/main.py."""
from dataclasses import replace

import streamlit as st

from app.charts import bullet_chart
from app.queries import (
    Filters, filter_options, last_ingested_at, priority_rows, time_bounds, zone_counts,
)
from app.summary import STANDARD_ZONES, summarize_zones
from app.timeutil import WIB, day_range_utc, format_wib
from db.connection import get_engine

CACHE_TTL_SECONDS = 60
TABLE_LIMIT = 200


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
def load_zone_counts(f: Filters):
    return zone_counts(get_engine(), f)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def load_priority(f: Filters, limit: int):
    return priority_rows(get_engine(), f, limit)


# ---- header: judul, data terakhir, tombol refresh -------------------------------------------
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
    st.info("Belum ada data pengukuran. Unggah file CSV/XLSX lewat halaman **Upload Data** di sidebar.")
    st.stop()

# ---- filter ---------------------------------------------------------------------------------
options = load_options()
min_day, max_day = bounds[0].astimezone(WIB).date(), bounds[1].astimezone(WIB).date()

with st.sidebar:
    st.header("Filter")
    machines = st.multiselect("Mesin", sorted(options["machine_id"].unique()))
    scoped = options[options["machine_id"].isin(machines)] if machines else options
    units = st.multiselect("Unit", sorted(scoped["unit_id"].unique()))
    if units:
        scoped = scoped[scoped["unit_id"].isin(units)]
    items = st.multiselect("Item ukur", sorted(scoped["item_ukur"].unique()))
    picked = st.date_input("Rentang waktu (WIB)", value=(min_day, max_day),
                           min_value=min_day, max_value=max_day)
    zones = st.multiselect("Zona", list(STANDARD_ZONES), default=["WARNING"],
                           help="Berlaku untuk tabel dan grafik. Kartu ringkasan menampilkan semua zona.")

if len(picked) != 2:
    st.info("Pilih tanggal akhir pada rentang waktu.")
    st.stop()

start, end = day_range_utc(picked[0], picked[1])
scope = Filters(start=start, end=end, machines=tuple(machines), units=tuple(units), items=tuple(items))
selected = replace(scope, zones=tuple(zones))

# ---- kartu ringkasan (semua zona, sesuai filter mesin/unit/item/waktu) ------------------------
summary = summarize_zones(load_zone_counts(scope))
cards = st.columns(4)
cards[0].metric("Total pengukuran", f"{summary.total:,}")
for col, zone in zip(cards[1:], STANDARD_ZONES):
    col.metric(zone, f"{summary.counts[zone]:,}")
    col.caption(f"{summary.percents[zone]:.1f}% dari total")
if summary.no_standard:
    st.caption(f"{summary.no_standard:,} pengukuran tanpa standar (NO_STANDARD) tidak dihitung.")

# ---- tabel prioritas --------------------------------------------------------------------------
st.subheader("Prioritas: rasio tertinggi")
priority = load_priority(selected, TABLE_LIMIT)
if priority.empty:
    st.info("Tidak ada pengukuran yang cocok dengan filter.")
    st.stop()

st.caption(f"Menampilkan {len(priority):,} baris (maksimum {TABLE_LIMIT}), diurutkan dari rasio tertinggi.")
table = priority.drop(columns=["deviation"]).copy()
table["measured_at"] = table["measured_at"].dt.tz_convert(WIB).dt.strftime("%Y-%m-%d %H:%M:%S")
for col in ("value", "nominal", "usl", "lsl", "ratio"):
    table[col] = table[col].astype(float)  # hanya untuk tampilan
table.columns = ["Mesin", "Unit", "Item", "Waktu (WIB)", "Nilai", "Nominal", "USL", "LSL", "Rasio", "Zona"]
st.dataframe(
    table,
    hide_index=True,
    column_config={"Rasio": st.column_config.ProgressColumn("Rasio", min_value=0, max_value=1.5, format="%.3f")},
)

# ---- bullet chart -------------------------------------------------------------------------------
st.subheader("Deviasi ternormalisasi")
top_n = st.slider("Jumlah baris di grafik", min_value=5, max_value=50, value=min(20, TABLE_LIMIT))
st.plotly_chart(bullet_chart(priority, max_rows=top_n))
