# update_contracts.py
"""Récupère contrats + statuts depuis l'API JSON de PuckPedia.

Source unique : https://puckpedia.com/players/api
- role=1 renvoie TOUS les joueurs (patineurs + gardiens confondus).
- data['p'] est parfois une liste, parfois un dict {'100': {...}} (quirk PHP)
  -> normalisé par _as_list.
- Jointure avec les stats NHL via nhl_id == playerId.

Récupération via une simple requête HTTP `requests` (l'API répond en HTTP 200
à un GET avec un User-Agent navigateur — plus besoin de Playwright/Chromium).

Deux modes :
- update_contracts()                 -> rafraîchit TOUS les contrats
- update_contracts_for(player_ids)   -> ne met à jour QUE les joueurs ciblés
"""

import json
import math
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

CONTRACTS_FILE = "nhl_contracts.json"
PAGE_SIZE = 100  # l'API PuckPedia plafonne à 100 joueurs par page
DELAY_SEC = 0.3  # pause entre pages (rester bien sous le timeout d'ouverture de l'app)
TIMEOUT = 30  # secondes pour requests
API_BASE = "https://puckpedia.com/players/api?q="
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}


def build_url(role, page, size=PAGE_SIZE):
    q = {
        "player_active": ["1"],
        "player_role": role,
        "sortBy": "cap_hit",
        "sortDirection": "DESC",
        "curPage": page,
        "pageSize": size,
        "focus_season": "162",
        "stat_season": "162",
    }
    return API_BASE + quote(json.dumps(q))


def _as_list(p):
    if isinstance(p, dict):
        return list(p.values())
    return p


def _fetch(url):
    """GET l'API PuckPedia et renvoie le JSON décodé."""
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_role(role, progress_cb=None, label=""):
    """Récupère tous les joueurs d'un rôle via pagination (requests)."""
    out = []

    data = _fetch(build_url(role, 1))["data"]
    out.extend(_as_list(data["p"]))
    total = data["meta"]["count"]
    pages = math.ceil(total / PAGE_SIZE)

    if progress_cb:
        progress_cb(1, pages, f"{label} page 1/{pages}")

    for p in range(2, pages + 1):
        time.sleep(DELAY_SEC)
        d = _fetch(build_url(role, p))["data"]
        out.extend(_as_list(d["p"]))
        if progress_cb:
            progress_cb(p, pages, f"{label} page {p}/{pages}")

    return out, total


def parse_player(p):
    return {
        "nhl_id": p.get("nhl_id"),
        "name": f"{p.get('p_fn', '')} {p.get('p_ln', '')}".strip(),
        "pos": p.get("pos"),
        "age": p.get("age"),
        "cap_hit_value": int(p["cap_hit"]) if p.get("cap_hit") else 0,
        "signing_status": p.get("sts_sign"),
        "expiry_status": p.get("sts_exp"),
        "expiry_year": p.get("exp"),
        "clauses": p.get("clauses"),
        "contract_level": p.get("lvl"),
        "years_left": p.get("yr_left"),
        "ppg_points": p.get("st_ppg"),
        "puckpedia_url": p.get("p_url"),
    }


def load_cache():
    try:
        with open(CONTRACTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"contracts": {}}


def save_cache(contracts):
    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "contracts": contracts,
    }
    with open(CONTRACTS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


def _fetch_all_parsed(progress_cb=None):
    all_players = []
    errors = []
    try:
        players, _ = fetch_role("1", progress_cb, "Joueurs")
        all_players.extend(players)
    except Exception as e:
        errors.append(("Joueurs", f"{type(e).__name__}: {e}"))

    parsed_by_id = {}
    no_id = 0
    now = datetime.now(timezone.utc).isoformat()
    for p in all_players:
        if not isinstance(p, dict):
            continue
        parsed = parse_player(p)
        nhl_id = parsed["nhl_id"]
        if not nhl_id:
            no_id += 1
            continue
        parsed["scraped_at"] = now
        parsed_by_id[str(nhl_id)] = parsed
    return parsed_by_id, errors, no_id


def update_contracts(progress_cb=None):
    parsed_by_id, errors, no_id = _fetch_all_parsed(progress_cb)
    if not parsed_by_id:
        raise RuntimeError(f"Aucun contrat récupéré — fichier non modifié. Erreurs : {errors}")
    save_cache(parsed_by_id)
    return {
        "scraped": len(parsed_by_id),
        "errors": errors,
        "no_id": no_id,
        "total_cached": len(parsed_by_id),
    }


def update_contracts_for(player_ids, progress_cb=None):
    targets = {str(pid) for pid in player_ids if pid is not None}
    parsed_by_id, errors, no_id = _fetch_all_parsed(progress_cb)

    db = load_cache()
    cache = db.get("contracts", {})

    updated = 0
    not_found = []
    for pid in targets:
        if pid in parsed_by_id:
            cache[pid] = parsed_by_id[pid]
            updated += 1
        else:
            not_found.append(pid)

    save_cache(cache)
    return {
        "scraped": updated,
        "errors": errors,
        "not_found": not_found,
        "total_cached": len(cache),
    }


if __name__ == "__main__":
    print("Récupération complète des contrats PuckPedia (Playwright)...")

    def cb(done, total, msg):
        print(f"  {msg}")

    s = update_contracts(progress_cb=cb)
    print(f"\nTerminé : {s['scraped']} contrats, "
          f"{len(s['errors'])} erreurs, {s['no_id']} sans nhl_id")
    for e in s["errors"]:
        print("  ERREUR:", e)
