"""Lecture des secrets/config : st.secrets (Streamlit Cloud) ou os.getenv/.env (local)."""
import os
from dotenv import load_dotenv

load_dotenv()


def get(key, default=None):
    try:
        import streamlit as st
        val = st.secrets.get(key)
        if val is not None:
            return str(val)
    except Exception:
        pass
    return os.getenv(key, default)
