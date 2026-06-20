# 🏒 NHL Stats & Contrats

Dashboard [Streamlit](https://streamlit.io/) qui combine les **statistiques** des
joueurs de la NHL (saison en cours) avec leurs **contrats** (cap hit, statut,
expiration), patineurs et gardiens.

- **Stats** : API officielle NHL (`api.nhle.com`) — rafraîchies quotidiennement.
- **Contrats** : API JSON publique de PuckPedia — récupérés à la demande.
- **Jointure** : `playerId` (NHL) == `nhl_id` (PuckPedia).

## Aperçu des fonctionnalités

- Onglets **Patineurs** et **Gardiens** avec stats détaillées (G, A, Pts, +/-, PIM, PPP, SOG, HIT / V, D, GAA, %Arr, BL).
- Colonnes contrat : **Cap Hit**, **Signing status**, **Expiry** (statut + année), **Clauses** (NMC/NTC).
- Recherche par nom et filtre par équipe.
- Bouton **Update All contracts** pour rafraîchir les données de contrat.

## Architecture

| Fichier | Rôle |
|---|---|
| `update_stats.py` | Collecte les stats NHL → `nhl_stats.json`. Conçu pour une tâche planifiée quotidienne. |
| `update_contracts.py` | Récupère les contrats PuckPedia → `nhl_contracts.json`. Appelé par le bouton de l'app. |
| `app.py` | Interface Streamlit : lit les deux JSON, fait la jointure, affiche les tableaux. |

Les deux fichiers `.json` sont **générés localement** et ne sont pas versionnés
(voir `.gitignore`).

## Installation

```bash
git clone https://github.com/<utilisateur>/<repo>.git
cd <repo>
python -m pip install -r requirements.txt
```

## Utilisation

### 1. Générer les statistiques (première fois, puis chaque matin)

```bash
python update_stats.py
```

Crée/met à jour `nhl_stats.json` (~1000 joueurs).

### 2. Lancer le dashboard

```bash
python -m streamlit run app.py
```

L'app s'ouvre sur http://localhost:8501

### 3. Récupérer les contrats

Au premier lancement, les contrats sont vides. Clique sur **🔄 Update All
contracts** dans l'app pour les récupérer depuis PuckPedia (~10 secondes).

## Automatisation des stats (Windows – Planificateur de tâches)

Pour rafraîchir les stats automatiquement chaque matin :

1. Ouvre le **Planificateur de tâches** Windows.
2. **Créer une tâche de base** → déclencheur **quotidien** (ex. 7h00).
3. Action : **Démarrer un programme**
   - Programme : `python`
   - Arguments : `update_stats.py`
   - Démarrer dans : le chemin du dossier du projet.

## Sources de données

- **NHL Stats API** : `https://api.nhle.com/stats/rest/en/` (non officielle mais publique et stable).
- **PuckPedia** : `https://puckpedia.com/players/api` (API JSON publique alimentant le dashboard du site).

## Avertissement

Projet personnel à but non commercial. Les données de contrat proviennent de
PuckPedia ; respecte leurs conditions d'utilisation. Ce projet n'est affilié ni
à la NHL ni à PuckPedia.

## Licence

MIT — voir [`LICENSE`](LICENSE).
