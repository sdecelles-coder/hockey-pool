# espn_roster.py
"""Récupère les rosters du pool ESPN Fantasy Hockey et associe
chaque joueur NHL au manager (équipe de pool) qui le possède.

Jointure ESPN <-> stats NHL : par NOM normalisé (les id ESPN
diffèrent des playerId NHL).

Cookies/IDs lus depuis le fichier .env (jamais versionné).
Résultat mis en cache dans espn_owned.json.
"""

import json
import re
import unicodedata
from datetime import datetime, timezone

import requests
import urllib3
import config

OWNED_FILE = "espn_owned.json"  # legacy (saison non précisée)
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Slots d'alignement ESPN hockey HORS partants : 7 = banc (BE), 8 = IR.
# Tout autre slot (0-6 : C/AG/AD/F/D/G/Util) = joueur « on ice » (alignement
# partant). Sert à dériver le statut On ice de la colonne « Mon équipe ESPN ».
BENCH_IR_SLOTS = {7, 8}

# Correspondance stat ID ESPN -> catégorie du pool (mêmes libellés que
# draft_engine.SKATER_CATS/GOALIE_CATS). Déterminé empiriquement sur la ligue
# (voir docstring _parse_schedule) ; stable d'une saison à l'autre. Sert à
# traduire le vrai résultat H2H (cumulativeScore.scoreByStat) en catégories.
ESPN_STAT_TO_CAT = {
    13: "G", 14: "A", 15: "+/-", 17: "PIM", 38: "PPP", 29: "SOG", 31: "HIT",
    1: "W", 7: "SO", 10: "GAA", 11: "SV%",
}

# injuryStatus considérés comme « en santé » (tout le reste = blessé).
HEALTHY_STATUSES = {"ACTIVE", "NORMAL", "", None}


def owned_file(season=None):
    """Chemin du cache roster pour une saison ESPN donnée.

    Un fichier par saison (`espn_owned_2026.json`, `espn_owned_2027.json`…) afin
    que le mode Repêchage (ancienne saison) et le mode Saison (nouvelle) ne
    s'écrasent pas. `espn*.json` est gitignoré : ces caches sont refetchés à
    chaque ouverture, jamais versionnés.
    """
    if season is None:
        return OWNED_FILE
    return f"espn_owned_{season}.json"


def norm_name(name):
    """Normalise un nom pour la jointure : minuscules, sans accents/ponctuation."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[.'-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fetch_rosters(season=None):
    """Retourne (owned, my_team_id, schedule).

    `owned` = {nom_normalisé: {'pool_team', 'is_mine', 'espn_name',
    'lineup_slot', 'on_ice'}} ; `schedule` = calendrier H2H (voir
    _parse_schedule) pour dériver l'adversaire de la semaine.

    `season` : année ESPN (ex. 2026). Si None, on retombe sur le secret/env
    `ESPN_SEASON` (override manuel optionnel, sinon la dérivation auto de l'app
    via seasons.py doit fournir l'année).
    """
    league_id = config.get("ESPN_LEAGUE_ID")
    season    = season if season is not None else config.get("ESPN_SEASON")
    my_team_id = int(config.get("ESPN_TEAM_ID", "0"))
    swid      = config.get("ESPN_SWID")
    espn_s2   = config.get("ESPN_S2")
    verify_ssl = config.get("VERIFY_SSL", "true").lower() == "true"

    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    if not (league_id and swid and espn_s2):
        raise RuntimeError(
            "Configuration ESPN manquante. Vérifie le fichier .env "
            "(ESPN_LEAGUE_ID, ESPN_SWID, ESPN_S2)."
        )
    if not season:
        raise RuntimeError(
            "Saison ESPN indéterminée : passe `season=` ou définis ESPN_SEASON."
        )

    base = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/fhl"
            f"/seasons/{season}/segments/0/leagues/{league_id}")
    cookies = {"SWID": swid, "espn_s2": espn_s2}

    r = requests.get(base, params={"view": ["mTeam", "mRoster", "mMatchup"]},
                     cookies=cookies, headers=HEADERS, timeout=20, verify=verify_ssl)
    r.raise_for_status()
    data = r.json()

    owned = {}
    teams_by_id = {}
    for team in data.get("teams", []):
        tid = team.get("id")
        tname = team.get("name") or f"{team.get('location','')} {team.get('nickname','')}".strip()
        teams_by_id[str(tid)] = tname
        is_mine = tid == my_team_id
        for entry in team.get("roster", {}).get("entries", []):
            player = entry.get("playerPoolEntry", {}).get("player", {})
            full = player.get("fullName", "")
            key = norm_name(full)
            if key:
                slot = entry.get("lineupSlotId")
                inj_status = player.get("injuryStatus")
                owned[key] = {
                    "pool_team": tname,
                    "is_mine": is_mine,
                    "espn_name": full,
                    "lineup_slot": slot,
                    "on_ice": slot not in BENCH_IR_SLOTS,
                    "injury_status": inj_status,
                    "injured": bool(player.get("injured"))
                                or inj_status not in HEALTHY_STATUSES,
                }

    schedule = _parse_schedule(data, teams_by_id)
    return owned, int(config.get("ESPN_TEAM_ID", "0")), schedule


def _finite(v):
    """Nombre fini ou None (ESPN renvoie Infinity/NaN en début de saison : GAA
    sur 0 match). Évite d'écrire du JSON invalide (`Infinity`) dans le log."""
    import math
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _team_cat_results(side):
    """{cat: {'value', 'result'}} pour un côté (home/away) d'un matchup.

    `side.cumulativeScore.scoreByStat` mappe un stat ID ESPN -> {result, score}.
    On ne garde que les stats correspondant à une catégorie du pool.
    """
    sbs = (side.get("cumulativeScore") or {}).get("scoreByStat") or {}
    out = {}
    for sid, info in sbs.items():
        try:
            cat = ESPN_STAT_TO_CAT.get(int(sid))
        except (TypeError, ValueError):
            cat = None
        if cat:
            out[cat] = {"value": _finite(info.get("score")),
                        "result": info.get("result")}
    return out


def _parse_schedule(data, teams_by_id):
    """Extrait le calendrier H2H : période courante, adversaires et résultats.

    Le mapping stat ID ESPN -> catégorie (ESPN_STAT_TO_CAT) a été déterminé
    empiriquement en comparant les stats ESPN de joueurs connus aux stats NHL
    (patineurs) et sur les réglages de ligue (gardiens : 10=GAA reverse). Il est
    stable d'une saison à l'autre.

    Retourne :
      - current_period : période de matchup en cours (int).
      - teams_by_id    : {team_id_str: nom_équipe}.
      - matchups       : {période_str: {team_id_str: opp_team_id_str}}.
      - results        : {période_str: {'winner_team_id', 'teams': {tid:
                         {cat: {'value','result'}}}}} — le VRAI résultat H2H
                         (partiel tant que la période n'est pas finie).
    """
    current = (data.get("status") or {}).get("currentMatchupPeriod")
    matchups = {}
    results = {}
    for m in data.get("schedule", []):
        period = m.get("matchupPeriodId")
        if period is None:
            continue
        pkey = str(period)
        home_side = m.get("home") or {}
        away_side = m.get("away") or {}
        home = str(home_side.get("teamId", "")) or None
        away = str(away_side.get("teamId", "")) or None
        slot = matchups.setdefault(pkey, {})
        if home and away:
            slot[home] = away
            slot[away] = home
        # Résultat réel par équipe (catégories gagnées/perdues).
        winner = m.get("winner")  # HOME / AWAY / TIE / UNDECIDED
        winner_id = home if winner == "HOME" else away if winner == "AWAY" else None
        pr = results.setdefault(pkey, {"winner_team_id": None, "teams": {}})
        pr["winner_team_id"] = winner_id
        if home:
            pr["teams"][home] = _team_cat_results(home_side)
        if away:
            pr["teams"][away] = _team_cat_results(away_side)
    return {"current_period": current, "teams_by_id": teams_by_id,
            "matchups": matchups, "results": results}


def opponent_for(db, team_id, period):
    """Nom de l'équipe adverse de `team_id` à la période donnée (ou None).

    `db` = structure retournée par load_owned (contient 'schedule').
    """
    sched = db.get("schedule") or {}
    if period is None:
        return None
    opp_id = (sched.get("matchups") or {}).get(str(period), {}).get(str(team_id))
    if opp_id is None:
        return None
    return (sched.get("teams_by_id") or {}).get(str(opp_id))


def update_owned(season=None):
    """Récupère et sauvegarde les rosters dans espn_owned_<season>.json."""
    owned, my_team_id, schedule = fetch_rosters(season)
    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "my_team_id": my_team_id,
        "season": season,
        "owned": owned,
        "schedule": schedule,
    }
    path = owned_file(season)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return {"count": len(owned), "season": season, "file": path}


def load_owned(season=None):
    """Lit espn_owned_<season>.json (ou {} si absent)."""
    try:
        with open(owned_file(season), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"owned": {}}


if __name__ == "__main__":
    # Rafraîchit toutes les saisons pertinentes (Repêchage + Saison) dérivées du
    # manifeste seasons.json — plus aucune saisie d'année manuelle.
    import seasons as S
    _years = S.espn_seasons_to_refresh()
    _ok = 0
    for _yr in _years:
        try:
            _res = update_owned(_yr)
            _ok += 1
            print(f"{_res['count']} joueurs (saison {_yr}) -> {_res['file']}")
        except Exception as _e:
            # Ex. nouvelle saison pas encore montée sur ESPN pendant l'intersaison.
            print(f"Saison {_yr} : ignorée ({_e})")
    if not _ok:
        raise SystemExit("Aucune saison ESPN récupérée.")