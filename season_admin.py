# season_admin.py
"""Bascules MANUELLES de saison (~2x/an). Voir docs/PLAN-stats-multi-saison.md.

Usage :
  python season_admin.py close            # fin de saison : fige stats+contrats, passe en tampon
  python season_admin.py open 20262027    # début nouvelle saison : active la nouvelle saison
  python season_admin.py status           # affiche l'état courant

Après un `close` ou un `open`, penser à committer :
  git add seasons.json archive/ && git commit -m "chore: bascule saison"
"""
import shutil
import sys

import seasons as S

# Console Windows en cp1252 : force UTF-8 pour les accents/emojis des messages.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def close():
    """Fin de saison régulière : fige la saison courante et bascule en tampon."""
    m = S.load_manifest()
    cur = m["current_season"]

    if not S.LIVE_STATS.exists() or not S.LIVE_CONTRACTS.exists():
        print("ERREUR : fichiers racine introuvables — lance d'abord update_stats.py "
              "et update_contracts.py.")
        return 1

    s_path, c_path = S.archive_paths(cur)
    s_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(S.LIVE_STATS, s_path)          # stats figées
    shutil.copyfile(S.LIVE_CONTRACTS, c_path)      # contrats gelés (fin de saison)

    if cur not in m["archived"]:
        m["archived"].append(cur)
    m["phase"] = "offseason"
    m["upcoming_season"] = S.next_season_id(cur)
    S.save_manifest(m)

    print(f"✅ Saison {S.fmt_season(cur)} clôturée et archivée dans {s_path.parent}/")
    print(f"   Phase : offseason — tampon prête pour le repêchage "
          f"{S.fmt_season(m['upcoming_season'])}.")
    print("   (stats de la tampon = stats finales de la saison, contrats = live quotidien)")
    print("\nN'oublie pas : git add seasons.json archive/ && git commit")
    return 0


def open_season(new_sid):
    """Début de la nouvelle saison : la racine passe à la nouvelle saison."""
    if not (len(new_sid) == 8 and new_sid.isdigit()):
        print(f"ERREUR : id de saison invalide '{new_sid}' (attendu 8 chiffres, ex. 20262027).")
        return 1

    m = S.load_manifest()
    m["current_season"] = new_sid
    m["phase"] = "active"
    m["upcoming_season"] = ""
    S.save_manifest(m)
    print(f"✅ Nouvelle saison active : {S.fmt_season(new_sid)}.")

    # Repeuple immédiatement les stats racine (sinon elles gardent les anciennes
    # valeurs jusqu'au prochain run quotidien).
    try:
        import update_stats
        print("   Récupération des stats de la nouvelle saison…")
        update_stats.main()
        print("   Stats à jour.")
    except Exception as e:
        print(f"   (Stats non repeuplées maintenant : {e})")
        print("   Le job quotidien s'en chargera, ou lance `python update_stats.py`.")

    print("\nN'oublie pas : git add seasons.json nhl_stats.json && git commit")
    return 0


def status():
    m = S.load_manifest()
    print(f"Saison racine (live) : {S.fmt_season(m['current_season'])}")
    print(f"Phase                : {m['phase']}")
    if m["phase"] == "offseason":
        up = m.get("upcoming_season") or S.next_season_id(m["current_season"])
        print(f"Tampon repêchage     : {S.fmt_season(up)}")
    archived = ", ".join(S.fmt_season(s) for s in sorted(m.get("archived", []), reverse=True))
    print(f"Saisons archivées    : {archived or '(aucune)'}")
    return 0


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "close":
        return close()
    if cmd == "open":
        if len(argv) < 2:
            print("Usage : python season_admin.py open <season_id>  (ex. 20262027)")
            return 1
        return open_season(argv[1])
    if cmd == "status":
        return status()
    print(f"Commande inconnue : {cmd}")
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
