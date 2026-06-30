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

OWNED_FILE = "espn_owned.json"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def norm_name(name):
    """Normalise un nom pour la jointure : minuscules, sans accents/ponctuation."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[.'-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fetch_rosters():
    """Retourne {nom_normalisé: {'pool_team', 'is_mine', 'espn_name'}}."""
    league_id = config.get("ESPN_LEAGUE_ID")
    season    = config.get("ESPN_SEASON")
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


def update_owned():
    """Récupère et sauvegarde les rosters dans espn_owned.json."""
    owned, my_team_id = fetch_rosters()
    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "my_team_id": my_team_id,
        "owned": owned,
    }
    with open(OWNED_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return {"count": len(owned)}


def load_owned():
    """Lit espn_owned.json (ou {} si absent)."""
    try:
        with open(OWNED_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"owned": {}}


if __name__ == "__main__":
    res = update_owned()
    print(f"{res['count']} joueurs possédés sauvegardés dans {OWNED_FILE}")