# app_settings.py
"""Préférences d'affichage PERSISTANTES de l'app (ex. « Mode d'affichage »).

Contrairement à `st.session_state` (perdu à chaque nouvelle session) ou aux
query params (liés à l'URL), ces préférences sont commitées sur GitHub via
`cloud_store` : elles restent donc permanentes d'une session à l'autre et
survivent aux redémarrages du Cloud, tant qu'elles ne sont pas modifiées
manuellement.

En test local (pas de token GitHub), l'écriture va dans `app_settings.local.json`
(gitignoré) : les tests n'altèrent jamais la référence commitée.
"""

from datetime import datetime, timezone

import cloud_store

REF_FILE = "app_settings.json"          # référence commitée (lue par le Cloud)

# Valeurs par défaut au tout premier lancement (aucun fichier encore).
DEFAULTS = {
    "team_mode": "🏒 Repêchage",
}


def load_settings():
    """Dict des préférences (défauts complétés par ce qui est persisté)."""
    data = cloud_store.load_json(REF_FILE, default={})
    stored = data.get("settings", {}) if isinstance(data, dict) else {}
    return {**DEFAULTS, **stored}


def get(key):
    """Valeur persistée pour `key`, ou son défaut."""
    return load_settings().get(key, DEFAULTS.get(key))


def set(key, value):
    """Persiste une préférence. Commit-retour GitHub si contexte officiel."""
    settings = load_settings()
    settings[key] = value
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings,
    }
    cloud_store.save_json(REF_FILE, payload, "chore: maj préférences d'affichage")
    return settings
