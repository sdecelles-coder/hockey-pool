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
import json
from datetime import datetime, timezone

import requests

NHL_LANDING = "https://api-web.nhle.com/v1/player/{}/landing"
TIMEOUT = 8            # s par appel NHL (évite les traînards)
STATUS_FILE = "roster_status.json"
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


def retired_ids(candidate_ids, max_workers=8, budget_sec=60):
    """Parmi les candidats (stats sans contrat courant), ceux inactifs (retraités).

    Un candidat dont l'appel NHL échoue (None) n'est PAS marqué retraité : choix
    sûr pour éviter les faux positifs. `budget_sec` borne le temps total : les
    appels non terminés sont annulés (ils seront réévalués au prochain refresh).
    Concurrence volontairement modérée : l'API NHL throttle les grosses rafales.
    """
    candidates = [str(pid) for pid in candidate_ids if pid]
    if not candidates:
        return set()
    retired = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(is_active_nhl, pid): pid for pid in candidates}
        done, not_done = concurrent.futures.wait(futures, timeout=budget_sec)
        for fut in done:
            if fut.result() is False:
                retired.add(futures[fut])
        for fut in not_done:
            fut.cancel()
    return retired


def load_retired():
    """Ensemble des ids SUSPECTÉS retraités (détection auto NHL isActive)."""
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return set(json.load(f).get("retired", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


# ----------------------------------------------------------------------
# Confirmation manuelle du statut retraité (l'auto n'est qu'une suggestion)
# ----------------------------------------------------------------------
MANUAL_FILE = "retired_manual.json"


def load_manual():
    """Décisions manuelles {player_id(str): 'retired' | 'active'}.

    'retired' = confirmé retraité par l'utilisateur ; 'active' = suspect écarté.
    """
    try:
        with open(MANUAL_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_manual(manual):
    with open(MANUAL_FILE, "w", encoding="utf-8") as f:
        json.dump(manual, f, ensure_ascii=False, indent=2)


def set_manual(player_id, status):
    """status in {'retired', 'active', None}. None => efface la décision (redevient suspect)."""
    manual = load_manual()
    pid = str(player_id)
    if status is None:
        manual.pop(pid, None)
    else:
        manual[pid] = status
    save_manual(manual)
    return manual


def _status_age_hours():
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            ts = json.load(f).get("updated_at")
        if not ts:
            return None
        return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 3600
    except Exception:
        return None


def refresh_retired(candidate_ids, max_age_h=24, budget_sec=60):
    """Recalcule et persiste les retraités si le cache disque est absent/périmé.

    Retourne l'ensemble des ids retraités (frais ou recalculés). Ne fait AUCUN
    appel réseau si roster_status.json a moins de `max_age_h` heures.
    """
    age = _status_age_hours()
    if age is not None and age < max_age_h:
        return load_retired()
    retired = retired_ids(candidate_ids, budget_sec=budget_sec)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "candidates": len(list(candidate_ids)),
        "retired": sorted(retired),
    }
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
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
