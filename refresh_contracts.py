#!/usr/bin/env python
# refresh_contracts.py
"""Mise à jour manuelle des contrats — à lancer EN LOCAL, de temps en temps.

PuckPedia (Cloudflare) renvoie 403 aux IP des datacenters (Streamlit Cloud ET
runners GitHub Actions) : seule une IP locale (résidentielle/corpo) peut fetcher.
Ce script fait tout en une commande, sans lancer Streamlit :

    1. fetch PuckPedia         -> réécrit nhl_contracts.json
    2. git add nhl_contracts.json
    3. commit (si le fichier a changé)
    4. push                    -> Streamlit Cloud le récupère au redéploiement

Usage :
    python refresh_contracts.py           # fetch + commit + push
    python refresh_contracts.py --no-push  # fetch + commit seulement
    python refresh_contracts.py --dry-run  # fetch seulement (aucun git)
"""

import argparse
import subprocess
import sys
from pathlib import Path

import update_contracts as uc

REPO = Path(__file__).resolve().parent
CONTRACTS_FILE = REPO / uc.CONTRACTS_FILE


def _git(*args, check=True):
    """Lance une commande git dans le repo et renvoie le CompletedProcess."""
    return subprocess.run(
        ["git", *args], cwd=REPO, check=check,
        capture_output=True, text=True, encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-push", action="store_true",
                        help="commit sans pousser vers le remote")
    parser.add_argument("--dry-run", action="store_true",
                        help="récupère les contrats sans toucher à git")
    args = parser.parse_args()

    # 1. Fetch PuckPedia --------------------------------------------------
    print("-> Récupération des contrats depuis PuckPedia…")

    def cb(done, total, msg):
        print(f"  {msg}")

    try:
        summary = uc.update_contracts(progress_cb=cb)
    except Exception as e:
        print(f"\n[ERREUR] Échec du fetch PuckPedia : {type(e).__name__}: {e}")
        print("  (Un 403 signifie que tu n'es PAS sur une IP locale autorisée.)")
        return 1

    print(f"[OK] {summary['scraped']} contrats écrits dans {uc.CONTRACTS_FILE}")

    if args.dry_run:
        print("-> --dry-run : aucune action git.")
        return 0

    # 2/3. Stage + commit -------------------------------------------------
    _git("add", str(CONTRACTS_FILE))
    staged = _git("diff", "--staged", "--quiet", check=False)
    if staged.returncode == 0:
        print("-> Aucun changement dans nhl_contracts.json — rien à committer.")
        return 0

    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _git("commit", "-m", f"chore: màj manuelle des contrats {stamp}")
    print(f"[OK] Commit créé (contrats {stamp}).")

    # 4. Push -------------------------------------------------------------
    if args.no_push:
        print("-> --no-push : commit local seulement. Pense à `git push`.")
        return 0

    print("-> git push…")
    pushed = _git("push", check=False)
    if pushed.returncode != 0:
        print(f"[ERREUR] git push a échoué :\n{pushed.stderr.strip()}")
        print("  Le commit local est fait — relance `git push` manuellement.")
        return 1
    print("[OK] Poussé. Streamlit Cloud récupérera la MàJ au prochain redéploiement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
