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
    """Retourne {nom_normalisé: {'pool_team', 'is_mine', 'espn_name'}}.

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

    r = requests.get(base, params={"view": ["mTeam", "mRoster"]},
                     cookies=cookies, headers=HEADERS, timeout=20, verify=verify_ssl)
    r.raise_for_status()
    data = r.json()

    owned = {}
    for team in data.get("teams", []):
        tid = team.get("id")
        tname = team.get("name") or f"{team.get('location','')} {team.get('nickname','')}".strip()
        is_mine = tid == my_team_id
        for entry in team.get("roster", {}).get("entries", []):
            player = entry.get("playerPoolEntry", {}).get("player", {})
            full = player.get("fullName", "")
            key = norm_name(full)
            if key:
                owned[key] = {
                    "pool_team": tname,
                    "is_mine": is_mine,
                    "espn_name": full,
                }
    return owned, int(config.get("ESPN_TEAM_ID", "0"))


def update_owned(season=None):
    """Récupère et sauvegarde les rosters dans espn_owned_<season>.json."""
    owned, my_team_id = fetch_rosters(season)
    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "my_team_id": my_team_id,
        "season": season,
        "owned": owned,
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