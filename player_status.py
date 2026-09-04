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

from datetime import datetime, timezone

import cloud_store

REF_FILE = "player_status.json"          # référence commitée (lue par le Cloud)

VALID_STATUSES = ("retired", "rookie")

# Détection du contexte : réexportée pour l'app (bandeau Cloud). La logique vit
# désormais dans cloud_store, partagée avec draft_engine.
is_cloud = cloud_store.is_cloud
is_official = cloud_store.is_official


# ----------------------------------------------------------------------
# Lecture / écriture
# ----------------------------------------------------------------------
def load_status():
    """Dict {player_id(str): {status, name, position, team, age, note}}.

    En local, l'override local prime ; à défaut on lit la référence commitée.
    """
    data = cloud_store.load_json(REF_FILE, default={})
    return data.get("players", {}) if isinstance(data, dict) else {}


def save_status(players):
    """Persiste le dict des overrides. Commit-retour GitHub si contexte officiel."""
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "players": players,
    }
    cloud_store.save_json(REF_FILE, payload, "chore: maj statut manuel des joueurs")
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
