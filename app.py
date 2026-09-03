# app.py
"""Dashboard NHL : stats (API NHL) + contrats (PuckPedia) + pool ESPN.
Lance : python -m streamlit run app.py

Sources :
- nhl_stats.json     : stats (update_stats.py / tâche planifiée)
- nhl_contracts.json : contrats (bouton 'Update All contracts')
- espn_owned.json    : rosters du pool ESPN (bouton 'Update pool')

Jointures :
- stats <-> contrats : playerId == nhl_id
- stats <-> pool ESPN: par nom normalisé

Coloration : Nordic bleu 60%, autres managers gris 80%, libres transparents.
Filtres combinables au-dessus de chaque tableau.
"""

import concurrent.futures
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

import update_contracts as uc
import espn_roster as er
import draft_engine as de
import update_stats as us
import player_status as ps
import seasons as seasons_mod

STATS_FILE = "nhl_stats.json"
CONTRACTS_FILE = "nhl_contracts.json"

# PuckPedia (Cloudflare) renvoie 403 aux IP des serveurs Streamlit Community
# Cloud : le fetch en direct des contrats y est donc impossible. Sur Cloud, on
# s'appuie uniquement sur le job GitHub Actions nocturne (qui commit
# nhl_contracts.json). En local, le fetch fonctionne normalement.
# Détection : Streamlit Community Cloud exécute l'app sous l'utilisateur
# « appuser » (HOME=/home/appuser).
IS_CLOUD = os.environ.get("HOME", "") == "/home/appuser"

COLOR_MINE = "rgba(0, 114, 206, 0.60)"     # bleu Nordique
COLOR_OTHER = "rgba(128, 128, 128, 0.80)"  # gris
COLOR_TARGET = "rgba(255, 224, 102, 0.45)" # jaune pâle (cibles au repêchage)
COLOR_NONE = ""

POOL_ABBR = {
    "Quebec Nordic": "QUE",
    "Montreal Canadiens": "MTL",
    "Pittsburgh Penguins": "PIT",
    "Buffalo Sabres": "BUF",
    "Chicago Blackhawks": "CHI",
    "Tampa Bay Lightning": "TBL",
    "Los Angeles Kings": "LAK",
    "Anaheim Ducks": "ANA",
    "Colorado Avalanche": "COL",
    "Ottawa Senators": "OTT",
}


def pool_abbr(name):
    if not name:
        return "—"
    return POOL_ABBR.get(name, name[:3].upper())


st.set_page_config(page_title="NHL Stats + Contrats + Pool", layout="wide")

# Compacter : réduire la marge du haut de page (sans casser l'alignement)
st.markdown("""
<style>
.block-container { padding-top: 2rem; }
</style>
""", unsafe_allow_html=True)


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


def _updated_today(path):
    """True si le JSON `path` a un updated_at tombant aujourd'hui (heure locale).

    Sert à sauter le refresh Stats/Contrats quand les données du matin sont déjà
    en place. Fichier absent, sans updated_at ou illisible -> False (on rafraîchit,
    comportement sûr).
    """
    data = load_json(path, {}) or {}
    iso = data.get("updated_at")
    if not iso:
        return False
    try:
        dt = datetime.fromisoformat(iso).astimezone()  # UTC -> tz locale
    except ValueError:
        return False
    return dt.date() == datetime.now().astimezone().date()


def norm_name(name):
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[.'-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ----------------------------------------------------------------------
# Chargement
# ----------------------------------------------------------------------
REFRESH_TIMEOUT = 30

# ---------- Auto-refresh au chargement (réveil Streamlit Community Cloud) -----
if "_auto_refreshed" not in st.session_state:
    _status_box = st.status("⏳ Actualisation automatique des données…", expanded=True)
    with _status_box:
        _ph_stats = st.empty()
        _ph_pool = st.empty()
        _ph_contracts = st.empty()
        # Stats & Contrats sont dispo chaque matin : on ne les re-télécharge pas
        # si leur JSON est déjà daté d'aujourd'hui (heure locale). Le Pool ESPN,
        # lui, est toujours rafraîchi à chaque ouverture. Les boutons manuels
        # (📊/🔄) forcent toujours la MàJ (ils n'utilisent pas ce test).
        _stats_fresh = _updated_today(STATS_FILE)
        _contracts_fresh = _updated_today(CONTRACTS_FILE)

        if _stats_fresh:
            _ph_stats.write("✅ **Stats NHL** : déjà à jour (aujourd'hui)")
        else:
            _ph_stats.write("📊 **Stats NHL** : récupération en cours…")
        _ph_pool.write("🏒 **Pool ESPN** : récupération en cours…")
        # Sur Cloud, PuckPedia renvoie 403 : on ne tente pas le fetch en direct
        # (les contrats sont rafraîchis chaque nuit via GitHub Actions).
        if IS_CLOUD:
            _ph_contracts.write("🌙 **Contrats** : mis à jour chaque nuit (GitHub Actions)")
        elif _contracts_fresh:
            _ph_contracts.write("✅ **Contrats** : déjà à jour (aujourd'hui)")
        else:
            _ph_contracts.write("🔄 **Contrats** : récupération en cours…")

        def _task_stats():
            us.main()

        def _task_pool():
            er.update_owned()

        def _task_contracts():
            uc.update_contracts()

        _rf_res = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as _ex:
            _futures = {
                _ex.submit(_task_pool): ("pool", _ph_pool, "🏒 Pool ESPN"),
            }
            if not _stats_fresh:
                _futures[_ex.submit(_task_stats)] = (
                    "stats", _ph_stats, "📊 Stats NHL")
            if not IS_CLOUD and not _contracts_fresh:
                _futures[_ex.submit(_task_contracts)] = (
                    "contracts", _ph_contracts, "🔄 Contrats")
            _done, _not_done = concurrent.futures.wait(
                list(_futures.keys()), timeout=REFRESH_TIMEOUT
            )
            for _f in _done:
                _key, _ph, _lbl = _futures[_f]
                try:
                    _f.result()
                    _rf_res[_key] = "ok"
                    _ph.write(f"✅ **{_lbl}** : à jour")
                except Exception as _e:
                    _rf_res[_key] = "error"
                    _ph.write(f"❌ **{_lbl}** : erreur — {_e}")
            for _f in _not_done:
                _key, _ph, _lbl = _futures[_f]
                _rf_res[_key] = "timeout"
                _ph.write(
                    f"⚠️ **{_lbl}** : délai {REFRESH_TIMEOUT} s dépassé "
                    "— données précédentes conservées"
                )
                _f.cancel()

        if all(v == "ok" for v in _rf_res.values()):
            _status_box.update(
                label="✅ Données actualisées", state="complete", expanded=False
            )
        else:
            _status_box.update(
                label="⚠️ Actualisation partielle — voir détails",
                state="error",
                expanded=True,
            )

    time.sleep(1.2)
    st.session_state["_auto_refreshed"] = True
    st.session_state["_refresh_results"] = _rf_res
    st.rerun()

# ----------------------------------------------------------------------
# Sélecteur de saison : choisit d'où charger stats + contrats.
#   - saison en cours / tampon -> fichiers racine (live, MàJ quotidienne)
#   - saison archivée          -> archive/<saison>/{stats,contracts}.json (figés)
# Par défaut : la plus récente (1re option). Voir seasons.py / season_admin.py.
# ----------------------------------------------------------------------
_season_opts = seasons_mod.list_selectable()
with st.sidebar:
    _sel_label = st.selectbox(
        "Saison", [o["label"] for o in _season_opts], index=0, key="season_select"
    )
    # Contrôle global du mode d'affichage (Dispo + coloration), appliqué à
    # TOUS les onglets. Repêchage : dispo/couleurs pilotées par le plan manuel
    # (onglet Pool STM) — gris = Autre DG, bleu = mes joueurs, jaune = cibles,
    # « Pool Team » ESPN conservé à titre informatif. Saison : possession ESPN.
    st.radio(
        "Mode d'affichage", ["🏒 Repêchage", "📅 Saison"], horizontal=True,
        key="team_mode",
        help="Repêchage : dispo/couleurs selon ton plan (Pool STM). "
             "Saison : possession réelle collectée par ESPN.",
    )
_sel = next(o for o in _season_opts if o["label"] == _sel_label)
STATS_PATH = str(_sel["stats_path"])
CONTRACTS_PATH = str(_sel["contracts_path"])
_SEL_IS_LIVE = _sel["kind"] in ("active", "buffer")

stats = load_json(STATS_PATH, None)

# Génération automatique des stats si le fichier est absent.
if stats is None:
    if _SEL_IS_LIVE:
        # Cas live (ex. après un clone où nhl_stats.json n'aurait pas été versionné).
        st.warning(f"`{STATS_PATH}` introuvable — génération automatique des stats "
                   "NHL en cours (10–20 secondes)…")
        try:
            import update_stats
            with st.spinner("Récupération des stats depuis l'API NHL…"):
                update_stats.main()
            st.success("Stats générées. Rechargement…")
            st.rerun()
        except Exception as e:
            st.error(
                f"Échec de la génération automatique : {e}\n\n"
                f"Lance manuellement `python update_stats.py` dans un terminal, "
                "puis recharge la page."
            )
            st.stop()
    else:
        # Saison archivée manquante : rien à régénérer (archive figée).
        st.error(
            f"Archive de la saison **{_sel_label}** introuvable (`{STATS_PATH}`).\n\n"
            "Choisis une autre saison dans le menu de gauche."
        )
        st.stop()

contracts_db = load_json(CONTRACTS_PATH, {"contracts": {}})
cache = contracts_db.get("contracts", {})

espn_db = er.load_owned()
owned = espn_db.get("owned", {})

players = stats.get("players", [])


# ----------------------------------------------------------------------
# Statut roster : overrides MANUELS uniquement (voir player_status.py).
#   - "rookie"  : recrue/nouveau absent des stats -> injecté comme jouable.
#   - "retired" : retraité -> ligne rouge, retiré des tableaux actifs.
# Le manuel gagne toujours ; il n'y a plus d'auto-détection NHL ni de suspects.
# ----------------------------------------------------------------------
_stats_ids = {str(p.get("playerId")) for p in players}
_STATUS = ps.load_status()              # {id: {status, name, position, team, age, note}}
RETIRED_IDS = {pid for pid, e in _STATUS.items() if e.get("status") == "retired"}
ROOKIE_IDS = {pid for pid, e in _STATUS.items() if e.get("status") == "rookie"}

# Recrues absentes des stats : on fabrique une ligne « joueur » jouable (les
# recrues déjà présentes dans les stats gardent leurs stats, juste marquées).
for _pid in ROOKIE_IDS:
    if _pid in _stats_ids:
        continue
    players.append(ps.make_rookie_row(_pid, _STATUS[_pid], cache.get(_pid)))

# Lookup des lignes joueur (augmentées) par id — sert à réinjecter les recrues
# dans les tables scorées qui filtrent par GP.
PLAYERS_BY_ID = {str(p.get("playerId")): p for p in players}


def statut_for(player_id):
    pid = str(player_id)
    if pid in RETIRED_IDS:
        return "RET"
    if pid in ROOKIE_IDS:
        return "NOUVEAU"
    return "actif"


def is_new(player_id):
    return str(player_id) in ROOKIE_IDS


def is_retired(player_id):
    return str(player_id) in RETIRED_IDS


# Toasts de confirmation après l'auto-refresh
if "_refresh_results" in st.session_state:
    _res_toast = st.session_state.pop("_refresh_results")
    _toast_labels = {"stats": "📊 Stats NHL", "pool": "🏒 Pool ESPN"}
    for _k, _v in _res_toast.items():
        _n = _toast_labels.get(_k, _k)
        if _v == "ok":
            st.toast(f"{_n} : données à jour", icon="✅")
        elif _v == "timeout":
            st.toast(f"{_n} : délai {REFRESH_TIMEOUT} s dépassé — données conservées", icon="⚠️")
        else:
            st.toast(f"{_n} : erreur lors de la mise à jour", icon="❌")


# ----------------------------------------------------------------------
# Jointures
# ----------------------------------------------------------------------
def contract_for(player_id):
    if player_id is None:
        return {}
    return cache.get(str(player_id), {})


def pool_for(player_name):
    entry = owned.get(norm_name(player_name))
    if not entry:
        return None, False
    return entry.get("pool_team"), entry.get("is_mine", False)


def expiry_label(c):
    etype = c.get("expiry_status")
    eyear = c.get("expiry_year")
    if etype and eyear:
        return f"{etype} {eyear}"
    return etype or "—"


def players_with_cap(player_type):
    """Liste des joueurs d'un type, enrichis du cap_hit depuis les contrats."""
    out = []
    for p in players:
        if p.get("type") != player_type:
            continue
        c = contract_for(p.get("playerId"))
        q = dict(p)
        q["cap_hit_value"] = c.get("cap_hit_value", 0) if c else 0
        q["signing_status"] = c.get("signing_status") if c else None
        out.append(q)
    return out


def build_df(player_type):
    rows = []
    for p in players:
        if p.get("type") != player_type:
            continue
        c = contract_for(p.get("playerId"))
        pool_team, is_mine = pool_for(p.get("name"))
        base = {
            "_pid": str(p.get("playerId")),
            "_mine": is_mine,
            "_owned": pool_team is not None,
            "_new": is_new(p.get("playerId")),
            "_retired": is_retired(p.get("playerId")),
            "_pool_full": pool_team or "",
            "Nom": p["name"],
            "Statut": statut_for(p.get("playerId")),
            "NHL Team": p.get("team"),
            "Pool Team": pool_abbr(pool_team) if pool_team else "—",
            "Pos": p.get("position"),
            "Âge": int(p["age"]) if p.get("age") is not None else None,
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
# Actions d'update
# ----------------------------------------------------------------------
def run_stats_update():
    with st.spinner("Récupération des stats depuis l'API NHL…"):
        try:
            us.main()
        except Exception as e:
            st.error(f"Échec stats : {e}")
            return
    st.rerun()


def run_full_update():
    # Sur Cloud, PuckPedia renvoie 403 : inutile de tenter, on informe.
    if IS_CLOUD:
        st.info(
            "Sur Streamlit Cloud, PuckPedia bloque les mises à jour en direct "
            "(403). Les contrats sont rafraîchis **automatiquement chaque nuit** "
            "via GitHub Actions — cache actuel : "
            f"{fmt_age(contracts_db.get('updated_at'))}."
        )
        return

    bar = st.progress(0.0, text="Appel API PuckPedia…")

    def cb(done, total, msg):
        bar.progress(done / total if total else 1.0, text=msg)

    try:
        with st.spinner("Récupération de tous les contrats…"):
            summary = uc.update_contracts(progress_cb=cb)
        bar.empty()
        st.success(f"Terminé : {summary['scraped']} contrats récupérés.")
        st.rerun()
    except Exception as e:
        bar.empty()
        st.warning(
            f"Impossible de récupérer les contrats depuis PuckPedia : `{e}`\n\n"
            "Les données de contrats en cache sont conservées "
            f"(dernière mise à jour {fmt_age(contracts_db.get('updated_at'))}). "
            "Elles sont de toute façon rafraîchies automatiquement chaque nuit "
            "via GitHub Actions."
        )


def run_pool_update():
    with st.spinner("Récupération des rosters ESPN…"):
        try:
            res = er.update_owned()
            st.success(f"Pool mis à jour : {res['count']} joueurs possédés.")
        except Exception as e:
            st.error(f"Échec ESPN : {e}")
            return
    st.rerun()


# ----------------------------------------------------------------------
# En-tête compact
# ----------------------------------------------------------------------
st.markdown("#### 🏒 NHL — Stats, Contrats & Pool")
hc1, hc2, hc3, hc4 = st.columns([4, 1.3, 1.3, 1.3])

# Vérification ancienneté des contrats
_contracts_age_h = None
if contracts_db.get("updated_at"):
    try:
        _cdt = datetime.fromisoformat(contracts_db["updated_at"])
        _contracts_age_h = (datetime.now(timezone.utc) - _cdt).total_seconds() / 3600
    except Exception:
        pass

hc1.caption(
    f"**{len(players)}** joueurs · **{len(cache)}** contrats · "
    f"**{len(owned)}** pool — "
    f"Stats {fmt_age(stats.get('updated_at'))} · "
    f"Contrats {fmt_age(contracts_db.get('updated_at'))}"
    + (" ⚠️" if _contracts_age_h is not None and _contracts_age_h > 25 else "")
    + f" · Pool {fmt_age(espn_db.get('updated_at'))}"
)

if _contracts_age_h is not None and _contracts_age_h > 25:
    _retry_hint = (
        "Le job GitHub Actions nocturne a probablement échoué ; il réessaiera "
        "la nuit prochaine (ou relance-le manuellement depuis l'onglet Actions)."
        if IS_CLOUD else
        "La récupération PuckPedia a probablement échoué — utilise le bouton "
        "🔄 Contrats pour réessayer."
    )
    st.warning(
        f"⚠️ Les données de contrats ont **{int(_contracts_age_h)} h** — "
        + _retry_hint,
        icon="🔔",
    )
if hc2.button("📊 Stats", width="stretch", help="Mettre à jour les stats NHL"):
    run_stats_update()
if hc3.button("🔄 Contrats", type="primary", width="stretch",
              help="Update All contracts (PuckPedia)"):
    run_full_update()
if hc4.button("🏒 Pool", width="stretch", help="Update pool (ESPN)"):
    run_pool_update()


# ----------------------------------------------------------------------
# Cap Hit total par équipe de pool
# ----------------------------------------------------------------------
def pool_cap_summary():
    totals = {}
    for p in players:
        pool_team, _ = pool_for(p.get("name"))
        if not pool_team:
            continue
        c = contract_for(p.get("playerId"))
        cap = c.get("cap_hit_value", 0) if c else 0
        totals[pool_team] = totals.get(pool_team, 0) + cap
    if not totals:
        return pd.DataFrame()
    return pd.DataFrame(
        [{"Équipe de pool": k, "Cap Hit total": v} for k, v in totals.items()]
    ).sort_values("Cap Hit total", ascending=False)


# ----------------------------------------------------------------------
# Coloration
# ----------------------------------------------------------------------
COLOR_RETIRED = "rgba(200, 0, 0, 0.35)"    # ligne rouge : retraité (manuel)
COLOR_NEW_TEXT = "#159e46"                  # texte vert : recrue/nouveau (manuel)


def row_style(row):
    if row.get("_retired"):
        return [f"background-color: {COLOR_RETIRED}" for _ in row]
    if row.get("_new"):
        return [f"color: {COLOR_NEW_TEXT}; font-weight: 600" for _ in row]
    if row.get("_mine"):
        color = COLOR_MINE
    elif row.get("_target"):
        color = COLOR_TARGET
    elif row.get("_owned"):
        color = COLOR_OTHER
    else:
        color = COLOR_NONE
    return [f"background-color: {color}" if color else "" for _ in row]


# ----------------------------------------------------------------------
# Barre de filtres + application
# ----------------------------------------------------------------------
def status_match(row, status):
    if status == "Tous":
        return True
    if status == "Libres":
        return not row["_owned"]
    if status == "Possédés":
        return row["_owned"]
    if status == "Mon équipe":
        return row["_mine"]
    return True


def apply_filters(df, key_prefix, player_type):
    """Affiche la barre de filtres (repliable) et retourne le DataFrame filtré."""
    with st.expander("🔧 Filtres", expanded=False):
        # Ligne 1 : recherche + NHL Team + Pool Team
        r1 = st.columns([2, 1, 1])
        search = r1[0].text_input("🔎 Nom", key=f"{key_prefix}_search",
                                  placeholder="ex. McDavid")
        nhl_teams = ["Toutes"] + sorted(t for t in df["NHL Team"].dropna().unique())
        nhl_sel = r1[1].selectbox("NHL Team", nhl_teams, key=f"{key_prefix}_nhl")
        pool_opts = ["Toutes"] + sorted(POOL_ABBR.get(p, p)
                                        for p in {v for v in df["_pool_full"] if v})
        pool_sel = r1[2].selectbox("Pool Team", pool_opts, key=f"{key_prefix}_pool")
        # Filtre possession en boutons (renommé « Possession » pour éviter la
        # confusion avec la colonne « Statut » = statut roster du joueur).
        status = st.segmented_control(
            "Possession", ["Tous", "Libres", "Possédés", "Mon équipe"],
            default="Tous", key=f"{key_prefix}_status") or "Tous"

        # Ligne 2 : Position (multi, patineurs) + Âge + GP min + Cap min-max
        cmax_m = (int(df["Cap Hit"].max()) // 1_000_000) if not df["Cap Hit"].dropna().empty else 0
        cmax_m = max(1, cmax_m)
        ages = df["Âge"].dropna()
        amin, amax = (int(ages.min()), int(ages.max())) if not ages.empty else (18, 45)
        gpmax = int(df["GP"].max()) if not df["GP"].dropna().empty else 0
        gpmax = max(1, gpmax)

        if player_type == "skater":
            r2 = st.columns([2, 2, 1, 2])
            pos_all = sorted(p for p in df["Pos"].dropna().unique())
            pos_sel = r2[0].multiselect("Position", pos_all, default=[],
                                        key=f"{key_prefix}_pos")
            age_rng = r2[1].slider("Âge", amin, amax, (amin, amax),
                                   key=f"{key_prefix}_age")
            gp_min = r2[2].slider("GP min", 0, gpmax, 0, key=f"{key_prefix}_gp")
            cap_rng = r2[3].slider("Cap ($M)", 0, cmax_m, (0, cmax_m),
                                   key=f"{key_prefix}_cap")
        else:
            r2 = st.columns([2, 1, 2])
            pos_sel = []
            age_rng = r2[0].slider("Âge", amin, amax, (amin, amax),
                                   key=f"{key_prefix}_age")
            gp_min = r2[1].slider("GP min", 0, gpmax, 0, key=f"{key_prefix}_gp")
            cap_rng = r2[2].slider("Cap ($M)", 0, cmax_m, (0, cmax_m),
                                   key=f"{key_prefix}_cap")

    # Application des filtres
    view = df.copy()
    if search:
        view = view[view["Nom"].str.contains(search, case=False, na=False)]
    if nhl_sel != "Toutes":
        view = view[view["NHL Team"] == nhl_sel]
    if pool_sel != "Toutes":
        view = view[view["Pool Team"] == pool_sel]
    if status != "Tous":
        view = view[view.apply(lambda r: status_match(r, status), axis=1)]
    if pos_sel:
        view = view[view["Pos"].isin(pos_sel)]
    view = view[view["Âge"].fillna(-1).between(age_rng[0], age_rng[1]) | view["Âge"].isna()]
    view = view[view["GP"].fillna(0) >= gp_min]
    view = view[view["Cap Hit"].fillna(0).between(cap_rng[0] * 1_000_000,
                                                  cap_rng[1] * 1_000_000)]
    return view


def apply_sort(view, key_prefix, display_cols, default_col):
    """Barre de tri multi-colonnes : l'ordre de sélection = priorité du tri,
    chaque colonne ayant son propre sens (↓ décroissant / ↑ croissant)."""
    with st.expander("↕️ Tri", expanded=False):
        sort_cols = st.multiselect(
            "Colonnes de tri (l'ordre de sélection définit la priorité)",
            display_cols,
            default=[default_col] if default_col in display_cols else [],
            key=f"{key_prefix}_sortcols",
        )
        ascending = []
        # Un sens de tri par colonne, réparti sur des lignes de 4 pour rester lisible.
        for start in range(0, len(sort_cols), 4):
            chunk = sort_cols[start:start + 4]
            cols = st.columns(len(chunk))
            for i, c in enumerate(chunk):
                key = f"{key_prefix}_dir_{c}"
                st.session_state.setdefault(key, True)  # True = décroissant par défaut
                cols[i].toggle(
                    f"{c} {'↓' if st.session_state[key] else '↑'}",
                    key=key,
                    help="Activé = décroissant (↓), désactivé = croissant (↑)")
                ascending.append(not st.session_state[key])

    active = [(c, a) for c, a in zip(sort_cols, ascending) if c in view.columns]
    if active:
        view = view.sort_values(
            by=[c for c, _ in active],
            ascending=[a for _, a in active],
            na_position="last",
        )
    return view


def render_tab(player_type, key_prefix, sort_col, display_cols):
    df = build_df(player_type)

    # Valeur / Valeur/$M / Tier / Bonus jeunesse : z-scores composites pilotés
    # par les réglages sauvegardés du Pool STM (draft_settings.json).
    cfg = de.load_settings()
    min_gp = int(cfg.get("min_gp", 20))
    youth_w = float(cfg.get("youth_w", 0.15))
    ref_age = int(cfg.get("ref_age", 27))
    if player_type == "skater":
        scored = de.compute_scores(players_with_cap("skater"), "skater",
                                   cfg.get("skater"), min_gp,
                                   youth_weight=youth_w, ref_age=ref_age,
                                   weights_d=cfg.get("def"))
    else:
        scored = de.compute_scores(players_with_cap("goalie"), "goalie",
                                   cfg.get("goalie"), min_gp,
                                   youth_weight=youth_w, ref_age=ref_age)
    de.assign_tiers(scored)
    sc_by_id = {str(p["playerId"]): p for p in scored}
    df["Valeur"] = df["_pid"].map(lambda i: sc_by_id.get(i, {}).get("value"))
    df["Valeur/$M"] = df["_pid"].map(lambda i: sc_by_id.get(i, {}).get("value_per_m"))
    df["Tier"] = df["_pid"].map(lambda i: sc_by_id.get(i, {}).get("tier", "—"))
    df["Bonus jeun."] = df["_pid"].map(lambda i: sc_by_id.get(i, {}).get("youth_bonus"))

    # Dispo + possession. En mode repêchage, on tient compte du marquage manuel
    # (plan) de l'onglet Pool STM ; en mode saison, de la possession ESPN.
    plan = de.load_plan()
    draft_mode = str(st.session_state.get("team_mode", "🏒 Repêchage")).startswith("🏒")

    def _dispo_owner(row):
        """Retourne (Dispo, _mine, _owned, _target).

        En mode repêchage la coloration suit la colonne Dispo (pilotée par le
        plan manuel) et non la possession ESPN : bleu = mes joueurs
        (protégés + ajoutés), jaune pâle = cibles, gris = Autre DG, rien =
        Libre. La colonne « Pool Team » (ESPN) reste affichée à titre
        informatif — un joueur non marqué dans le plan reste « Libre ».
        """
        if draft_mode:
            s = plan.get(row["_pid"])
            if s == "mine":
                return "Moi", True, True, False        # bleu
            if s == "added":
                return "➕ Ajouté", True, True, False    # bleu (mon équipe)
            if s == "other":
                return "Autre DG", False, True, False   # gris
            if s == "target":
                return "🎯 Cible", False, False, True    # jaune pâle
            return "Libre", False, False, False
        # Mode saison : possession ESPN (déjà dans _mine / _owned)
        if row["_mine"]:
            return "Moi", True, True, False
        if row["_owned"]:
            return (row["Pool Team"] if row["Pool Team"] != "—" else "Possédé"), False, True, False
        return "Libre", False, False, False

    if df.empty:
        df["Dispo"] = pd.Series(dtype="object")
        df["_target"] = pd.Series(dtype="bool")
    else:
        _info = df.apply(_dispo_owner, axis=1, result_type="expand")
        df["Dispo"] = _info[0]
        df["_mine"] = _info[1]
        df["_owned"] = _info[2]
        df["_target"] = _info[3]

    view = apply_filters(df, key_prefix, player_type)
    view = apply_sort(view, key_prefix, display_cols, sort_col)

    style_cols = display_cols + ["_mine", "_owned", "_target", "_new",
                                 "_retired"]

    def _int(v):
        return f"{int(v)}" if pd.notna(v) else "—"

    def _float2(v):
        return f"{v:.2f}" if pd.notna(v) else "—"

    fmt = {"Cap Hit": lambda v: f"${v:,.0f}" if v else "—"}
    if "Âge" in display_cols:
        fmt["Âge"] = _int
    for col in ("G", "A", "Pts", "+/-", "PIM", "PPP", "SOG", "HIT"):
        if col in display_cols:
            fmt[col] = _int
    for col in ("Valeur", "Valeur/$M", "Bonus jeun."):
        if col in display_cols:
            fmt[col] = _float2
    styled = (view[style_cols].style
              .apply(row_style, axis=1)
              .format(fmt))

    st.dataframe(
        styled, hide_index=True, width="stretch",
        height=1090,
        column_order=display_cols,
        column_config={"_mine": None, "_owned": None, "_target": None,
                       "_new": None, "_retired": None},
    )
    n_mine = int(view["_mine"].sum())
    n_owned = int(view["_owned"].sum())
    n_ret = int(view["_retired"].sum())
    n_new = int(view["_new"].sum())
    st.caption(f"{len(view)} joueurs — {n_mine} dans mon équipe, "
               f"{n_owned} possédés · {n_new} recrues, {n_ret} retraités")


# ----------------------------------------------------------------------
# Onglet Agents libres & Prospects
# ----------------------------------------------------------------------
def render_fa_tab():
    """Joueurs libres à surveiller + prospects (non possédés dans le pool)."""

    sk_sc = de.compute_scores(players_with_cap("skater"), "skater", min_gp=1,
                               youth_weight=0.15, ref_age=27)
    go_sc = de.compute_scores(players_with_cap("goalie"), "goalie", min_gp=1,
                               youth_weight=0.15, ref_age=27)
    all_sc = de.assign_tiers(sk_sc + go_sc)
    score_by_id = {str(p["playerId"]): p for p in all_sc}

    rows_fa = []
    for p in players:
        c = contract_for(p.get("playerId"))
        pool_team, is_mine = pool_for(p.get("name"))
        sc = score_by_id.get(str(p.get("playerId")), {})
        expiry_year = (c.get("expiry_year") or "") if c else ""
        expiry_status = (c.get("expiry_status") or "") if c else ""
        row = {
            "_owned": pool_team is not None,
            "_mine": is_mine,
            "_new": is_new(p.get("playerId")),
            "_retired": is_retired(p.get("playerId")),
            "_expiry_year": expiry_year,
            "_type": p.get("type", ""),
            "Nom": p["name"],
            "Statut": statut_for(p.get("playerId")),
            "NHL Team": p.get("team", ""),
            "Pool Team": pool_abbr(pool_team) if pool_team else "—",
            "Pos": p.get("position", ""),
            "Âge": int(p["age"]) if p.get("age") is not None else None,
            "GP": p.get("gp") or 0,
            "Cap Hit": (c.get("cap_hit_value") or 0) if c else 0,
            "FA": expiry_status,
            "Expiry": expiry_label(c) if c else "—",
            "Clauses": (c.get("clauses") or "—") if c else "—",
            "Tier": sc.get("tier", "—"),
            "Valeur": sc.get("value"),
            "G": p.get("goals") if p.get("type") == "skater" else None,
            "A": p.get("assists") if p.get("type") == "skater" else None,
            "Pts": p.get("points") if p.get("type") == "skater" else None,
            "+/-": p.get("plus_minus") if p.get("type") == "skater" else None,
            "PPP": p.get("ppp") if p.get("type") == "skater" else None,
            "SOG": p.get("sog") if p.get("type") == "skater" else None,
            "V": p.get("wins") if p.get("type") == "goalie" else None,
            "Moy": p.get("gaa") if p.get("type") == "goalie" else None,
            "%Arr": p.get("sv_pct") if p.get("type") == "goalie" else None,
            "BL": p.get("shutouts") if p.get("type") == "goalie" else None,
        }
        rows_fa.append(row)

    adf = pd.DataFrame(rows_fa)

    def _int_fa(v):
        return f"{int(v)}" if pd.notna(v) else "—"

    _fmt_fa = {
        "Cap Hit": lambda v: f"${v / 1_000_000:.2f}M" if v else "—",
        "Valeur": lambda v: f"{v:.2f}" if pd.notna(v) else "—",
        "Moy": lambda v: f"{v:.2f}" if pd.notna(v) else "—",
        "%Arr": lambda v: f"{v:.3f}" if pd.notna(v) else "—",
        "G": _int_fa, "A": _int_fa, "Pts": _int_fa, "+/-": _int_fa,
        "PIM": _int_fa, "PPP": _int_fa, "SOG": _int_fa, "HIT": _int_fa,
    }

    def _show(df_show, display_cols, height=500):
        if df_show.empty:
            st.info("Aucun joueur avec ces critères.")
            return
        ok_disp = [c for c in display_cols if c in df_show.columns]
        ok_style = ok_disp + [c for c in ("_mine", "_owned", "_new",
                                          "_retired")
                              if c in df_show.columns]
        styled = (df_show[ok_style].style
                  .apply(row_style, axis=1)
                  .format(_fmt_fa, na_rep="—"))
        st.dataframe(styled, hide_index=True, width="stretch", height=height,
                     column_order=ok_disp,
                     column_config={"_mine": None, "_owned": None, "_new": None,
                                    "_retired": None})

    # ------------------------------------------------------------------
    # Section 1 : Agents libres
    # ------------------------------------------------------------------
    st.markdown("### 🆓 Agents libres à surveiller")
    st.caption(
        "Joueurs dont le contrat expire en 2025-2026 ou 2026-2027, "
        "non possédés dans le pool — triés par Valeur (z-scores pondérés)."
    )

    with st.expander("⚙️ Filtres", expanded=True):
        fa1, fa2, fa3, fa4 = st.columns(4)
        saison_opts = sorted(
            {r["_expiry_year"] for r in rows_fa if r["_expiry_year"]}, reverse=True
        )
        default_saisons = [s for s in saison_opts if s in {"2025-2026", "2026-2027"}]
        saisons = fa1.multiselect(
            "Saison d'expiry", saison_opts, default=default_saisons, key="fa_saisons"
        )
        fa_statut = fa2.multiselect(
            "Type FA", ["UFA", "RFA"], default=["UFA", "RFA"], key="fa_statut"
        )
        fa_ptype = fa3.selectbox(
            "Type joueur", ["Tous", "Patineurs", "Gardiens"], key="fa_ptype"
        )
        fa_hide = fa4.checkbox("Masquer possédés", value=True, key="fa_hide")

    mask = pd.Series(True, index=adf.index)
    if saisons:
        mask &= adf["_expiry_year"].isin(saisons)
    if fa_statut:
        mask &= adf["FA"].isin(fa_statut)
    fa_df = adf[mask].copy()
    if fa_hide:
        fa_df = fa_df[~fa_df["_owned"]]
    if fa_ptype == "Patineurs":
        fa_df = fa_df[fa_df["_type"] == "skater"]
    elif fa_ptype == "Gardiens":
        fa_df = fa_df[fa_df["_type"] == "goalie"]
    fa_df = fa_df.sort_values("Valeur", ascending=False, na_position="last")

    if fa_ptype == "Gardiens":
        fa_cols = ["Nom", "Statut", "NHL Team", "Pool Team", "Pos", "Âge", "GP",
                   "Cap Hit", "FA", "Expiry", "Clauses", "Tier", "Valeur",
                   "V", "Moy", "%Arr", "BL"]
    elif fa_ptype == "Patineurs":
        fa_cols = ["Nom", "Statut", "NHL Team", "Pool Team", "Pos", "Âge", "GP",
                   "Cap Hit", "FA", "Expiry", "Clauses", "Tier", "Valeur",
                   "G", "A", "Pts", "+/-", "PPP", "SOG"]
    else:
        fa_cols = ["Nom", "Statut", "NHL Team", "Pool Team", "Pos", "Âge", "GP",
                   "Cap Hit", "FA", "Expiry", "Clauses", "Tier", "Valeur"]

    _show(fa_df, fa_cols, height=min(700, max(150, 45 * len(fa_df) + 40)))
    st.caption(
        f"{len(fa_df)} agents libres "
        f"— {int(fa_df['_owned'].sum()) if not fa_df.empty else 0} déjà possédés"
    )

    st.divider()

    # ------------------------------------------------------------------
    # Section 2 : Prospects
    # ------------------------------------------------------------------
    st.markdown("### 🌱 Prospects à surveiller")
    st.caption(
        "Jeunes joueurs non possédés dans le pool, triés par Valeur "
        "(bonus jeunesse : +0.15/an sous 27 ans inclus)."
    )

    with st.expander("⚙️ Filtres", expanded=True):
        p1, p2, p3, p4 = st.columns(4)
        max_age = p1.slider("Âge maximum", 18, 26, 23, key="pro_max_age")
        pro_ptype = p2.selectbox(
            "Type joueur", ["Tous", "Patineurs", "Gardiens"], key="pro_ptype"
        )
        pro_hide = p3.checkbox("Masquer possédés", value=True, key="pro_hide")
        pro_min_gp = p4.slider("GP minimum", 0, 40, 5, key="pro_min_gp")

    pro_df = adf[
        adf["Âge"].notna() & (adf["Âge"] <= max_age) & (adf["GP"] >= pro_min_gp)
    ].copy()
    if pro_hide:
        pro_df = pro_df[~pro_df["_owned"]]
    if pro_ptype == "Patineurs":
        pro_df = pro_df[pro_df["_type"] == "skater"]
    elif pro_ptype == "Gardiens":
        pro_df = pro_df[pro_df["_type"] == "goalie"]
    pro_df = pro_df.sort_values("Valeur", ascending=False, na_position="last")

    if pro_ptype == "Gardiens":
        pro_cols = ["Nom", "Statut", "NHL Team", "Pool Team", "Pos", "Âge", "GP",
                    "Cap Hit", "FA", "Expiry", "Tier", "Valeur",
                    "V", "Moy", "%Arr", "BL"]
    elif pro_ptype == "Patineurs":
        pro_cols = ["Nom", "Statut", "NHL Team", "Pool Team", "Pos", "Âge", "GP",
                    "Cap Hit", "FA", "Expiry", "Tier", "Valeur",
                    "G", "A", "Pts", "+/-", "PPP", "SOG"]
    else:
        pro_cols = ["Nom", "Statut", "NHL Team", "Pool Team", "Pos", "Âge", "GP",
                    "Cap Hit", "FA", "Expiry", "Tier", "Valeur"]

    _show(pro_df, pro_cols, height=min(700, max(150, 45 * len(pro_df) + 40)))
    st.caption(
        f"{len(pro_df)} prospects (≤ {max_age} ans, ≥ {pro_min_gp} GP) "
        f"— {int(pro_df['_owned'].sum()) if not pro_df.empty else 0} déjà possédés"
    )


# ----------------------------------------------------------------------
# Onglets
# ----------------------------------------------------------------------
tab_s, tab_g, tab_d, tab_c, tab_fa, tab_ret, tab_aide = st.tabs(
    ["⚡ Patineurs", "🥅 Gardiens", "🎯 Pool STM", "🥊 Confrontations",
     "🔍 Agents libres & Prospects", "🛠️ Statut joueurs", "📐 Comment ça marche ?"])

with tab_s:
    render_tab(
        "skater", "sk", "Pts",
        ["Nom", "Statut", "Dispo", "Tier", "NHL Team", "Pool Team", "Pos", "Âge",
         "GP", "Cap Hit", "Valeur", "Valeur/$M", "Bonus jeun.", "Signing",
         "Expiry", "Clauses",
         "G", "A", "Pts", "+/-", "PIM", "PPP", "SOG", "HIT"],
    )

with tab_g:
    render_tab(
        "goalie", "go", "V",
        ["Nom", "Statut", "Dispo", "Tier", "NHL Team", "Pool Team", "Pos", "Âge",
         "GP", "Cap Hit", "Valeur", "Valeur/$M", "Bonus jeun.", "Signing",
         "Expiry", "Clauses",
         "V", "D", "DPr", "Moy", "%Arr", "BL"],
    )


# ----------------------------------------------------------------------
# Onglet Repêchage
# ----------------------------------------------------------------------
DEFAULT_CAP = 102_000_000


def status_label(s):
    return {"mine": "★ Protégé (moi)", "added": "➕ Ajouté",
            "other": "🔒 Protégé (autre DG)",
            "target": "🎯 Cible", None: "—"}.get(s, "—")


def render_draft_tab():
    plan = de.load_plan()   # {player_id: 'mine'/'other'/'target'}

    # --- Réglages (poids, cap, seuil GP) : valeurs effectives ---
    # Les widgets sont rendus en bas de l'onglet (_render_settings_expander).
    # Ici on lit les valeurs courantes depuis st.session_state, initialisées
    # depuis draft_settings.json, afin qu'elles soient disponibles pour le
    # calcul des scores plus bas.
    cfg = de.load_settings()
    _W_DEFAULTS = {
        "skater": {"ppp": 2.0, "plus_minus": 1.5, "hits": 1.25, "goals": 1.0,
                   "assists": 1.0, "pim": 1.0, "sog": 1.0},
        "def": {"ppp": 2.0, "plus_minus": 1.5, "hits": 1.25, "goals": 1.0,
                "assists": 1.0, "pim": 1.0, "sog": 1.0},
        "goalie": {"shutouts": 2.0, "wins": 1.0, "gaa": 1.0, "sv_pct": 1.0},
    }
    st.session_state.setdefault("stm_cap_limit", int(cfg.get("cap_limit", DEFAULT_CAP)))
    st.session_state.setdefault("stm_min_gp", int(cfg.get("min_gp", 20)))
    st.session_state.setdefault("stm_youth_w", float(cfg.get("youth_w", 0.15)))
    st.session_state.setdefault("stm_ref_age", int(cfg.get("ref_age", 27)))
    for _section, _wd in _W_DEFAULTS.items():
        for _cat, _dv in _wd.items():
            st.session_state.setdefault(
                f"stm_w_{_section}_{_cat}",
                float(cfg.get(_section, {}).get(_cat, _dv)))

    def _wdict(section):
        return {cat: st.session_state[f"stm_w_{section}_{cat}"]
                for cat in _W_DEFAULTS[section]}

    cap_limit = st.session_state["stm_cap_limit"]
    min_gp = st.session_state["stm_min_gp"]
    youth_w = st.session_state["stm_youth_w"]
    ref_age = st.session_state["stm_ref_age"]
    ws, ws_d, wg = _wdict("skater"), _wdict("def"), _wdict("goalie")

    # Sauvegarde des réglages si un changement a eu lieu (persistance entre sessions)
    new_cfg = {
        "cap_limit": int(cap_limit), "min_gp": int(min_gp),
        "youth_w": float(youth_w), "ref_age": int(ref_age),
        "skater": ws, "def": ws_d, "goalie": wg,
    }
    if new_cfg != cfg:
        de.save_settings(new_cfg)

    _SK_LABELS = [("ppp", "PPP"), ("plus_minus", "+/-"), ("hits", "HIT"),
                  ("goals", "G"), ("assists", "A"), ("pim", "PIM"), ("sog", "SOG")]
    _GO_LABELS = [("shutouts", "SO"), ("wins", "W"), ("gaa", "GAA"), ("sv_pct", "SV%")]

    def _render_settings_expander():
        with st.expander("⚙️ Réglages (poids, cap, seuil GP)", expanded=False):
            st.number_input("Cap salarial ($)", step=1_000_000, format="%d",
                            key="stm_cap_limit")
            st.slider("GP minimum z-score", 1, 60, key="stm_min_gp")
            cj = st.columns(2)
            cj[0].number_input("Poids jeunesse (bonus/an sous l'âge réf.)",
                               0.0, 2.0, step=0.05, key="stm_youth_w")
            cj[1].number_input("Âge de référence", 20, 35, step=1, key="stm_ref_age")
            st.markdown("**Poids attaquants**")
            w1 = st.columns(len(_SK_LABELS))
            for _i, (_cat, _lbl) in enumerate(_SK_LABELS):
                w1[_i].number_input(_lbl, 0.0, 5.0, step=0.05,
                                    key=f"stm_w_skater_{_cat}")
            st.markdown("**Poids défenseurs**")
            wd_cols = st.columns(len(_SK_LABELS))
            for _i, (_cat, _lbl) in enumerate(_SK_LABELS):
                wd_cols[_i].number_input(_lbl, 0.0, 5.0, step=0.05,
                                         key=f"stm_w_def_{_cat}")
            st.markdown("**Poids gardiens**")
            w2 = st.columns(len(_GO_LABELS))
            for _i, (_cat, _lbl) in enumerate(_GO_LABELS):
                w2[_i].number_input(_lbl, 0.0, 5.0, step=0.05,
                                    key=f"stm_w_goalie_{_cat}")


    # --- Calcul des scores ---
    sk = de.compute_scores(players_with_cap("skater"), "skater", ws, min_gp,
                           youth_weight=youth_w, ref_age=ref_age, weights_d=ws_d)
    go = de.compute_scores(players_with_cap("goalie"), "goalie", wg, min_gp,
                           youth_weight=youth_w, ref_age=ref_age)
    de.assign_tiers(sk)
    de.assign_tiers(go)
    by_id = {str(p["playerId"]): p for p in sk + go}

    # --- Panneau Mon équipe ---
    mine_ids = [pid for pid, s in plan.items() if s == "mine"]
    added_ids = [pid for pid, s in plan.items() if s == "added"]
    target_ids = [pid for pid, s in plan.items() if s == "target"]

    def cap_sum(ids):
        return sum(by_id.get(pid, {}).get("cap_hit_value", 0) for pid in ids)

    # Masse salariale = protégés (moi) + ajoutés. Les cibles NE comptent pas.
    cap_total = cap_sum(mine_ids + added_ids)
    remaining = cap_limit - cap_total

    MAX_PROTECTED = 8

    # --- Assigner un statut ---
    st.markdown("**Assigner un statut à un joueur**")
    search_assign = st.text_input(
        "🔎 Rechercher un joueur", key="draft_assign_search",
        placeholder="Tape un nom pour filtrer la liste…")

    # Liste des joueurs assignables, indépendante du tableau ci-dessous :
    # tous les joueurs scorés + les recrues sans stats.
    assignable = list(by_id.values())
    _seen_assign = {str(p["playerId"]) for p in assignable}
    for _nid in ROOKIE_IDS:
        if _nid in _seen_assign:
            continue
        _pr = PLAYERS_BY_ID.get(_nid)
        if _pr:
            assignable.append(_pr)
    all_names = {f"{p.get('name')} ({p.get('position')})": str(p["playerId"])
                 for p in sorted(assignable, key=lambda x: (x.get("name") or ""))}
    if search_assign:
        names = {k: v for k, v in all_names.items()
                 if search_assign.lower() in k.lower()}
    else:
        names = all_names

    if not names:
        st.info("Aucun joueur ne correspond à la recherche.")
    else:
        a1, a2, a3 = st.columns([3, 2, 1])
        chosen = a1.selectbox("Joueur", list(names.keys()),
                              key="draft_assign_player")
        new_status = a2.selectbox(
            "Statut",
            ["★ Protégé (moi)", "➕ Ajouté", "🔒 Protégé (autre DG)",
             "🎯 Cible", "— (retirer)"],
            key="draft_assign_status")
        status_map = {"★ Protégé (moi)": "mine", "➕ Ajouté": "added",
                      "🔒 Protégé (autre DG)": "other",
                      "🎯 Cible": "target", "— (retirer)": None}
        if a3.button("Appliquer", width="stretch", key="draft_apply"):
            target_pid = names[chosen]
            target_status = status_map[new_status]
            if (target_status == "mine"
                    and plan.get(target_pid) != "mine"
                    and len(mine_ids) >= MAX_PROTECTED):
                st.error(f"⚠️ Maximum {MAX_PROTECTED} protégés atteint. "
                         "Retire un protégé avant d'en ajouter un autre.")
            else:
                de.set_status(target_pid, target_status)
                st.rerun()

    st.divider()

    # --- Mode d'affichage (contrôlé globalement par le radio de la barre
    # latérale, clé « team_mode ») ---
    st.subheader("📋 Sommaire de mon équipe")
    draft_mode = str(st.session_state.get("team_mode", "🏒 Repêchage")).startswith("🏒")

    # --- Compteur de slots par position (Protégés + Cibles) ---
    SLOTS = {"F": 7, "D": 4, "G": 2}

    def pos_group(pos):
        pos = (pos or "").upper()
        if pos == "G":
            return "G"
        if pos == "D":
            return "D"
        return "F"

    counts = {"F": 0, "D": 0, "G": 0}
    for pid in mine_ids + added_ids + target_ids:
        p = by_id.get(pid, {})
        counts[pos_group(p.get("position"))] += 1
    total_sel = counts["F"] + counts["D"] + counts["G"]
    starters = sum(min(counts[g], SLOTS[g]) for g in SLOTS)
    bench = max(0, total_sel - starters)

    # Ligne unique compacte : protégés/cibles/cap + slots
    over = " ⚠️" if len(mine_ids) > MAX_PROTECTED else ""
    cap_color = "red" if remaining < 0 else "gray"
    st.markdown(
        f"<div style='font-size:0.9rem;line-height:1.6'>"
        f"<b>Protégés:</b> {len(mine_ids)}/{MAX_PROTECTED}{over} · "
        f"<b>Ajoutés:</b> {len(added_ids)} · "
        f"<b>Cibles:</b> {len(target_ids)} · "
        f"<b>F:</b> {counts['F']}/{SLOTS['F']} · "
        f"<b>D:</b> {counts['D']}/{SLOTS['D']} · "
        f"<b>G:</b> {counts['G']}/{SLOTS['G']} · "
        f"<b>Banc:</b> {bench}  &nbsp;|&nbsp;  "
        f"<b>Cap:</b> ${cap_total:,.0f} / ${cap_limit:,.0f} · "
        f"<span style='color:{cap_color}'><b>Restant:</b> ${remaining:,.0f}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if remaining < 0:
        st.error(f"⚠️ Dépassement du cap de ${-remaining:,.0f}")
    if len(mine_ids) > MAX_PROTECTED:
        st.error(f"⚠️ Trop de protégés ({len(mine_ids)}/{MAX_PROTECTED}). "
                 "Retire-en avant le repêchage.")

    # liste des joueurs marqués (repliable — pertinent au repêchage)
    marked = [(pid, s) for pid, s in plan.items()
              if s in ("mine", "added", "target")]
    if marked:
        with st.expander("📑 Mes joueurs au repêchage (protégés + ajoutés + cibles)",
                         expanded=False):
            rows = []
            for pid, s in marked:
                p = by_id.get(pid, {})
                rows.append({
                    "Statut": status_label(s),
                    "Nom": p.get("name", pid),
                    "Pos": p.get("position"),
                    "Cap Hit": p.get("cap_hit_value", 0),
                    "Valeur": p.get("value"),
                    "Valeur/$M": p.get("value_per_m"),
                })
            mdf = pd.DataFrame(rows).sort_values("Statut")
            st.dataframe(mdf, hide_index=True, width="stretch",
                         column_config={
                             "Cap Hit": st.column_config.NumberColumn(
                                 "Cap Hit", format="$%d"),
                             "Valeur": st.column_config.NumberColumn(
                                 "Valeur", format="%.2f"),
                             "Valeur/$M": st.column_config.NumberColumn(
                                 "Valeur/$M", format="%.2f")})

    st.divider()

    # --- Mon équipe actuelle (ESPN) : décider Garder / Laisser ---
    _exp_team = st.expander("🏒 Mon équipe ESPN", expanded=True)
    with _exp_team:
        # Joueurs ESPN à moi, reliés à leur playerId NHL via le nom normalisé
        my_espn_norm = {k for k, v in owned.items() if v.get("is_mine")}
        my_current = []
        for p in players:
            if norm_name(p.get("name")) in my_espn_norm:
                my_current.append(p)

        if not my_current:
            st.info("Aucun joueur ESPN trouvé. Clique sur « Update pool (ESPN) » "
                    "dans l'en-tête, ou la jointure par nom n'a pas matché.")
        else:
            st.caption(f"{len(my_current)} joueurs dans ton équipe ESPN. "
                       "Garde (= Protégé) ou laisse (= retourne au repêchage).")

            lineup = de.load_lineup()
            n_protected = len(mine_ids)

            st.caption(f"{len(my_current)} joueurs. Édite **Align.** (On ice/Bench) "
                       "et **Gardé** directement dans le tableau. "
                       f"Max {MAX_PROTECTED} protégés.")

            def pos_rank(p):
                pos = (p.get("position") or "").upper()
                if pos == "G":
                    return 3
                if pos == "D":
                    return 2
                return 1

            def build_editable(subset_type, cols_stats):
                rws = []
                for p in sorted(my_current,
                                key=lambda x: (pos_rank(x), x.get("name", ""))):
                    if p.get("type") != subset_type:
                        continue
                    pid = str(p.get("playerId"))
                    info = by_id.get(pid, {})
                    base = {
                        "_id": pid,
                        "On ice": lineup.get(pid) == "ice",
                        "Gardé": plan.get(pid) == "mine",
                        "Tier": info.get("tier", "—"),
                        "Nom": p.get("name"),
                        "Statut": statut_for(pid),
                        "Pool Team": pool_abbr(
                            pool_for(p.get("name"))[0]) if pool_for(p.get("name"))[0] else "—",
                        "Pos": p.get("position"),
                        "Âge": int(p["age"]) if p.get("age") is not None else None,
                        "Cap Hit": info.get("cap_hit_value") or None,
                        "GP": p.get("gp"),
                        "Valeur": info.get("value"),
                        "Valeur/$M": info.get("value_per_m"),
                    }
                    for c, src in cols_stats:
                        base[c] = p.get(src)
                    rws.append(base)
                return pd.DataFrame(rws)

            sk_stats = [("G","goals"),("A","assists"),("Pts","points"),
                        ("+/-","plus_minus"),("PIM","pim"),("PPP","ppp"),
                        ("SOG","sog"),("HIT","hits")]
            go_stats = [("V","wins"),("D","losses"),("DPr","ot_losses"),
                        ("Moy","gaa"),("%Arr","sv_pct"),("BL","shutouts")]

            def table_height(n):
                return (n + 1) * 35 + 3

            def col_cfg(stat_cols):
                cfg = {
                    "_id": None,
                    "On ice": st.column_config.CheckboxColumn("🟢 On ice"),
                    "Gardé": st.column_config.CheckboxColumn(
                        "★ Gardé", help="Protéger ce joueur (max 8)",
                        disabled=not draft_mode),
                    "Cap Hit": st.column_config.NumberColumn(
                        "Cap Hit", format="$%d"),
                    "Âge": st.column_config.NumberColumn("Âge", format="%d"),
                    "Valeur": st.column_config.NumberColumn("Valeur", format="%.2f"),
                    "Valeur/$M": st.column_config.NumberColumn(
                        "Valeur/$M", format="%.2f"),
                }
                for col in ("G", "A", "Pts", "+/-", "PIM", "PPP", "SOG", "HIT"):
                    if col in stat_cols:
                        cfg[col] = st.column_config.NumberColumn(col, format="%d")
                return cfg

            def handle_edits(original, edited, label):
                """Compare et applique les changements On ice / Gardé."""
                changed = False
                for i in range(len(edited)):
                    pid = original.iloc[i]["_id"]
                    # Align
                    new_ice = bool(edited.iloc[i]["On ice"])
                    old_ice = bool(original.iloc[i]["On ice"])
                    if new_ice != old_ice:
                        de.set_lineup(pid, "ice" if new_ice else "bench")
                        changed = True
                    # Gardé (seulement en mode repêchage)
                    if draft_mode:
                        new_keep = bool(edited.iloc[i]["Gardé"])
                        old_keep = bool(original.iloc[i]["Gardé"])
                        if new_keep != old_keep:
                            if new_keep:
                                # respecte la limite de 8
                                if len([s for s in de.load_plan().values()
                                        if s == "mine"]) >= MAX_PROTECTED:
                                    st.warning(f"Max {MAX_PROTECTED} protégés atteint "
                                               f"({label}).")
                                else:
                                    de.set_status(pid, "mine")
                                    changed = True
                            else:
                                de.set_status(pid, None)
                                de.set_lineup(pid, None)
                                changed = True
                return changed

            order_cols = ["On ice", "Gardé", "Tier", "Nom", "Statut", "Pool Team",
                          "Pos", "Âge", "Cap Hit", "GP", "Valeur", "Valeur/$M"]

            sk_df = build_editable("skater", sk_stats)
            if not sk_df.empty:
                st.markdown("**🏒 Patineurs**")
                ed = st.data_editor(
                    sk_df, hide_index=True, width="stretch",
                    height=table_height(len(sk_df)),
                    column_order=order_cols + [c[0] for c in sk_stats],
                    column_config=col_cfg([c[0] for c in sk_stats]),
                    disabled=["Tier", "Nom", "Statut", "Pool Team", "Pos", "Âge",
                              "Cap Hit", "GP", "Valeur", "Valeur/$M"] + [c[0] for c in sk_stats],
                    key="edit_sk",
                )
                if handle_edits(sk_df, ed, "patineurs"):
                    st.rerun()

            go_df = build_editable("goalie", go_stats)
            if not go_df.empty:
                st.markdown("**🥅 Gardiens**")
                ed = st.data_editor(
                    go_df, hide_index=True, width="stretch",
                    height=table_height(len(go_df)),
                    column_order=order_cols + [c[0] for c in go_stats],
                    column_config=col_cfg([c[0] for c in go_stats]),
                    disabled=["Tier", "Nom", "Statut", "Pool Team", "Pos", "Âge",
                              "Cap Hit", "GP", "Valeur", "Valeur/$M"] + [c[0] for c in go_stats],
                    key="edit_go",
                )
                if handle_edits(go_df, ed, "gardiens"):
                    st.rerun()

    st.divider()

    # --- Cap Hit total par équipe de pool ---
    with st.expander("💰 Cap Hit total par équipe de pool", expanded=False):
        cap_df = pool_cap_summary()
        if cap_df.empty:
            st.info("Aucune donnée de pool. Clique sur « 🏒 Pool » dans l'en-tête.")
        else:
            st.dataframe(
                cap_df, hide_index=True, width="stretch",
                column_config={
                    "Cap Hit total": st.column_config.NumberColumn(
                        "Cap Hit total", format="$%d")
                },
            )

    st.divider()

    _render_settings_expander()


with tab_d:
    render_draft_tab()


# ----------------------------------------------------------------------
# Onglet Confrontations (forces/faiblesses des DG)
# ----------------------------------------------------------------------
def all_players_with_cap():
    out = []
    for p in players:
        q = dict(p)
        c = contract_for(p.get("playerId"))
        q["cap_hit_value"] = c.get("cap_hit_value", 0) if c else 0
        out.append(q)
    return out


CONF_CATS = [c[0] for c in de.SKATER_CATS] + [c[0] for c in de.GOALIE_CATS]


def render_conf_tab():
    if not owned:
        st.info("Aucune donnée de pool. Clique sur « 🏒 Pool » dans l'en-tête "
                "pour récupérer les rosters ESPN.")
        return

    min_gp = st.slider("GP minimum par joueur", 1, 60, 20, key="conf_gp")
    teams, is_mine = de.aggregate_by_team(all_players_with_cap(), owned, min_gp)
    if not teams:
        st.info("Pas assez de données. Vérifie le seuil GP ou lance les updates.")
        return

    # nom de mon équipe
    my_team = next((t for t, m in is_mine.items() if m), None)

    # --- Classement des DG par Valeur et Valeur/$M ---
    # On agrège la Valeur (z-score) et le cap de chaque DG via les joueurs scorés.
    sk_sc = de.compute_scores(players_with_cap("skater"), "skater", min_gp=min_gp)
    go_sc = de.compute_scores(players_with_cap("goalie"), "goalie", min_gp=min_gp)
    score_by_id = {str(p["playerId"]): p for p in sk_sc + go_sc}

    dg_value = {}   # team -> somme des Valeurs
    dg_cap = {}     # team -> somme des caps
    for p in players:
        entry = owned.get(norm_name(p.get("name")))
        if not entry:
            continue
        team = entry.get("pool_team")
        sc = score_by_id.get(str(p.get("playerId")))
        if not sc:
            continue
        dg_value[team] = dg_value.get(team, 0.0) + (sc.get("value") or 0)
        c = contract_for(p.get("playerId"))
        dg_cap[team] = dg_cap.get(team, 0) + (c.get("cap_hit_value", 0) if c else 0)

    if dg_value:
        rank_val = sorted(dg_value.items(), key=lambda x: x[1], reverse=True)
        # Valeur par million de cap (efficience), plancher cap 1M
        dg_vpm = {t: dg_value[t] / (max(dg_cap.get(t, 0), 1_000_000) / 1_000_000)
                  for t in dg_value}
        rank_vpm = sorted(dg_vpm.items(), key=lambda x: x[1], reverse=True)

        def fmt_team(t):
            return t + (" ★" if is_mine.get(t) else "")

        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown("**🏆 Classement par Valeur totale**")
            best = rank_val[0]; worst = rank_val[-1]
            st.success(f"Meilleur : {fmt_team(best[0])} ({best[1]:.1f})")
            st.error(f"Pire : {fmt_team(worst[0])} ({worst[1]:.1f})")
            my_rank = next((i + 1 for i, (t, _) in enumerate(rank_val)
                            if is_mine.get(t)), None)
            if my_rank:
                st.caption(f"Mon rang : {my_rank}/{len(rank_val)}")
        with cc2:
            st.markdown("**💲 Classement par Valeur/$M (efficience)**")
            best = rank_vpm[0]; worst = rank_vpm[-1]
            st.success(f"Meilleur : {fmt_team(best[0])} ({best[1]:.2f})")
            st.error(f"Pire : {fmt_team(worst[0])} ({worst[1]:.2f})")
            my_rank = next((i + 1 for i, (t, _) in enumerate(rank_vpm)
                            if is_mine.get(t)), None)
            if my_rank:
                st.caption(f"Mon rang : {my_rank}/{len(rank_vpm)}")

        with st.expander("Voir le classement complet des DG", expanded=False):
            rrows = []
            for t in dg_value:
                rrows.append({
                    "DG": fmt_team(t),
                    "Valeur totale": round(dg_value[t], 1),
                    "Cap total": dg_cap.get(t, 0),
                    "Valeur/$M": round(dg_vpm[t], 2),
                })
            rdf = pd.DataFrame(rrows).sort_values("Valeur totale", ascending=False)
            st.dataframe(rdf, hide_index=True, width="stretch",
                         column_config={"Cap total": st.column_config.NumberColumn(
                             "Cap total", format="$%d")})

    st.divider()

    # --- Tableau croisé DG x catégories ---
    st.subheader("📊 Forces & faiblesses par DG")
    st.caption("Stats projetées sur 82 matchs, cumulées par équipe. "
               "Vert = fort dans la catégorie, rouge = faible. "
               "GAA : plus bas = mieux.")

    rows = []
    for team, cats in teams.items():
        row = {"DG": team + (" ★" if is_mine.get(team) else "")}
        for cat in CONF_CATS:
            row[cat] = cats.get(cat)
        rows.append(row)
    cdf = pd.DataFrame(rows).set_index("DG")

    # Coloration par catégorie (vert haut / rouge bas), GAA inversé
    def color_col(s):
        cat = s.name
        if cat not in de.CAT_DIRECTION:
            return ["" for _ in s]
        higher = de.CAT_DIRECTION[cat]
        vals = s.astype(float)
        vmin, vmax = vals.min(), vals.max()
        if vmin == vmax:
            return ["" for _ in s]
        out = []
        for v in vals:
            if pd.isna(v):
                out.append("")
                continue
            frac = (v - vmin) / (vmax - vmin)
            if not higher:
                frac = 1 - frac          # GAA : bas = bon
            # vert (bon) -> rouge (faible)
            r = int(255 * (1 - frac))
            g = int(180 * frac)
            out.append(f"background-color: rgba({r},{g},80,0.45)")
        return out

    styled = cdf.style.apply(color_col, axis=0).format(precision=1)
    st.dataframe(styled, width="stretch", height=420)

    st.divider()

    # --- Vue Moi vs un DG ---
    st.subheader("🥊 Moi vs un adversaire")
    if not my_team:
        st.warning("Ton équipe n'est pas identifiée (vérifie ESPN_TEAM_ID).")
        return
    others = [t for t in teams if t != my_team]
    opp = st.selectbox("Adversaire", others, key="conf_opp")

    mine_cats = teams.get(my_team, {})
    opp_cats = teams.get(opp, {})

    comp_rows = []
    my_wins = opp_wins = 0
    for cat in CONF_CATS:
        mv = mine_cats.get(cat)
        ov = opp_cats.get(cat)
        if mv is None or ov is None:
            winner = "—"
        else:
            higher = de.CAT_DIRECTION[cat]
            if mv == ov:
                winner = "="
            elif (mv > ov) == higher:
                winner = "moi"; my_wins += 1
            else:
                winner = "adv"; opp_wins += 1
        comp_rows.append({"Catégorie": cat, my_team: mv, opp: ov,
                          "Gagnant": winner})
    comp = pd.DataFrame(comp_rows).set_index("Catégorie")

    def hl(row):
        w = row["Gagnant"]
        styles = ["", "", ""]
        if w == "moi":
            styles[0] = "background-color: rgba(0,180,80,0.5)"
        elif w == "adv":
            styles[1] = "background-color: rgba(0,180,80,0.5)"
        return styles

    styled2 = (comp[[my_team, opp, "Gagnant"]].style
               .apply(hl, axis=1).format(precision=1, subset=[my_team, opp]))
    st.dataframe(styled2, width="stretch", height=440)

    m1, m2 = st.columns(2)
    m1.metric(f"{my_team} (moi)", f"{my_wins} cat.")
    m2.metric(opp, f"{opp_wins} cat.")
    if my_wins > opp_wins:
        st.success(f"Tu gagnerais cette confrontation {my_wins}–{opp_wins}")
    elif opp_wins > my_wins:
        st.error(f"Tu perdrais cette confrontation {my_wins}–{opp_wins}")
    else:
        st.info(f"Égalité {my_wins}–{opp_wins}")


with tab_c:
    render_conf_tab()

with tab_fa:
    render_fa_tab()


# ----------------------------------------------------------------------
# Onglet Statut joueurs — overrides MANUELS (retraités / recrues)
# ----------------------------------------------------------------------
STATUS_LABELS = {"retired": "🔴 Retraité", "rookie": "🟢 Recrue"}
_STATUS_OPTS = ["retired", "rookie"]


def render_status_tab():
    st.markdown("### 🛠️ Statut manuel des joueurs")
    st.caption(
        "Marque un joueur **retraité** (ligne rouge, retiré des tableaux actifs) "
        "ou **recrue** (nouveau joueur absent des stats, injecté comme jouable). "
        "Le statut manuel **prime toujours** sur les données auto."
    )
    if ps.is_official():
        st.success("🌐 Mode **officiel** : tes changements sont persistés "
                   "(commit vers GitHub) et survivent aux redémarrages.")
    elif ps.is_cloud():
        # Sur le Cloud SANS token : la persistance ne marche pas et les modifs
        # seraient perdues au prochain redémarrage. On le signale clairement.
        st.error(
            "⚠️ **Persistance désactivée** — tu es sur le web mais le secret "
            "`GITHUB_TOKEN` est absent. Tes changements seront **perdus** au "
            "prochain redémarrage. Ajoute `GITHUB_TOKEN` dans "
            "**Settings → Secrets** de l'app Streamlit Cloud pour activer le "
            "mode officiel."
        )
    else:
        st.info("🧪 Mode **test local** : tes changements vont dans "
                "`player_status.local.json` (gitignoré) et ne touchent pas la "
                "référence en ligne.")

    status_data = ps.load_status()
    by_id = {str(p.get("playerId")): p for p in players}

    def _name_of(pid, e):
        p = by_id.get(pid, {})
        c = cache.get(pid, {})
        return e.get("name") or p.get("name") or c.get("name") or pid

    # ---------------- Overrides actuels ----------------
    st.markdown("#### Joueurs avec un statut manuel")
    if not status_data:
        st.info("Aucun statut manuel pour le moment. Ajoute un joueur ci-dessous.")
    else:
        for pid in sorted(status_data, key=lambda k: _name_of(k, status_data[k]).lower()):
            e = status_data[pid]
            p = by_id.get(pid, {})
            c = cache.get(pid, {})
            name = _name_of(pid, e)
            pos = e.get("position") or p.get("position") or c.get("pos") or "—"
            team = e.get("team") or p.get("team") or "—"
            col1, col2, col3 = st.columns([4, 3, 1])
            col1.markdown(f"**{name}** · {pos} · {team}")
            cur = e.get("status")
            new = col2.selectbox(
                "Statut", _STATUS_OPTS,
                index=_STATUS_OPTS.index(cur) if cur in _STATUS_OPTS else 0,
                format_func=lambda s: STATUS_LABELS[s],
                key=f"st_sel_{pid}", label_visibility="collapsed")
            if new != cur:
                ps.set_status(pid, new)
                st.rerun()
            if col3.button("🗑️", key=f"st_del_{pid}",
                           help="Retirer le statut manuel"):
                ps.set_status(pid, None)
                st.rerun()
        n_ret = sum(1 for e in status_data.values() if e.get("status") == "retired")
        n_rk = sum(1 for e in status_data.values() if e.get("status") == "rookie")
        st.caption(f"{n_ret} retraités · {n_rk} recrues")

    st.divider()

    # ---------------- Ajouter un joueur ----------------
    st.markdown("#### ➕ Ajouter un joueur")
    add_mode = st.radio(
        "Source", ["Chercher un joueur connu", "Saisie libre (recrue absente)"],
        horizontal=True, key="st_add_mode", label_visibility="collapsed")

    if add_mode == "Chercher un joueur connu":
        # Joueurs des stats + joueurs sous contrat (recrues potentielles absentes
        # des stats). Le manuel écrase de toute façon les données auto.
        known = {}
        for p in players:
            if p.get("name"):
                known[f"{p['name']} ({p.get('position') or '?'})"] = str(p.get("playerId"))
        _seen = set(known.values())
        for cid, cinfo in cache.items():
            if cinfo.get("name") and cid not in _seen:
                known[f"{cinfo['name']} ({cinfo.get('pos') or '?'}) · contrat"] = cid
        q = st.text_input("🔎 Rechercher un joueur", key="st_add_search",
                          placeholder="Tape un nom…")
        opts = {k: v for k, v in known.items() if q.lower() in k.lower()} if q else {}
        if q and not opts:
            st.info("Aucun joueur ne correspond.")
        elif opts:
            c1, c2, c3 = st.columns([3, 2, 1])
            chosen = c1.selectbox("Joueur", sorted(opts), key="st_add_known")
            status = c2.selectbox("Statut", _STATUS_OPTS,
                                  format_func=lambda s: STATUS_LABELS[s],
                                  key="st_add_known_status")
            if c3.button("Ajouter", width="stretch", key="st_add_known_btn"):
                ps.set_status(opts[chosen], status)
                st.rerun()
    else:
        st.caption("Pour un joueur **absent** des stats ET des contrats "
                   "(ex. recrue tout juste arrivée). Il sera injecté comme jouable.")
        c1, c2, c3 = st.columns([3, 1, 1])
        f_name = c1.text_input("Nom", key="st_free_name",
                               placeholder="Ex. Connor Bedard")
        f_pos = c2.selectbox("Pos", ["C", "LW", "RW", "D", "G"], key="st_free_pos")
        f_team = c3.text_input("Équipe", key="st_free_team", placeholder="Ex. CHI")
        f_status = st.selectbox("Statut", ["rookie", "retired"],
                                format_func=lambda s: STATUS_LABELS[s],
                                key="st_free_status")
        if st.button("Ajouter", key="st_free_btn"):
            if not f_name.strip():
                st.warning("Entre un nom.")
            else:
                # Id synthétique stable (pas de playerId NHL pour un joueur libre).
                pid = "manual:" + (norm_name(f_name).replace(" ", "-") or "joueur")
                ps.set_status(pid, f_status, name=f_name.strip(), position=f_pos,
                              team=f_team.strip() or None)
                st.rerun()


with tab_ret:
    render_status_tab()


# ----------------------------------------------------------------------
# Onglet Aide — explication du z-score
# ----------------------------------------------------------------------
def render_aide_tab():
    st.header("Comment fonctionne le score de valeur ?")

    st.markdown("""
Le score affiché dans les colonnes **Valeur** et **Valeur/$M** est un **z-score composite**.
Voici ce que ça veut dire et comment c'est calculé.
""")

    st.subheader("1. C'est quoi un z-score ?")
    st.markdown("""
Un z-score mesure **à combien d'écarts-types de la moyenne** se trouve un joueur pour une
statistique donnée. Il répond à la question : *est-ce que ce joueur est vraiment meilleur
que la moyenne, et de combien ?*

**Formule :**
""")
    st.latex(r"z = \frac{x - \mu}{\sigma}")
    st.markdown("""
- **x** = la statistique brute du joueur (ex. 35 buts)
- **μ** (mu) = la moyenne de tous les joueurs scorés
- **σ** (sigma) = l'écart-type de tous les joueurs scorés

Un z-score de **+2** signifie que le joueur est 2 écarts-types *au-dessus* de la moyenne
— il fait partie du top ~2 % pour cette stat.
Un z-score de **−1** signifie qu'il est 1 écart-type *en-dessous*.
""")

    st.subheader("2. Exemple concret — les buts")

    data_exemple = {
        "Joueur": ["McDavid", "Girard", "Joueur moyen", "4e trio"],
        "Buts (x)": [64, 12, 24, 6],
        "Moyenne μ": [24, 24, 24, 24],
        "Écart-type σ": [14, 14, 14, 14],
        "z-score buts": ["+2.86", "−0.86", "0.00", "−1.29"],
    }
    st.dataframe(data_exemple, hide_index=True, use_container_width=False)

    st.markdown("""
McDavid à +2.86 est nettement au-dessus ; le joueur moyen est exactement à 0 ;
le 4e trio à −1.29 est sous la moyenne.
""")

    st.subheader("3. Pourquoi combiner plusieurs stats ?")
    st.markdown("""
On calcule un z-score pour **chaque catégorie** (buts, passes, PPP, hits, +/−, etc.),
puis on les **pondère** et on les **additionne** pour obtenir un score unique.

**Exemple avec 3 catégories (poids égaux = 1) :**

| Stat | z-score | Poids |
|------|---------|-------|
| Buts | +1.5    | × 1   |
| PPP  | +2.0    | × 2   |
| Hits | −0.5    | × 1   |

**Score composite = (1.5 × 1) + (2.0 × 2) + (−0.5 × 1) = 6.5**

Les poids permettent de donner plus d'importance aux catégories qui comptent davantage
dans ton pool (ex. PPP pondéré à 2× dans l'onglet *Pool STM*).
""")

    st.subheader("4. Le seuil GP minimum z-score")
    st.markdown("""
Seuls les joueurs ayant atteint le **GP minimum** fixé entrent dans le calcul de la
moyenne (μ) et de l'écart-type (σ).

**Pourquoi ?** Un joueur qui a joué 3 matchs peut afficher 2 buts en 3 GP
(= 0.67 buts/match), ce qui semblerait fantastique mais n'est pas représentatif.
En l'excluant, on évite qu'il fausse la moyenne de référence et le classement.

> Réglage par défaut : **20 GP**. Tu peux l'ajuster dans l'onglet *Pool STM*
> ou *Confrontations* selon la phase de saison.
""")

    st.subheader("5. Valeur/$M — l'efficacité salariale")
    st.markdown("""
La colonne **Valeur/$M** divise simplement le score composite par le cap hit du joueur
en millions de dollars :

""")
    st.latex(r"\text{Valeur/\$M} = \frac{\text{Score composite}}{\text{Cap hit (M\$)}}")
    st.markdown("""
Un joueur à **score 8** avec un cap de **2 M$** donne Valeur/$M = **4.0**.
Un joueur à **score 12** avec un cap de **9 M$** donne Valeur/$M = **1.3**.

Le premier est bien meilleur *marché* même s'il a un score brut plus faible.
C'est l'indicateur clé pour juger les contrats dans ton pool.
""")


with tab_aide:
    render_aide_tab()