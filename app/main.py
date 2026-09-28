"""Entrypoint Streamlit dan router halaman. Jalankan: streamlit run app/main.py"""
import streamlit as st

st.set_page_config(page_title="Monitoring 80% Activity", layout="wide")

pages = [
    st.Page("dashboard.py", title="Dashboard", default=True),
    st.Page("upload.py", title="Upload Data"),
]
st.navigation(pages).run()
