# roster_status.py
"""Détection des joueurs retraités et fabrication des lignes « nouveaux ».

- Retraité : joueur présent dans les stats de l'an dernier mais sans contrat
  courant ET inactif dans la LNH (API NHL landing -> isActive == False).
  PuckPedia seul ne distingue pas un retraité d'un UFA non resigné, d'où le
  recours à l'API NHL.
- Nouveau : joueur ayant un contrat courant mais aucune stat l'an dernier
  (recrue, import). On fabrique une ligne « joueur » de la même forme que les
  stats, avec les stats absentes (affichées « — »).
"""

import concurrent.futures

import requests

NHL_LANDING = "https://api-web.nhle.com/v1/player/{}/landing"
TIMEOUT = 15
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}


def is_active_nhl(nhl_id):
    """True/False selon l'API NHL, None si l'appel échoue (indéterminé)."""
    try:
        r = requests.get(NHL_LANDING.format(nhl_id), headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return bool(r.json().get("isActive"))
    except Exception:
        return None


def retired_ids(candidate_ids, max_workers=12):
    """Parmi les candidats (stats sans contrat courant), ceux inactifs (retraités).

    Un candidat dont l'appel NHL échoue (None) n'est PAS marqué retraité : choix
    sûr pour éviter les faux positifs.
    """
    candidates = [str(pid) for pid in candidate_ids if pid]
    if not candidates:
        return set()
    retired = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(is_active_nhl, pid): pid for pid in candidates}
        for fut in concurrent.futures.as_completed(futures):
            if fut.result() is False:
                retired.add(futures[fut])
    return retired


def make_new_player_row(nhl_id, contract):
    """Construit une ligne « joueur » (forme des stats) pour un nouveau joueur.

    Les champs de stats sont absents -> restent None et s'affichent « — ».
    """
    pos = contract.get("pos")
    return {
        "type": "goalie" if pos == "G" else "skater",
        "playerId": nhl_id,
        "name": contract.get("name"),
        "team": None,           # PuckPedia ne fournit pas l'abréviation NHL
        "position": pos,
        "age": contract.get("age"),
        "gp": 0,
    }
