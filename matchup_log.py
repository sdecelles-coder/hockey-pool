# matchup_log.py
"""Journal des confrontations H2H : prédiction vs résultat réel + blessures.

But
----
À chaque exécution du job quotidien, on enregistre un « snapshot » de la semaine
de matchup en cours :

  * PRÉDICTION : ma confrontation catégorie par catégorie contre l'adversaire
    réel de la semaine, basée sur les TOTAUX DE SAISON projetés (même moteur que
    l'onglet Pool STM : draft_engine.aggregate_by_team). Le dernier snapshot
    d'une période devient la prédiction « finale ».
  * RÉSULTAT RÉEL : le vrai pointage H2H ESPN (cumulativeScore.scoreByStat)
    traduit en catégories, figé à la fin de la période.
  * BLESSURES : les joueurs (mon équipe ET l'adversaire) qui passent en statut
    blessé PENDANT la semaine sont journalisés (suivi jour après jour).

Quand la période ESPN avance, la période précédente est « finalisée » (prédiction
finale + résultat réel + blessures) et déplacée dans l'historique. Ces données
servent à comparer prédiction et réalité pour d'éventuels ajustements.

Persistance
-----------
Fichier de RÉFÉRENCE committé `matchup_log.json` (non gitignoré), écrit
directement puis committé par le job quotidien (GitHub Actions / daily_update).
La lecture passe par cloud_store pour rester cohérente avec le reste de l'app.
"""

import json
from datetime import datetime, timezone

import cloud_store
import draft_engine as de
import espn_roster as er

LOG_FILE = "matchup_log.json"

# Prédiction = totaux réels + remplissage médian (gp<=5), alignés sur l'onglet.
RESPECT_ON_ICE = True
# Choix du dataset (live vs dernière saison complète) — critère PAR JOUEUR : on
# n'utilise la saison en cours que si au moins CONF_LIVE_MIN_SHARE des joueurs
# alignés ont un échantillon exploitable (gp > FILL_GP_MAX). Sinon (début de
# saison : la plupart des joueurs à gp<=5 seraient tous « remplis » -> équipes
# identiques), on garde la dernière saison archivée (complète).
FILL_GP_MAX = 5
CONF_LIVE_MIN_SHARE = 0.6

# Catégories comparées (mêmes libellés/ordre que l'onglet Confrontations).
CONF_CATS = [c[0] for c in de.SKATER_CATS] + [c[0] for c in de.GOALIE_CATS]


# ----------------------------------------------------------------------
# Persistance
# ----------------------------------------------------------------------
def load_log():
    """Structure {'seasons': {season_str: {...}}} (ou vide)."""
    data = cloud_store.load_json(LOG_FILE, default={"seasons": {}})
    if not isinstance(data, dict) or "seasons" not in data:
        return {"seasons": {}}
    return data


def _save_log(data):
    """Écrit le journal selon le contexte (même discipline que cloud_store).

    - OFFICIEL (token présent = job cloud/GitHub Actions) : écrit la RÉFÉRENCE
      matchup_log.json, committée par le job quotidien (git push).
    - LOCAL (pas de token) : écrit matchup_log.local.json (GITIGNORÉ). Les essais
      locaux ne partent JAMAIS sur GitHub — c'est le job cloud qui met à jour la
      référence lue par l'app.
    """
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    target = LOG_FILE if cloud_store.is_official() else cloud_store._local_path(LOG_FILE)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


# ----------------------------------------------------------------------
# Prédiction (totaux de saison) — même logique que _matchup_compare de l'app
# ----------------------------------------------------------------------
def _prediction(my_team, opp, teams):
    """{cats, my_wins, opp_wins, predicted_winner} pour my_team vs opp."""
    mine = teams.get(my_team, {})
    them = teams.get(opp, {})
    cats = {}
    my_wins = opp_wins = 0
    for cat in CONF_CATS:
        mv = mine.get(cat)
        ov = them.get(cat)
        if mv is None or ov is None:
            winner = None
        else:
            higher = de.CAT_DIRECTION[cat]
            if mv == ov:
                winner = "tie"
            elif (mv > ov) == higher:
                winner = "mine"; my_wins += 1
            else:
                winner = "opp"; opp_wins += 1
        cats[cat] = {"me": mv, "opp": ov, "winner": winner}
    pred_winner = ("mine" if my_wins > opp_wins
                   else "opp" if opp_wins > my_wins else "tie")
    return {"cats": cats, "my_wins": my_wins, "opp_wins": opp_wins,
            "predicted_winner": pred_winner}


# ----------------------------------------------------------------------
# Résultat réel H2H (depuis schedule.results)
# ----------------------------------------------------------------------
def _actual(schedule, period, my_id, opp_id):
    """{cats, my_wins, opp_wins, ties, winner} réel, ou None si indisponible."""
    results = (schedule.get("results") or {}).get(str(period))
    if not results:
        return None
    mine = (results.get("teams") or {}).get(str(my_id))
    if not mine:
        return None
    cats = {}
    my_wins = opp_wins = ties = 0
    for cat, info in mine.items():
        res = info.get("result")  # WIN / LOSS / TIE / None
        cats[cat] = {"me": info.get("value"), "result": res}
        if res == "WIN":
            my_wins += 1
        elif res == "LOSS":
            opp_wins += 1
        elif res == "TIE":
            ties += 1
    win_id = results.get("winner_team_id")
    if win_id == str(my_id):
        winner = "mine"
    elif win_id == str(opp_id):
        winner = "opp"
    elif my_wins > opp_wins:
        winner = "mine"
    elif opp_wins > my_wins:
        winner = "opp"
    else:
        winner = "tie"
    return {"cats": cats, "my_wins": my_wins, "opp_wins": opp_wins,
            "ties": ties, "winner": winner}


# ----------------------------------------------------------------------
# Blessures (suivi jour après jour, mon équipe + adversaire)
# ----------------------------------------------------------------------
def _injury_scan(owned, my_team, opp):
    """{key: {name, team, status, injured}} des joueurs On ice des 2 équipes."""
    out = {}
    for key, e in owned.items():
        team = e.get("pool_team")
        if team not in (my_team, opp):
            continue
        if not e.get("on_ice", True):
            continue
        out[key] = {
            "name": e.get("espn_name") or key,
            "team": "mine" if team == my_team else "opp",
            "status": e.get("injury_status"),
            "injured": bool(e.get("injured")),
        }
    return out


def _track_injuries(cur, owned, my_team, opp, today):
    """Met à jour l'état vu et journalise les nouvelles blessures de la semaine.

    Un événement est ajouté quand un joueur passe de sain à blessé. `cur` est
    l'entrée de période courante (modifiée en place).
    """
    seen = cur.setdefault("injury_seen", {})
    events = cur.setdefault("injury_events", [])
    scan = _injury_scan(owned, my_team, opp)
    for key, info in scan.items():
        prev = seen.get(key, {})
        was_injured = bool(prev.get("injured"))
        now_injured = info["injured"]
        if now_injured and not was_injured:
            events.append({
                "name": info["name"],
                "team": info["team"],
                "from": prev.get("status", "ACTIVE"),
                "to": info["status"],
                "date": today,
            })
        seen[key] = info


# ----------------------------------------------------------------------
# Chargement des stats NHL (pour la prédiction, hors Streamlit)
# ----------------------------------------------------------------------
def _read_players(path):
    """Liste de joueurs d'un fichier stats (ou [] si absent/illisible)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("players", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _read_archived_players():
    """Joueurs de la dernière saison archivée (complète), ou [] si aucune."""
    try:
        import seasons as S
        arch_sid = S.latest_archived()
        if arch_sid:
            arch_stats_path, _ = S.archive_paths(arch_sid)
            return _read_players(str(arch_stats_path))
    except Exception:
        pass
    return []


def _aligned_share_with_sample(players, owned, fill_gp_max=FILL_GP_MAX):
    """Part des joueurs ALIGNÉS (on ice) ayant un échantillon exploitable (gp>max).

    Mesure sur tout le pool (toutes équipes) : signal ligue de « maturité » de la
    saison. Les joueurs alignés absents des stats comptent comme gp=0.
    """
    gp_by = {er.norm_name(p.get("name")): (p.get("gp") or 0) for p in players}
    aligned = [k for k, e in owned.items() if e.get("on_ice", True)]
    if not aligned:
        return 0.0
    have = sum(1 for k in aligned if gp_by.get(k, 0) > fill_gp_max)
    return have / len(aligned)


def select_conf_players(owned, min_share=CONF_LIVE_MIN_SHARE,
                        fill_gp_max=FILL_GP_MAX):
    """(players, source) pour la confrontation, avec choix live vs archivée.

    Utilise la saison en cours (nhl_stats.json) seulement si assez de joueurs
    alignés ont un échantillon réel ; sinon la dernière saison complète. Partagé
    par le log et l'onglet pour qu'ils soient cohérents.
    """
    live = _read_players("nhl_stats.json")
    if live and _aligned_share_with_sample(live, owned, fill_gp_max) >= min_share:
        return live, "live"
    arch = _read_archived_players()
    if arch:
        return arch, "archived"
    return live, "live"


# ----------------------------------------------------------------------
# Point d'entrée : enregistre un snapshot / finalise la période écoulée
# ----------------------------------------------------------------------
def record(season):
    """Enregistre le snapshot du jour pour `season` (année ESPN). Idempotent.

    Retourne un dict résumé ({'period', 'opponent', 'finalized'|None}).
    """
    owned, my_id, schedule = er.fetch_rosters(season)
    cur_period = schedule.get("current_period")
    if cur_period is None or not schedule.get("matchups"):
        return {"skipped": "calendrier H2H indisponible"}

    teams_by_id = schedule.get("teams_by_id", {})
    my_team = teams_by_id.get(str(my_id))
    if not my_team:
        return {"skipped": "équipe introuvable"}

    players, dataset = select_conf_players(owned)
    teams, _ = de.aggregate_by_team(players, owned,
                                    respect_on_ice=RESPECT_ON_ICE)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = load_log()
    skey = str(season)
    sdata = data["seasons"].setdefault(skey, {"current": None, "history": []})

    cur = sdata.get("current")
    finalized = None

    # La période a avancé -> finaliser la précédente avant de démarrer la nouvelle.
    if cur and cur.get("period") is not None and cur["period"] < cur_period:
        finalized = _finalize(cur, schedule, my_id)
        sdata["history"].append(finalized)
        cur = None

    opp = er.opponent_for({"schedule": schedule}, my_id, cur_period)

    # (Re)démarrage d'une période : nouvelle entrée courante.
    if cur is None or cur.get("period") != cur_period:
        cur = {
            "period": cur_period,
            "my_team": my_team,
            "opponent": opp,
            "started": today,
            "injury_seen": {},
            "injury_events": [],
        }

    # Rafraîchir prédiction + résultat live + blessures du jour.
    cur["opponent"] = opp
    cur["last_snapshot"] = today
    cur["dataset"] = dataset  # 'live' (saison en cours) ou 'archived' (complète)
    if opp:
        cur["prediction"] = _prediction(my_team, opp, teams)
        opp_id = _team_id_of(teams_by_id, opp)
        cur["actual_live"] = _actual(schedule, cur_period, my_id, opp_id)
        _track_injuries(cur, owned, my_team, opp, today)

    sdata["current"] = cur
    _save_log(data)
    return {"period": cur_period, "opponent": opp,
            "finalized_period": finalized.get("period") if finalized else None}


def _team_id_of(teams_by_id, name):
    for tid, nm in teams_by_id.items():
        if nm == name:
            return tid
    return None


def _finalize(cur, schedule, my_id):
    """Fige une période écoulée : prédiction finale + résultat réel + blessures."""
    period = cur.get("period")
    opp = cur.get("opponent")
    opp_id = _team_id_of(schedule.get("teams_by_id", {}), opp)
    actual = _actual(schedule, period, my_id, opp_id)
    pred = cur.get("prediction") or {}
    hit = None
    if actual and pred:
        hit = pred.get("predicted_winner") == actual.get("winner")
    return {
        "period": period,
        "my_team": cur.get("my_team"),
        "opponent": opp,
        "prediction": pred,
        "actual": actual,
        "predicted_correctly": hit,
        "injuries": cur.get("injury_events", []),
        "finalized_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


if __name__ == "__main__":
    import seasons as S
    yr = S.espn_season_for_mode(False)  # saison en cours
    print(record(yr))
