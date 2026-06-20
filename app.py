# app.py
"""Dashboard NHL : stats (API NHL) + contrats (PuckPedia).
Lance : python -m streamlit run app.py

- Stats   : nhl_stats.json     (update_stats.py / tâche planifiée)
- Contrats: nhl_contracts.json (bouton 'Update All contracts' -> update_contracts.py)

Jointure : playerId (stats) == nhl_id (contrats).
Tous les joueurs sont affichés ; contrat vide si pas de contrat actif.
Recherche par nom + filtre équipe dans chaque onglet.
"""

import json
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

import update_contracts as uc

STATS_FILE = "nhl_stats.json"
CONTRACTS_FILE = "nhl_contracts.json"

st.set_page_config(page_title="NHL Stats + Contrats", layout="wide")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def fmt_age(iso):
    if not iso:
        return "jamais"
    try:
        dt = datetime.fromisoformat(iso)
        delta = datetime.now(timezone.utc) - dt
        h = int(delta.total_seconds() // 3600)
        if h < 1:
            return "il y a < 1 h"
        if h < 24:
            return f"il y a {h} h"
        return f"il y a {h // 24} j"
    except ValueError:
        return iso


# ----------------------------------------------------------------------
# Chargement
# ----------------------------------------------------------------------
stats = load_json(STATS_FILE, None)
contracts_db = load_json(CONTRACTS_FILE, {"contracts": {}})
cache = contracts_db.get("contracts", {})   # clé = nhl_id (str)

st.title("🏒 NHL — Stats & Contrats")

if stats is None:
    st.error(f"`{STATS_FILE}` introuvable. Lance d'abord `python update_stats.py`.")
    st.stop()

players = stats.get("players", [])


# ----------------------------------------------------------------------
# Contrats + DataFrames
# ----------------------------------------------------------------------
def contract_for(player_id):
    if player_id is None:
        return {}
    return cache.get(str(player_id), {})


def expiry_label(c):
    etype = c.get("expiry_status")
    eyear = c.get("expiry_year")
    if etype and eyear:
        return f"{etype} {eyear}"
    return etype or "—"


def build_df(player_type):
    rows = []
    for p in players:
        if p.get("type") != player_type:
            continue
        c = contract_for(p.get("playerId"))
        base = {
            "Nom": p["name"],
            "Équipe": p.get("team"),
            "Pos": p.get("position"),
            "GP": p.get("gp"),
            "Cap Hit": c.get("cap_hit_value", 0) if c else 0,
            "Signing": c.get("signing_status") or "—",
            "Expiry": expiry_label(c) if c else "—",
            "Clauses": c.get("clauses") or "—",
        }
        if player_type == "skater":
            base.update({
                "G": p.get("goals"), "A": p.get("assists"),
                "Pts": p.get("points"), "+/-": p.get("plus_minus"),
                "PIM": p.get("pim"), "PPP": p.get("ppp"),
                "SOG": p.get("sog"), "HIT": p.get("hits"),
            })
        else:
            base.update({
                "V": p.get("wins"), "D": p.get("losses"),
                "DPr": p.get("ot_losses"), "Moy": p.get("gaa"),
                "%Arr": p.get("sv_pct"), "BL": p.get("shutouts"),
            })
        rows.append(base)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# Action : update complet des contrats
# ----------------------------------------------------------------------
def run_full_update():
    bar = st.progress(0.0, text="Appel API PuckPedia…")

    def cb(done, total, msg):
        bar.progress(done / total if total else 1.0, text=msg)

    with st.spinner("Récupération de tous les contrats…"):
        summary = uc.update_contracts(progress_cb=cb)
    bar.empty()
    st.success(f"Terminé : {summary['scraped']} contrats récupérés.")
    st.rerun()


# ----------------------------------------------------------------------
# En-tête
# ----------------------------------------------------------------------
c1, c2, c3 = st.columns([2, 2, 1])
c1.metric("Joueurs (stats)", len(players))
c1.caption(f"Stats MAJ : {fmt_age(stats.get('updated_at'))}")
c2.metric("Contrats récupérés", len(cache))
c2.caption(f"Contrats MAJ : {fmt_age(contracts_db.get('updated_at'))}")

with c3:
    if st.button("🔄 Update All contracts", type="primary", width="stretch"):
        run_full_update()


# ----------------------------------------------------------------------
# Rendu d'un onglet
# ----------------------------------------------------------------------
cap_col = st.column_config.NumberColumn("Cap Hit", format="$%d")


def render_tab(player_type, key_prefix, sort_col, display_cols):
    df = build_df(player_type)

    f1, f2 = st.columns([2, 2])
    search = f1.text_input("🔎 Recherche par nom", key=f"{key_prefix}_search",
                           placeholder="ex. McDavid")
    teams = ["Toutes"] + sorted(t for t in df["Équipe"].dropna().unique())
    team_sel = f2.selectbox("Équipe", teams, key=f"{key_prefix}_team")

    view = df.copy()
    if search:
        view = view[view["Nom"].str.contains(search, case=False, na=False)]
    if team_sel != "Toutes":
        view = view[view["Équipe"] == team_sel]
    if sort_col in view.columns:
        view = view.sort_values(sort_col, ascending=False)

    st.dataframe(
        view, hide_index=True, width="stretch",
        column_order=display_cols,
        column_config={"Cap Hit": cap_col},
    )
    st.caption(f"{len(view)} joueurs")


# ----------------------------------------------------------------------
# Onglets
# ----------------------------------------------------------------------
tab_s, tab_g = st.tabs(["⚡ Patineurs", "🥅 Gardiens"])

with tab_s:
    render_tab(
        "skater", "sk", "Pts",
        ["Nom", "Équipe", "Pos", "GP",
         "Cap Hit", "Signing", "Expiry", "Clauses",
         "G", "A", "Pts", "+/-", "PIM", "PPP", "SOG", "HIT"],
    )

with tab_g:
    render_tab(
        "goalie", "go", "V",
        ["Nom", "Équipe", "Pos", "GP",
         "Cap Hit", "Signing", "Expiry", "Clauses",
         "V", "D", "DPr", "Moy", "%Arr", "BL"],
    )