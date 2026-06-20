# update_stats.py
"""Collecteur quotidien des stats NHL -> nhl_stats.json
Lancé par une tâche planifiée Windows chaque matin."""

import json
import re
import unicodedata
from datetime import datetime, timezone

import requests

SEASON = "20252026"
GAME_TYPE = 2  # 2 = saison régulière
OUT_FILE = "nhl_stats.json"
TIMEOUT = 30

STATS_BASE = "https://api.nhle.com/stats/rest/en"


def make_slug(full_name: str) -> str:
    """McDavid, Connor / 'Connor McDavid' -> 'connor-mcdavid'."""
    s = unicodedata.normalize("NFKD", full_name)
    s = s.encode("ascii", "ignore").decode("ascii")  # retire accents
    s = s.lower().strip()
    s = re.sub(r"[.']", "", s)          # enlève points/apostrophes
    s = re.sub(r"[^a-z0-9]+", "-", s)   # tout le reste -> tiret
    return s.strip("-")

def current_team(team_abbrevs: str) -> str:
    """'ANA,CGY' -> 'CGY' (dernière = équipe actuelle)."""
    if not team_abbrevs:
        return None
    return team_abbrevs.split(",")[-1].strip()

def fetch_all(endpoint: str, sort_prop: str) -> list:
    """Pagine un endpoint stats NHL (limit 100 par page)."""
    results = []
    start = 0
    limit = 100
    while True:
        params = {
            "isAggregate": "false",
            "isGame": "false",
            "sort": json.dumps([{"property": sort_prop, "direction": "DESC"}]),
            "start": start,
            "limit": limit,
            "cayenneExp": f"seasonId={SEASON} and gameTypeId={GAME_TYPE}",
        }
        r = requests.get(f"{STATS_BASE}/{endpoint}", params=params, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data", [])
        results.extend(data)
        total = payload.get("total", 0)
        start += limit
        if start >= total or not data:
            break
    return results

def collect_skaters() -> list:
    summary = fetch_all("skater/summary", "points")
    realtime = fetch_all("skater/realtime", "hits")

    # index realtime par playerId pour fusion rapide
    rt = {r["playerId"]: r for r in realtime}

    out = []
    for p in summary:
        pid = p.get("playerId")
        extra = rt.get(pid, {})
        name = p.get("skaterFullName", "")
        out.append({
            "playerId": pid,
            "name": name,
            "puckpedia_slug": make_slug(name),
            "type": "skater",
            "team": current_team(p.get("teamAbbrevs")),
            "teams_all": p.get("teamAbbrevs"),
            "position": p.get("positionCode"),
            "gp": p.get("gamesPlayed"),
            "goals": p.get("goals"),
            "assists": p.get("assists"),
            "points": p.get("points"),
            "plus_minus": p.get("plusMinus"),
            "pim": p.get("penaltyMinutes"),
            "ppp": p.get("ppPoints"),
            "sog": p.get("shots"),
            "hits": extra.get("hits"),
        })
    return out

def collect_goalies() -> list:
    rows = fetch_all("goalie/summary", "wins")
    out = []
    for g in rows:
        name = g.get("goalieFullName", "")
        out.append({
            "playerId": g.get("playerId"),
            "name": name,
            "puckpedia_slug": make_slug(name),
            "type": "goalie",
            "team": current_team(g.get("teamAbbrevs")),
            "teams_all": g.get("teamAbbrevs"),
            "position": "G",
            "gp": g.get("gamesPlayed"),
            "wins": g.get("wins"),
            "losses": g.get("losses"),
            "ot_losses": g.get("otLosses"),
            "gaa": g.get("goalsAgainstAverage"),
            "sv_pct": g.get("savePct"),
            "shutouts": g.get("shutouts"),
            "saves": g.get("saves"),
        })
    return out


def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Collecte stats NHL...")
    skaters = collect_skaters()
    print(f"  patineurs: {len(skaters)}")
    goalies = collect_goalies()
    print(f"  gardiens : {len(goalies)}")

    db = {
        "season": SEASON,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "players": skaters + goalies,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    print(f"  -> {OUT_FILE} écrit ({len(db['players'])} joueurs)")


if __name__ == "__main__":
    main()