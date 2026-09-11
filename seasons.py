# seasons.py
"""Gestion des saisons : manifeste `seasons.json` + résolution des chemins stats/contrats.

Modèle (voir docs/PLAN-stats-multi-saison.md) :
- Les fichiers RACINE (`nhl_stats.json` / `nhl_contracts.json`) contiennent toujours le
  « live » : mis à jour quotidiennement. Ils représentent la saison `current_season`.
- Une saison terminée est FIGÉE dans `archive/<saison>/{stats,contracts}.json`.
- Deux phases :
    * "active"    -> racine = saison en cours (stats + contrats live).
    * "offseason" -> racine = stats finales de la dernière saison + contrats live ;
                     sert de TAMPON pour le repêchage de `upcoming_season`.
- Bascule MANUELLE (~2x/an) via season_admin.py (`close` / `open`).
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent
MANIFEST = ROOT / "seasons.json"
ARCHIVE_DIR = ROOT / "archive"
LIVE_STATS = ROOT / "nhl_stats.json"
LIVE_CONTRACTS = ROOT / "nhl_contracts.json"

# État de repli si le manifeste est absent (compat. avant première init).
DEFAULT_MANIFEST = {
    "current_season": "20252026",
    "phase": "active",
    "upcoming_season": "",
    "archived": [],
}


def load_manifest():
    """Lit `seasons.json`, complété par les clés par défaut manquantes."""
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_MANIFEST)
    out = dict(DEFAULT_MANIFEST)
    out.update(data)
    return out


def save_manifest(manifest):
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def current_season():
    """Id de la saison que les fichiers racine représentent (ex. '20252026')."""
    return load_manifest()["current_season"]


def fmt_season(sid):
    """'20252026' -> '2025-2026'."""
    if sid and len(sid) == 8 and sid.isdigit():
        return f"{sid[:4]}-{sid[4:]}"
    return sid or ""


def next_season_id(sid):
    """'20252026' -> '20262027'."""
    a, b = int(sid[:4]), int(sid[4:])
    return f"{a + 1}{b + 1}"


def archive_paths(sid):
    """Chemins (stats, contrats) figés d'une saison archivée."""
    d = ARCHIVE_DIR / sid
    return d / "stats.json", d / "contracts.json"


def latest_archived():
    """Id de la saison archivée la plus récente (ou None)."""
    archived = sorted(load_manifest().get("archived", []), reverse=True)
    return archived[0] if archived else None


def espn_year(sid):
    """Année ESPN (int) d'une saison : '20252026' -> 2026 (année de fin)."""
    return int(sid[4:])


def espn_season_for_mode(draft_mode):
    """Année ESPN (int) à interroger selon le mode d'affichage.

    - Repêchage (draft_mode=True) : année de fin de `current_season` — pendant
      l'intersaison c'est la saison qui vient de se terminer (« l'ancienne »).
    - Saison (draft_mode=False)   : année de fin de la saison la plus récente,
      c.-à-d. `upcoming_season` en intersaison (la nouvelle ligue déjà montée sur
      ESPN par l'admin), sinon `current_season`.

    Ex. intersaison current=20252026 / upcoming=20262027 :
        Repêchage -> 2026, Saison -> 2027.
    """
    m = load_manifest()
    if draft_mode:
        sid = m["current_season"]
    elif m["phase"] == "offseason":
        sid = m.get("upcoming_season") or next_season_id(m["current_season"])
    else:
        sid = m["current_season"]
    return espn_year(sid)


def espn_seasons_to_refresh():
    """Années ESPN à garder fraîches (Repêchage + Saison), triées et dédupliquées."""
    return sorted({espn_season_for_mode(True), espn_season_for_mode(False)})


def list_selectable():
    """Options ordonnées pour le sélecteur de saison de l'app.

    Chaque option : dict(key, label, stats_path, contracts_path, kind).
    La 1re option (index 0) est la plus récente -> défaut naturel du selectbox.
    """
    m = load_manifest()
    opts = []

    if m["phase"] == "offseason":
        up = m.get("upcoming_season") or next_season_id(m["current_season"])
        opts.append({
            "key": f"buffer-{up}",
            "label": f"{fmt_season(up)} (repêchage — stats {fmt_season(m['current_season'])})",
            "stats_path": LIVE_STATS,
            "contracts_path": LIVE_CONTRACTS,
            "kind": "buffer",
        })
    else:  # active
        cur = m["current_season"]
        opts.append({
            "key": f"active-{cur}",
            "label": f"{fmt_season(cur)} (en cours)",
            "stats_path": LIVE_STATS,
            "contracts_path": LIVE_CONTRACTS,
            "kind": "active",
        })

    for sid in sorted(m.get("archived", []), reverse=True):
        s_path, c_path = archive_paths(sid)
        opts.append({
            "key": f"archive-{sid}",
            "label": fmt_season(sid),
            "stats_path": s_path,
            "contracts_path": c_path,
            "kind": "archive",
        })

    return opts
