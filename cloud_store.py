# cloud_store.py
"""Persistance consciente de l'environnement, PARTAGÉE par tous les modules qui
doivent survivre au disque ÉPHÉMÈRE de Streamlit Community Cloud.

Problème résolu
---------------
Sur le Cloud, le disque est remis à zéro à chaque redémarrage : un simple
`open(..., "w")` ne suffit pas, les modifs seraient perdues. On distingue donc
deux contextes selon la présence d'un token GitHub dans les secrets :

  * OFFICIEL (en ligne) : GITHUB_TOKEN présent
      → écrit le fichier de RÉFÉRENCE sur disque ET le commit vers GitHub
        (API contents) pour survivre aux reboots.
  * TEST (local)        : pas de token
      → écrit un fichier `<nom>.local.<ext>` (GITIGNORÉ). Ne touche JAMAIS la
        référence commitée : les tests locaux n'altèrent pas ce que voit l'app
        en ligne.

Lecture : en local on lit l'override local s'il existe, sinon la référence
commitée. En ligne on lit toujours la référence.

Ce module ne connaît RIEN de la forme des données : il manipule du JSON brut.
Chaque appelant (player_status, draft_engine) façonne sa propre structure.
"""

import base64
import json
import os

import requests

import config

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


def _local_path(ref_file):
    """`draft_plan.json` -> `draft_plan.local.json` (override local gitignoré)."""
    root, ext = os.path.splitext(ref_file)
    return f"{root}.local{ext}"


# ----------------------------------------------------------------------
# Lecture / écriture JSON brut
# ----------------------------------------------------------------------
def load_json(ref_file, default=None):
    """Charge le JSON de référence.

    En local, l'override `<nom>.local.<ext>` prime s'il existe ; à défaut on lit
    la référence commitée. En ligne on lit toujours la référence.
    """
    if is_official():
        candidates = [ref_file]
    else:
        candidates = [_local_path(ref_file), ref_file]
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return {} if default is None else default


def _github_commit(ref_file, content_str, message):
    """Commit best-effort de la référence vers GitHub (API contents PUT).

    Silencieux en cas d'échec : l'app ne doit jamais planter à cause de ça.
    Le disque local a déjà été écrit, donc la session courante voit la modif.
    """
    token, repo, branch = _github_conf()
    if not (token and repo):
        return False
    url = f"https://api.github.com/repos/{repo}/contents/{ref_file}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    print(f"[cloud_store] Tentative de commit GitHub pour {ref_file} "
          f"(repo={repo}, branch={branch})", flush=True)
    try:
        # sha du fichier existant (requis pour un update)
        r = requests.get(url, headers=headers, params={"ref": branch}, timeout=10)
        sha = r.json().get("sha") if r.ok else None
        if not r.ok:
            print(f"[cloud_store] GET {url} (ref={branch}) a échoué : "
                  f"{r.status_code} {r.text[:300]}", flush=True)
        payload = {
            "message": message,
            "content": base64.b64encode(content_str.encode("utf-8")).decode(),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        pr = requests.put(url, headers=headers, json=payload, timeout=10)
        if not pr.ok:
            print(f"[cloud_store] PUT {url} a échoué : "
                  f"{pr.status_code} {pr.text[:300]}", flush=True)
        else:
            print(f"[cloud_store] Commit réussi pour {ref_file} "
                  f"(sha={pr.json().get('content', {}).get('sha', '?')[:8]})", flush=True)
        return pr.ok
    except Exception as e:
        print(f"[cloud_store] Exception lors du commit GitHub de {ref_file} : {e!r}", flush=True)
        return False


def save_json(ref_file, data, message):
    """Persiste `data` (JSON). Commit-retour GitHub si contexte officiel.

    En test local, écrit dans `<nom>.local.<ext>` sans jamais toucher la
    référence commitée ni GitHub.
    """
    content = json.dumps(data, ensure_ascii=False, indent=2)
    target = ref_file if is_official() else _local_path(ref_file)
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    if is_official():
        _github_commit(ref_file, content, message)
    return data
