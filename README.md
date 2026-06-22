# 🏒 Hockey Pool — Stats, Contrats & Gestion d'équipe

Application [Streamlit](https://streamlit.io/) de gestion de pool de hockey (fantasy)
qui combine plusieurs sources de données pour suivre les joueurs, planifier un
repêchage et analyser les confrontations de la ligue.

## Sources de données

| Source | Données | Méthode |
|---|---|---|
| **API NHL** (`api.nhle.com`) | Stats joueurs (patineurs + gardiens), âge | Collecte quotidienne |
| **PuckPedia** (`puckpedia.com`) | Contrats : cap hit, statut, expiration, clauses | API JSON, à la demande |
| **ESPN Fantasy** (`fantasy.espn.com`) | Rosters du pool (qui possède qui) | API privée, cookies |

Les jointures se font par `playerId` (NHL ↔ contrats) et par nom normalisé
(NHL ↔ ESPN).

## Fonctionnalités

- **Onglets Patineurs / Gardiens** : toutes les stats + contrat + appartenance au pool, avec recherche, filtres combinables et coloration des lignes (mon équipe en bleu, autres DG en gris).
- **Onglet Équipe & Repêchage** :
  - Score de **Valeur** (z-scores pondérés par catégorie, projetés sur 82 matchs, plancher à 0 pour ne pas pénaliser les faiblesses) et **Valeur/$M** (efficience cap).
  - Tiers relatifs par position (Forwards / Défenseurs / Gardiens).
  - Bonus de jeunesse ajustable.
  - Suivi du cap salarial (102 M$, modifiable) et des slots par position.
  - Modes **Repêchage** (protéger/laisser, max 8) et **Saison** (alignement, échanges).
  - Tableau éditable On ice / Bench / Gardé.
- **Onglet Confrontations** : tableau croisé DG × catégories (forces/faiblesses), classements des DG par Valeur et Valeur/$M, et simulateur de duel 1v1.

## Fichiers du projet

**Code :**
- `app.py` — interface Streamlit
- `update_stats.py` — collecte des stats NHL → `nhl_stats.json`
- `update_contracts.py` — contrats PuckPedia → `nhl_contracts.json`
- `espn_roster.py` — rosters ESPN → `espn_owned.json`
- `draft_engine.py` — moteur de scoring et persistance du repêchage

**Données et choix (versionnés pour usage multi-ordi) :**
- `nhl_stats.json`, `nhl_contracts.json`, `espn_owned.json` — données
- `draft_plan.json` — statuts des joueurs (protégé / cible / autre DG)
- `lineup.json` — alignement on ice / bench
- `.env` — identifiants ESPN (⚠️ voir avertissement sécurité)

**Config :**
- `requirements.txt`, `.gitignore`, `.env.example`

## ⚠️ Avertissement sécurité

Le fichier `.env` contient tes **cookies de session ESPN**. Pour pouvoir utiliser
l'app sur plusieurs ordinateurs sans reconfiguration, ce projet les inclut dans le
dépôt. **Ce dépôt doit donc impérativement rester PRIVÉ.** Ne jamais le rendre
public ni y inviter des personnes non autorisées tant que le `.env` y figure.

Les cookies ESPN expirent périodiquement ; il faudra les rafraîchir de temps en
temps (voir « Configurer ESPN » ci-dessous).

## Installation

```bash
git clone https://github.com/<utilisateur>/hockey-pool.git
cd hockey-pool
python -m pip install -r requirements.txt
```

Comme les données sont incluses dans le dépôt, l'application fonctionne dès le
clone, sans rien lancer d'autre :

```bash
python -m streamlit run app.py
```

L'app s'ouvre sur http://localhost:8501

## Configurer ESPN (première fois ou cookies expirés)

1. Connecte-toi à ta ligue sur `fantasy.espn.com`.
2. Ouvre les outils développeur (**F12**) → onglet **Application** → **Cookies** → `https://fantasy.espn.com`.
3. Copie les valeurs des cookies **`SWID`** et **`espn_s2`**.
4. Crée (ou édite) le fichier `.env` à partir de `.env.example` :

```
ESPN_LEAGUE_ID=12957
ESPN_SEASON=2026
ESPN_TEAM_ID=12
ESPN_SWID={ton_swid}
ESPN_S2=ta_valeur_espn_s2
```

Sans `.env`, l'app fonctionne quand même, mais les fonctions liées au pool
(onglet Confrontations, section « Mon équipe ESPN ») resteront vides.

## Mettre à jour les données

- **Stats NHL** : `python update_stats.py` (ou via une tâche planifiée quotidienne).
- **Contrats** : bouton **🔄 Contrats** dans l'app.
- **Rosters du pool** : bouton **🏒 Pool** dans l'app.

## Utilisation sur plusieurs ordinateurs

Comme les données et tes choix (`draft_plan.json`, `lineup.json`) sont versionnés,
tu peux travailler sur plusieurs machines en synchronisant via Git.

**Sur l'ordinateur où tu as fait des changements** (protégés, alignement, etc.) :

```bash
git add -A
git commit -m "Mise à jour de mon équipe"
git push
```

**Sur l'autre ordinateur, avant de commencer** :

```bash
git pull
```

⚠️ **Travaille sur un seul ordinateur à la fois** et fais toujours `git pull` avant
de modifier. Si tu modifies les mêmes fichiers sur deux machines sans synchroniser
entre les deux, Git signalera un conflit à résoudre manuellement.

## Automatisation des stats (Windows — Planificateur de tâches)

Pour rafraîchir les stats chaque matin :

1. Ouvre le **Planificateur de tâches** Windows.
2. **Créer une tâche de base** → déclencheur **quotidien**.
3. Action **Démarrer un programme** :
   - Programme : `python`
   - Arguments : `update_stats.py`
   - Démarrer dans : le dossier du projet.

## Notes sur le scoring

- **Projection 82 matchs** : les stats cumulatives sont ramenées à un rythme sur 82 matchs, pour ne pas pénaliser un joueur ayant manqué des matchs (blessure).
- **Plancher à 0** : un joueur ne perd pas de points pour ses catégories faibles ; il en gagne seulement pour ses forces (un marqueur pur n'est pas puni pour ses mises en échec basses).
- **Tiers par position** : un défenseur est comparé aux autres défenseurs, pas aux attaquants.
- **Valeur/$M** : non calculée pour les joueurs sans contrat connu.

## Avertissement général

Projet personnel à but non commercial, non affilié à la NHL, à PuckPedia ni à ESPN.
Respecte les conditions d'utilisation de chaque source de données.