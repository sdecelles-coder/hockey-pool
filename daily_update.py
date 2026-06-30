# daily_update.py
"""Mise à jour automatique quotidienne des fichiers JSON.
Lancé chaque matin à 5h15 par le Planificateur de tâches Windows.

Automatisé :
  nhl_stats.json    <- API publique NHL  (toujours possible)
  espn_owned.json   <- API ESPN Fantasy  (nécessite credentials .env)
  nhl_contracts.json <- PuckPedia        (cloudscraper, peut échouer si Cloudflare bloque)

Non automatisé (choix manuels) :
  draft_plan.json, lineup.json
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).parent / "daily_update.log"
MAX_LOG_LINES = 500  # garde les 500 dernières lignes pour éviter que le log grossisse trop


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def trim_log():
    if not LOG_FILE.exists():
        return
    lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
    if len(lines) > MAX_LOG_LINES:
        LOG_FILE.write_text("\n".join(lines[-MAX_LOG_LINES:]) + "\n", encoding="utf-8")


def update_stats():
    import update_stats as us
    log("--- Stats NHL (nhl_stats.json) ---")
    us.main()
    log("Stats NHL OK.")


def update_espn():
    import espn_roster as er
    log("--- Pool ESPN (espn_owned.json) ---")
    res = er.update_owned()
    log(f"Pool ESPN OK : {res['count']} joueurs.")


def update_contracts():
    import update_contracts as uc
    log("--- Contrats PuckPedia (nhl_contracts.json) ---")
    res = uc.update_contracts()
    log(f"Contrats OK : {res['scraped']} contrats, {len(res['errors'])} erreur(s).")
    for err in res["errors"]:
        log(f"  ERREUR contrats : {err}")


TASKS = [
    ("Stats NHL",         update_stats),
    ("Pool ESPN",         update_espn),
    ("Contrats PuckPedia", update_contracts),
]


def main():
    trim_log()
    log("=" * 60)
    log("Début mise à jour quotidienne")

    success = 0
    for name, fn in TASKS:
        try:
            fn()
            success += 1
        except Exception:
            log(f"ÉCHEC — {name} :")
            for line in traceback.format_exc().splitlines():
                log(f"  {line}")

    log(f"Fin mise à jour : {success}/{len(TASKS)} tâches réussies")
    log("=" * 60)
    return 0 if success == len(TASKS) else 1


if __name__ == "__main__":
    sys.exit(main())
