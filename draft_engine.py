# draft_engine.py
"""Moteur de scoring et de planification pour le repêchage du pool.

- Calcule un score 'Valeur' (somme de z-scores pondérés par catégorie).
- Calcule 'Valeur/$' (Valeur / cap_hit, avec plancher de cap).
- Gère la persistance des statuts (Protégé moi / Protégé autre DG / Cible)
  dans draft_plan.json.

Catégories patineurs : G, A, +/-, PIM, PPP, SOG, HIT  (toutes à maximiser)
Catégories gardiens  : W, SO, GAA, SV%  (GAA à minimiser => inversée)
"""

import json
from statistics import mean, pstdev

PLAN_FILE = "draft_plan.json"

# Poids par défaut (ajustables ensuite via l'app)
DEFAULT_WEIGHTS_SKATER = {
    "goals": 1.0, "assists": 1.0, "plus_minus": 1.5,
    "pim": 1.0, "ppp": 2.0, "sog": 1.0, "hits": 1.25,
}
DEFAULT_WEIGHTS_GOALIE = {
    "wins": 1.0, "shutouts": 2.0, "gaa": 1.0, "sv_pct": 1.0,
}

# Sens des catégories : True = plus c'est haut, mieux c'est ; False = inversé
SKATER_DIRECTION = {
    "goals": True, "assists": True, "plus_minus": True,
    "pim": True, "ppp": True, "sog": True, "hits": True,
}
GOALIE_DIRECTION = {
    "wins": True, "shutouts": True, "gaa": False, "sv_pct": True,
}

# Catégories cumulatives -> à projeter sur 82 matchs (taux/match * 82).
# Les autres (taux comme GAA, SV%) ne se projettent PAS.
SKATER_CUMULATIVE = {"goals", "assists", "plus_minus", "pim", "ppp", "sog", "hits"}
GOALIE_CUMULATIVE = {"wins", "shutouts"}   # gaa, sv_pct sont déjà des taux
PROJECT_GAMES = 82

CAP_FLOOR = 1_000_000   # plancher pour Valeur/$ (évite division par ~0


def _zscores(values, higher_is_better=True, floor_zero=True):
    """Retourne la liste des z-scores. Gère les None et écart-type nul.

    floor_zero=True : plancher les z-scores négatifs à 0, pour ne PAS pénaliser
    un joueur dans les catégories où il est faible (ex. un marqueur pur comme
    Caufield n'est pas puni pour ses HIT/PIM bas ; il gagne seulement des points
    pour ses forces).
    """
    nums = [v for v in values if v is not None]
    if len(nums) < 2:
        return [0.0 for _ in values]
    mu = mean(nums)
    sd = pstdev(nums)
    if sd == 0:
        return [0.0 for _ in values]
    out = []
    for v in values:
        if v is None:
            out.append(0.0)
        else:
            z = (v - mu) / sd
            z = z if higher_is_better else -z
            if floor_zero and z < 0:
                z = 0.0
            out.append(z)
    return out


def compute_scores(players, player_type, weights=None, min_gp=1,
                   youth_weight=0.0, ref_age=27):
    """Calcule Valeur et Valeur/$ pour chaque joueur d'un type.

    players : liste de dicts (depuis nhl_stats.json), filtrés sur player_type.
    Retourne une liste de dicts enrichis avec 'value' et 'value_per_m',
    en réutilisant le cap_hit fourni dans le champ 'cap_hit_value'.
    """
    if player_type == "skater":
        cats = list(DEFAULT_WEIGHTS_SKATER.keys())
        direction = SKATER_DIRECTION
        w = {**DEFAULT_WEIGHTS_SKATER, **(weights or {})}
    else:
        cats = list(DEFAULT_WEIGHTS_GOALIE.keys())
        direction = GOALIE_DIRECTION
        w = {**DEFAULT_WEIGHTS_GOALIE, **(weights or {})}

    # On ne score que les joueurs ayant assez de matchs (sinon stats trompeuses)
    pool = [p for p in players if (p.get("gp") or 0) >= min_gp]
    if not pool:
        return []

    cumulative = SKATER_CUMULATIVE if player_type == "skater" else GOALIE_CUMULATIVE

    def projected(p, cat):
        """Stat projetée sur 82 matchs si cumulative, sinon valeur brute (taux)."""
        v = p.get(cat)
        if v is None:
            return None
        if cat in cumulative:
            gp = p.get("gp") or 0
            if gp <= 0:
                return None
            return v / gp * PROJECT_GAMES
        return v   # taux (GAA, SV%) : pas de projection

    # z-scores par catégorie (sur stats projetées)
    z_by_cat = {}
    for cat in cats:
        vals = [projected(p, cat) for p in pool]
        z_by_cat[cat] = _zscores(vals, direction[cat])

    # somme pondérée + bonus de jeunesse séparé
    # Bonus jeunesse : (ref_age - age) * youth_weight, planché à 0 pour les vieux
    # => un joueur plus jeune que ref_age gagne un bonus proportionnel.
    results = []
    for i, p in enumerate(pool):
        base_value = sum(w[cat] * z_by_cat[cat][i] for cat in cats)
        age = p.get("age")
        youth_bonus = 0.0
        if youth_weight and age is not None:
            youth_bonus = max(0.0, (ref_age - age)) * youth_weight
        value = base_value + youth_bonus
        cap = p.get("cap_hit_value", 0) or 0
        enriched = dict(p)
        enriched["value_base"] = round(base_value, 2)
        enriched["youth_bonus"] = round(youth_bonus, 2)
        enriched["value"] = round(value, 2)
        # Valeur/$M : None si pas de contrat (cap inconnu) -> pas de ratio trompeur
        if cap > 0:
            enriched["value_per_m"] = round(value / (cap / 1_000_000), 2)
        else:
            enriched["value_per_m"] = None
        results.append(enriched)
    return results


def _position_group(p):
    """Forwards (C/L/R) / Défenseurs (D) / Gardiens (G)."""
    pos = (p.get("position") or "").upper()
    if pos == "G":
        return "G"
    if pos == "D":
        return "D"
    return "F"   # C, L, R


def assign_tiers(scored):
    """Ajoute un champ 'tier' selon le percentile de la Valeur,
    calculé SÉPARÉMENT par groupe de position (F / D / G).

    Un défenseur est comparé aux autres défenseurs, pas aux attaquants.

    Barème relatif :
      top 5%  -> 🌟 Superstar
      top 15% -> 🔥 Excellent
      top 35% -> ⭐ Très bon
      top 60% -> ✓ Bon
      reste   -> · Moyen
    """
    if not scored:
        return scored

    # regroupe par position
    groups = {}
    for p in scored:
        groups.setdefault(_position_group(p), []).append(p)

    for grp, members in groups.items():
        vals = sorted((p["value"] for p in members), reverse=True)
        n = len(vals)

        def threshold(pct, vals=vals, n=n):
            idx = min(n - 1, int(pct * n))
            return vals[idx]

        t05, t15, t35, t60 = (threshold(0.05), threshold(0.15),
                              threshold(0.35), threshold(0.60))
        for p in members:
            v = p["value"]
            if v >= t05:
                p["tier"] = "🌟 Superstar"
            elif v >= t15:
                p["tier"] = "🔥 Excellent"
            elif v >= t35:
                p["tier"] = "⭐ Très bon"
            elif v >= t60:
                p["tier"] = "✓ Bon"
            else:
                p["tier"] = "· Moyen"
    return scored


# ----------------------------------------------------------------------
# Persistance du plan de repêchage
# ----------------------------------------------------------------------
def load_plan():
    """Retourne {player_id(str): status}. status in
    {'mine', 'other', 'target'}."""
    try:
        with open(PLAN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_plan(plan):
    with open(PLAN_FILE, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)


def set_status(player_id, status):
    """status in {'mine','other','target', None}. None => retire le joueur."""
    plan = load_plan()
    pid = str(player_id)
    if status is None:
        plan.pop(pid, None)
    else:
        plan[pid] = status
    save_plan(plan)
    return plan


# ----------------------------------------------------------------------
# Alignement on ice / bench (indépendant du statut de possession)
# ----------------------------------------------------------------------
LINEUP_FILE = "lineup.json"


def load_lineup():
    """Retourne {player_id(str): 'ice'|'bench'}."""
    try:
        with open(LINEUP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_lineup(lineup):
    with open(LINEUP_FILE, "w", encoding="utf-8") as f:
        json.dump(lineup, f, ensure_ascii=False, indent=2)


def set_lineup(player_id, slot):
    """slot in {'ice','bench', None}. None => retire l'alignement."""
    lineup = load_lineup()
    pid = str(player_id)
    if slot is None:
        lineup.pop(pid, None)
    else:
        lineup[pid] = slot
    save_lineup(lineup)
    return lineup



# ----------------------------------------------------------------------
# Agrégation par équipe de pool (pour l'onglet Confrontations)
# ----------------------------------------------------------------------
SKATER_CATS = [("G", "goals"), ("A", "assists"), ("+/-", "plus_minus"),
               ("PIM", "pim"), ("PPP", "ppp"), ("SOG", "sog"), ("HIT", "hits")]
GOALIE_CATS = [("W", "wins"), ("SO", "shutouts"), ("GAA", "gaa"), ("SV%", "sv_pct")]

# Sens : True = plus haut est mieux ; False = plus bas est mieux (GAA)
CAT_DIRECTION = {
    "G": True, "A": True, "+/-": True, "PIM": True, "PPP": True,
    "SOG": True, "HIT": True, "W": True, "SO": True, "GAA": False, "SV%": True,
}


def aggregate_by_team(players, owned, min_gp=20, project_games=82):
    """Agrège les stats projetées sur 82 matchs par équipe de pool.

    players : liste de joueurs (avec stats brutes + 'name', 'gp', 'type').
    owned   : dict {nom_normalisé: {pool_team, is_mine}} (depuis ESPN).

    Retourne : (dict {pool_team: {cat: valeur}}, dict {pool_team: is_mine}).
    Patineurs : cumul des projections. Gardiens : W/SO projetés cumulés,
    GAA/SV% en moyenne pondérée par les matchs.
    """
    import re, unicodedata

    def norm(name):
        if not name:
            return ""
        s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        s = re.sub(r"[.'-]", "", s.lower())
        return re.sub(r"\s+", " ", s).strip()

    teams = {}       # pool_team -> {cat: value}
    is_mine = {}     # pool_team -> bool
    goalie_acc = {}  # pool_team -> {'gp':, 'gaa_w':, 'svp_w':} pour moyennes

    for p in players:
        entry = owned.get(norm(p.get("name")))
        if not entry:
            continue
        team = entry.get("pool_team")
        is_mine[team] = entry.get("is_mine", False)
        gp = p.get("gp") or 0
        if gp < min_gp:
            continue
        teams.setdefault(team, {})
        goalie_acc.setdefault(team, {"gp": 0, "gaa_w": 0.0, "svp_w": 0.0})

        if p.get("type") == "skater":
            for label, src in SKATER_CATS:
                v = p.get(src)
                if v is None:
                    continue
                proj = v / gp * project_games
                teams[team][label] = teams[team].get(label, 0.0) + proj
        else:  # gardien
            for label, src in [("W", "wins"), ("SO", "shutouts")]:
                v = p.get(src)
                if v is not None:
                    proj = v / gp * project_games
                    teams[team][label] = teams[team].get(label, 0.0) + proj
            # GAA / SV% : moyenne pondérée par GP
            acc = goalie_acc[team]
            if p.get("gaa") is not None:
                acc["gaa_w"] += p["gaa"] * gp
            if p.get("sv_pct") is not None:
                acc["svp_w"] += p["sv_pct"] * gp
            acc["gp"] += gp

    # finaliser GAA / SV% (moyenne pondérée)
    for team, acc in goalie_acc.items():
        if acc["gp"] > 0:
            teams.setdefault(team, {})
            teams[team]["GAA"] = round(acc["gaa_w"] / acc["gp"], 3)
            teams[team]["SV%"] = round(acc["svp_w"] / acc["gp"], 4)

    # arrondir les cumuls patineurs
    for team, cats in teams.items():
        for label, _ in SKATER_CATS + [("W", "wins"), ("SO", "shutouts")]:
            if label in cats:
                cats[label] = round(cats[label], 1)

    return teams, is_mine



if __name__ == "__main__":
    # Test rapide sur les stats locales
    with open("nhl_stats.json", encoding="utf-8") as f:
        db = json.load(f)
    # injecte le cap depuis les contrats
    try:
        with open("nhl_contracts.json", encoding="utf-8") as f:
            contracts = json.load(f)["contracts"]
    except (FileNotFoundError, KeyError):
        contracts = {}
    for p in db["players"]:
        c = contracts.get(str(p.get("playerId")), {})
        p["cap_hit_value"] = c.get("cap_hit_value", 0)

    skaters = [p for p in db["players"] if p.get("type") == "skater"]
    scored = compute_scores(skaters, "skater", min_gp=20)
    scored.sort(key=lambda x: x["value"], reverse=True)
    print("TOP 10 patineurs par Valeur :")
    for p in scored[:10]:
        print(f"  {p['name']:25} val={p['value']:6.2f}  "
              f"val/$M={p['value_per_m']:6.2f}  "
              f"cap=${p.get('cap_hit_value',0):,}")