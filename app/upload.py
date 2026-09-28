"""Halaman upload manual CSV/XLSX: parse, preview, konfirmasi, lalu upsert ke Postgres."""
from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from adapters.column_mapping import DEFAULT_MAPPING
from adapters.file_adapter import load_measurements_from_bytes
from app.timeutil import WIB
from db.connection import get_engine
from db.ingestion import upsert_measurements

PREVIEW_ROWS = 20
STATE_KEY = "upload_state"


def _confirm(state: dict) -> None:
    """Callback tombol konfirmasi: upsert, lalu kosongkan cache agar dashboard langsung segar."""
    state["error"] = None
    try:
        state["upsert"] = upsert_measurements(get_engine(), state["result"].data)
    except Exception as exc:  # DB mati / constraint: jangan crash, tampilkan pesan
        state["error"] = type(exc).__name__
        return
    st.cache_data.clear()


def _preview_table(data: pd.DataFrame) -> pd.DataFrame:
    t = data.head(PREVIEW_ROWS).copy()
    t["measured_at"] = t["measured_at"].dt.tz_convert(WIB).dt.strftime("%Y-%m-%d %H:%M:%S")
    for col in ("value", "nominal", "usl", "lsl", "ratio"):
        t[col] = t[col].astype(float)  # hanya untuk tampilan
    t = t.drop(columns="source_batch")
    t.columns = ["Part", "Operation", "Mesin", "Characteristics", "Waktu (WIB)", "XChart",
                 "Nominal", "USL", "LSL", "Rasio", "Zona"]
    return t


st.title("Upload export FEXQMS (fallback)")
st.info("Jalur fallback untuk investigasi satu item cek atau backfill historis. Export FEXQMS hanya bisa "
        "per satu item cek, jadi upload manual bukan jalur utama monitoring.")
st.caption("Unggah export Control Chart FEXQMS (XLSX atau CSV). Nilai yang dipakai adalah kolom XChart; "
           "USL/LSL dibaca dari header file. Waktu tanpa zona dianggap WIB dan disimpan sebagai UTC. "
           "Data baru langsung tampil di Dashboard setelah dikonfirmasi.")

with st.expander("Format file yang diharapkan"):
    st.markdown("**Blok header** (label di kiri, nilai di sel kanannya):")
    st.table(pd.DataFrame({"Field": list(DEFAULT_MAPPING.header_labels),
                           "Label di file": list(DEFAULT_MAPPING.header_labels.values())}))
    st.markdown("**Tabel data** (judul kolom):")
    st.table(pd.DataFrame({"Field": list(DEFAULT_MAPPING.table_columns),
                           "Kolom di file": list(DEFAULT_MAPPING.table_columns.values())}))
    st.caption("Wajib: Part, Operation, Machine, Characteristics di header; Sample Date Time dan XChart di tabel. "
               "USL/LSL boleh kosong (LSL 0 dianggap nilai, bukan kosong); tanpa USL dan LSL baris disimpan "
               "sebagai NO_STANDARD. Sheet ringkasan pelanggaran (Sheet1) diabaikan.")

uploaded = st.file_uploader("File CSV atau XLSX", type=["csv", "xlsx"], key="upload_file")
if uploaded is None:
    st.session_state.pop(STATE_KEY, None)
    st.stop()

file_id = getattr(uploaded, "file_id", None) or uploaded.name
state = st.session_state.get(STATE_KEY)
if state is None or state["file_id"] != file_id:
    batch = f"{uploaded.name}#{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    try:
        result = load_measurements_from_bytes(uploaded.getvalue(), uploaded.name, source_batch=batch)
    except ValueError as exc:  # kolom wajib hilang, format tidak didukung, CSV rusak/kosong
        st.error(f"File tidak dapat diproses: {exc}")
        st.stop()
    except Exception as exc:
        st.error("File tidak dapat dibaca. Pastikan formatnya CSV/XLSX yang valid.")
        st.caption(f"Detail: {type(exc).__name__}")
        st.stop()
    state = {"file_id": file_id, "result": result, "batch": batch, "upsert": None, "error": None}
    st.session_state[STATE_KEY] = state

data, failures, duplicates = state["result"]

st.subheader(f"Preview: {uploaded.name}")
c1, c2, c3 = st.columns(3)
c1.metric("Baris sukses", f"{len(data):,}")
c2.metric("Baris gagal", f"{len(failures):,}")
c3.metric("Baris duplikat", f"{len(duplicates):,}")
st.caption("Baris sukses = baris yang akan diunggah (duplikat dalam file sudah digabung). "
           "Baris gagal tidak diunggah dan tidak menghentikan baris lain.")
if len(data):
    zones = data["zone"].value_counts()
    st.caption(" · ".join(f"{z}: {int(zones.get(z, 0))}" for z in ("OK", "WARNING", "NG", "NO_STANDARD")))

if failures:
    st.subheader("Baris gagal")
    st.dataframe(pd.DataFrame([{"Baris": f.row_number, "Alasan": f.reason} for f in failures]),
                 hide_index=True, key="failures_table")

if duplicates:
    st.subheader("Baris duplikat")
    st.caption("Kunci (part, operation, mesin, characteristics, waktu) sama dalam satu file: "
               "baris terakhir yang dipakai.")
    st.dataframe(pd.DataFrame([{"Baris ditimpa": w.row_number, "Ditimpa oleh baris": w.overwritten_by,
                                "Keterangan": w.reason} for w in duplicates]),
                 hide_index=True, key="duplicates_table")

if len(data):
    st.subheader(f"Data yang akan diunggah (maks {PREVIEW_ROWS} baris pertama)")
    st.dataframe(_preview_table(data), hide_index=True, key="preview_table")

st.divider()
if state["upsert"] is None:
    if state["error"]:
        st.error("Gagal menyimpan ke database. Tidak ada data yang diubah; coba lagi.")
        st.caption(f"Detail: {state['error']}")
    if len(data) == 0:
        st.info("Tidak ada baris valid untuk diunggah.")
    st.button("Konfirmasi upload", type="primary", disabled=len(data) == 0,
              on_click=_confirm, args=(state,), key="confirm_upload")
else:
    res = state["upsert"]
    st.success("Upload selesai. Cache dashboard sudah dikosongkan; data baru langsung terlihat di Dashboard.")
    r1, r2, r3 = st.columns(3)
    r1.metric("Baris baru masuk", f"{res.inserted:,}")
    r2.metric("Ter-update (nilai berubah)", f"{res.updated:,}")
    r3.metric("Tidak berubah", f"{res.unchanged:,}")
    st.caption(f"{len(failures):,} baris gagal dilewati · {len(duplicates):,} baris duplikat digabung · "
               f"batch `{state['batch']}`")
