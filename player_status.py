# player_status.py
"""Overrides de statut MANUELS : retraités et recrues (nouveaux joueurs).

Un seul concept, un seul fichier : l'utilisateur décide du statut d'un joueur.
Le manuel gagne TOUJOURS sur les données auto (stats/contrats). Il n'y a plus
d'auto-détection NHL (isActive) : voir l'ancien roster_status.py, retiré.

Deux statuts :
  - "retired" : retraité → ligne rouge, retiré des tableaux actifs.
  - "rookie"  : recrue/nouveau absent des stats → injecté comme joueur jouable.

Persistance consciente de l'environnement
-----------------------------------------
L'app en ligne (Streamlit Cloud) a un disque ÉPHÉMÈRE : un simple fichier local
serait perdu au redémarrage. On distingue donc deux contextes via la présence
d'un token GitHub dans les secrets (config.get) :

  * OFFICIEL (en ligne) : GITHUB_TOKEN présent
      → écrit player_status.json sur disque ET le commit vers GitHub
        (API contents) pour survivre aux reboots.
  * TEST (local)        : pas de token
      → écrit player_status.local.json (GITIGNORÉ). Ne touche JAMAIS la
        référence commitée. Les tests locaux n'altèrent pas ce que voit
        l'app en ligne.

Lecture : en local on lit l'override local s'il existe, sinon la référence
commitée. En ligne on lit toujours la référence.
"""

import base64
import json
import os
from datetime import datetime, timezone

import requests
import config

REF_FILE = "player_status.json"          # référence commitée (lue par le Cloud)
LOCAL_FILE = "player_status.local.json"   # override local gitignoré (tests)

VALID_STATUSES = ("retired", "rookie")

# Repo/branche par défaut : évite d'avoir à configurer 3 secrets. Sur le Cloud,
# il suffit d'ajouter GITHUB_TOKEN ; repo et branche sont déjà connus (mais
# restent surchargeables via secrets si le repo est renommé/forké).
DEFAULT_REPO = "sdecelles-coder/hockey-pool"
DEFAULT_BRANCH = "main"


# ----------------------------------------------------------------------
# Détection du contexte : officiel (token) vs test (local)
# ----------------------------------------------------------------------
def _github_conf():
    """(token, repo, branch) pour le commit-retour. token=None si non configuré."""
    token = config.get("GITHUB_TOKEN")
    repo = config.get("GITHUB_REPO", DEFAULT_REPO)      # "owner/name"
    branch = config.get("GITHUB_BRANCH", DEFAULT_BRANCH)
    return token, repo, branch


def is_cloud():
    """True si on tourne sur Streamlit Community Cloud (heuristique HOME)."""
    return os.environ.get("HOME", "") == "/home/appuser"


def is_official():
    """True si le commit-retour est possible (token GitHub présent).

    Repo/branche ont des valeurs par défaut : seul le token est requis.
    """
    token, repo, _ = _github_conf()
    return bool(token and repo)


def _active_file():
    """Fichier d'écriture selon le contexte."""
    return REF_FILE if is_official() else LOCAL_FILE


# ----------------------------------------------------------------------
# Lecture / écriture
# ----------------------------------------------------------------------
def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("players", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_status():
    """Dict {player_id(str): {status, name, position, team, age, note}}.

    En local, l'override local prime ; à défaut on lit la référence commitée.
    """
    if not is_official():
        local = _read(LOCAL_FILE)
        if local is not None:
            return local
    return _read(REF_FILE) or {}


def _github_commit(content_str):
    """Commit best-effort de la référence vers GitHub (API contents PUT).

    Silencieux en cas d'échec : l'app ne doit jamais planter à cause de ça.
    Le disque local a déjà été écrit, donc la session courante voit la modif.
    """
    token, repo, branch = _github_conf()
    if not (token and repo):
        return False
    url = f"https://api.github.com/repos/{repo}/contents/{REF_FILE}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        # sha du fichier existant (requis pour un update)
        r = requests.get(url, headers=headers, params={"ref": branch}, timeout=10)
        sha = r.json().get("sha") if r.ok else None
        payload = {
            "message": "chore: maj statut manuel des joueurs",
            "content": base64.b64encode(content_str.encode("utf-8")).decode(),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        pr = requests.put(url, headers=headers, json=payload, timeout=10)
        return pr.ok
    except Exception:
        return False


def save_status(players):
    """Persiste le dict des overrides. Commit-retour GitHub si contexte officiel."""
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "players": players,
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    with open(_active_file(), "w", encoding="utf-8") as f:
        f.write(content)
    if is_official():
        _github_commit(content)
    return players


def set_status(player_id, status, **meta):
    """Ajoute/modifie un override. status=None efface l'override.

    meta accepte name, position, team, age, note (utile pour une recrue en
    saisie libre, absente des stats et des contrats).
    """
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"statut invalide : {status!r}")
    data = load_status()
    pid = str(player_id)
    if status is None:
        data.pop(pid, None)
    else:
        entry = data.get(pid, {})
        entry["status"] = status
        for k in ("name", "position", "team", "age", "note"):
            v = meta.get(k)
            if v is not None and v != "":
                entry[k] = v
        data[pid] = entry
    save_status(data)
    return data


# ----------------------------------------------------------------------
# Fabrication d'une ligne « joueur » pour une recrue absente des stats
# ----------------------------------------------------------------------
def make_rookie_row(player_id, entry, contract=None):
    """Ligne au format des stats pour une recrue (stats absentes -> None -> « — »).

    Priorité aux méta saisies dans l'override, complétées par le contrat si dispo.
    """
    contract = contract or {}
    pos = entry.get("position") or contract.get("pos")
    return {
        "type": "goalie" if pos == "G" else "skater",
        "playerId": player_id,
        "name": entry.get("name") or contract.get("name") or str(player_id),
        "team": entry.get("team") or None,
        "position": pos,
        "age": entry.get("age") if entry.get("age") is not None else contract.get("age"),
        "gp": 0,
    }
