# Plan d'implémentation — Stats multi-saisons + sélecteur de saison

> **Statut : PLAN uniquement. Aucun code n'est modifié dans cette branche.**
> Branche : `plan/stats-multi-saison`. Ce document décrit *quoi* changer et *pourquoi*,
> pas encore *le code*.

## 1. Objectif

- Conserver les **stats de chaque saison** en archive (figées une fois la saison terminée).
- Un **sélecteur (dropdown)** dans l'app pour consulter n'importe quelle saison ; par
  défaut la plus récente.
- **Bascule manuelle 2×/an** (choix retenu) : pas d'auto-détection de la saison NHL.
- Comportement souhaité par phase :

| Vue | Stats | Contrats |
|---|---|---|
| Saison terminée (ex. 2025-2026) | figées | **gelées** (instantané fin de saison) |
| Tampon / repêchage (avant le début de 2026-2027) | empruntées à 2025-2026 (figées) | **live, MàJ quotidienne** |
| Saison active (2026-2027 en cours) | réelles, MàJ quotidienne | live |

Hors périmètre (assumé OK) : âge, équipe, propriété ESPN et statut retraités restent
« actuels » — non historisés.

## 2. Idée-force qui simplifie tout

Le « live » reste **toujours** les fichiers racine actuels
([nhl_stats.json](../nhl_stats.json) + [nhl_contracts.json](../nhl_contracts.json)),
mis à jour quotidiennement **exactement comme aujourd'hui**. On ne change pas le pipeline
de collecte, on ajoute seulement :

1. un **dossier d'archives** de saisons figées ;
2. un **manifeste** (`seasons.json`) qui dit quelle saison le racine représente et dans
   quelle phase on est ;
3. un **sélecteur** dans l'app qui choisit *d'où* charger stats + contrats.

**La saison tampon n'est PAS une copie de fichier** : pendant l'entre-saison, les
fichiers racine contiennent naturellement « stats finales de N + contrats live » — c'est
déjà, tel quel, la saison tampon. Aucun copié-collé à maintenir. Quand la vraie saison
N+1 démarre, le racine se met à accumuler les vraies stats N+1 et la tampon disparaît
d'elle-même.

Le gros avantage : dans [app.py](../app.py) il n'y a **qu'un seul point de chargement**
des stats ([app.py:216](../app.py#L216)) et des contrats ([app.py:237](../app.py#L237)).
Tout le reste (tableaux, z-scores, moteur de repêchage, confrontations, coloration)
consomme `players` et `cache`. Rediriger ces deux chargements suffit à faire suivre toute
l'app.

## 3. Disposition des fichiers (nouveau)

```
seasons.json                 # manifeste (versionné) — voir §4
archive/
  20242025/
    stats.json               # figé (copie de nhl_stats.json au moment du gel)
    contracts.json           # figé (copie de nhl_contracts.json au moment du gel)
  20252026/
    stats.json
    contracts.json
nhl_stats.json               # RACINE / live — inchangé, MàJ quotidienne
nhl_contracts.json           # RACINE / live — inchangé, MàJ quotidienne
```

## 4. Manifeste `seasons.json`

Petit fichier versionné, source de vérité pour l'app **et** pour `update_stats.py`.

```json
{
  "current_season": "20252026",   // saison que les fichiers RACINE représentent
  "phase": "offseason",           // "active" | "offseason"
  "upcoming_season": "20262027",  // libellé de la tampon en phase offseason
  "archived": ["20242025", "20252026"]
}
```

- **`phase: active`** → racine = saison N en cours (stats + contrats live).
- **`phase: offseason`** → racine = stats finales de N (figées de fait) + contrats live ;
  sert de **tampon** pour le repêchage de `upcoming_season`.

## 5. Les 2 bascules manuelles (~2×/an)

Un petit script d'admin, ex. `season_admin.py`, avec deux commandes. Aucune édition de
code nécessaire — juste lancer la commande au bon moment.

### Bascule A — Clôturer la saison (fin avril, saison régulière terminée)
`python season_admin.py close`
1. Copie `nhl_stats.json` → `archive/<current_season>/stats.json`.
2. Copie `nhl_contracts.json` → `archive/<current_season>/contracts.json` (← gel des
   contrats à leur valeur de fin de saison).
3. Manifeste : `phase = "offseason"`, ajoute `current_season` à `archived`, renseigne
   `upcoming_season`.
4. Commit + push des nouveaux fichiers `archive/…` et de `seasons.json`.

→ Dès lors, les fichiers racine = tampon (stats finales N + contrats live). Le job
quotidien continue de rafraîchir les **contrats** (tampon reste live) ; le rafraîchissement
des **stats** devient idempotent (mêmes chiffres finaux) — voir §7 pour l'option de le
désactiver.

### Bascule B — Démarrer la nouvelle saison (octobre, 1er match N+1)
`python season_admin.py open 20262027`
1. Manifeste : `current_season = "20262027"`, `phase = "active"`.
2. (Recommandé) lance immédiatement `update_stats.main()` pour repeupler
   `nhl_stats.json` avec la nouvelle saison (sinon le racine garde les vieux chiffres
   jusqu'au prochain run quotidien).
3. Commit + push.

→ Le racine accumule désormais les vraies stats N+1. L'archive `20252026` reste figée
avec ses contrats gelés. La tampon disparaît du sélecteur.

## 6. Modifications de code (fichier par fichier)

### 6.1 `update_stats.py`
- **Retirer la constante figée** `SEASON = "20252026"` ([update_stats.py:19](../update_stats.py#L19)).
- Lire la saison depuis `seasons.json` → `current_season`. (C'est ce qui rend la Bascule B
  « sans édition de code » : `season_admin.py open` modifie le manifeste, et le prochain
  run lit la nouvelle valeur.)
- Aucune autre logique ne change ; l'API NHL sert la saison demandée via `seasonId`
  ([update_stats.py:73](../update_stats.py#L73)).

### 6.2 `app.py` — sélecteur + double redirection de chargement
- **Charger le manifeste** au démarrage et construire la **liste des saisons**
  sélectionnables :
  - si `phase == offseason` : entrée tampon en tête, libellée
    `"2026-2027 (repêchage — stats 2025-2026)"`, **défaut** ;
  - puis les saisons `archived` (ordre décroissant), libellées `"2025-2026"`, etc. ;
  - si `phase == active` : entrée `"2026-2027 (en cours)"` en tête + archives.
- **Placer un `st.selectbox`** avant les chargements (ex. dans la sidebar). Le pattern est
  déjà partout dans le fichier (nombreux `st.selectbox`, ex.
  [app.py:566](../app.py#L566)).
- **Résoudre les chemins** selon la sélection :
  - tampon / active → `nhl_stats.json` + `nhl_contracts.json` (racine) ;
  - archive N → `archive/N/stats.json` + `archive/N/contracts.json`.
- **Rediriger les 2 points de chargement** :
  - stats : [app.py:216](../app.py#L216) `stats = load_json(STATS_FILE, None)`
    → `load_json(chemin_stats_choisi, None)` ;
  - contrats : [app.py:237](../app.py#L237) `contracts_db = load_json(CONTRACTS_FILE, …)`
    → `load_json(chemin_contrats_choisi, …)`.
- **Rien d'autre en aval ne change** : `players`, `cache`, la logique nouveaux/retraités
  ([app.py:250-271](../app.py#L250-L271)), la coloration, le moteur de repêchage — tout
  consomme `players`/`cache` et suivra automatiquement.

> ⚠️ Détail Streamlit : le bloc d'auto-refresh au démarrage
> ([app.py:122-214](../app.py#L122-L214)) rafraîchit les fichiers **racine** (live).
> Il n'affecte jamais les archives. Placer le sélecteur avant la ligne 216 suffit (rerun
> top-down). L'auto-refresh peut rester tel quel, quelle que soit la saison consultée.

### 6.3 `season_admin.py` (nouveau)
- Commandes `close` / `open <season_id>` décrites au §5.
- Réutilise `load_json` / écritures JSON déjà présentes ; opérations fichier simples
  (copie racine → `archive/<id>/`, mise à jour du manifeste).

### 6.4 `daily_update.py` / GitHub Actions
- **Aucun changement fonctionnel requis** : le job continue de mettre à jour les fichiers
  racine et de les committer ([update_json.yml:57-63](../.github/workflows/update_json.yml#L57-L63)).
- `update_stats.py` lira la saison dans `seasons.json` (déjà versionné), donc le job
  suit automatiquement la Bascule B sans modification du workflow.
- Les fichiers `archive/…` sont créés/commités par la Bascule A (manuelle) — pas besoin
  de les ajouter au commit quotidien.

## 7. Détails & décisions ouvertes

1. **Rafraîchir les stats en phase offseason ?** Techniquement idempotent (mêmes chiffres
   finaux). Option : dans `update_stats.py`, si `phase == offseason`, sauter la collecte
   stats (garder seulement contrats live). *Recommandation : le laisser tourner* (plus
   simple, sans risque) et trancher plus tard.
2. **Backfill d'anciennes saisons** : les **stats** passées sont récupérables via l'API
   NHL (gratuit) en lançant la collecte avec d'autres `seasonId` → possible de préremplir
   `archive/2023…`, `archive/2024…`. En revanche **PuckPedia ne fournit que les contrats
   courants** : les archives rétro-créées auraient des stats justes mais **pas** les
   contrats de l'époque. Le gel « correct » des contrats ne commence donc qu'à partir de
   la première Bascule A réelle. *Optionnel, à faire seulement si tu veux l'historique
   stats.*
3. **Cohérence interne d'une archive** : chaque archive figée est auto-cohérente (ses
   stats + ses contrats gelés). La logique nouveaux (ELC) / retraités
   ([app.py:250-271](../app.py#L250-L271)) fonctionne sur cette paire sans adaptation.
4. **Propriété ESPN & retraités** : restent « actuels » (assumé OK). Si un jour tu veux
   les historiser, même schéma (snapshot dans `archive/<id>/`), mais hors périmètre ici.
5. **Libellés du sélecteur** : à valider (format « 2025-2026 » vs « 2025-26 », mention
   « repêchage » sur la tampon…). Cosmétique, aucun impact technique.

## 8. Effort estimé

| Lot | Fichiers | Effort |
|---|---|---|
| Manifeste + lecture saison | `seasons.json`, `update_stats.py` | Faible |
| Script d'admin (close/open) | `season_admin.py` (nouveau) | Faible |
| Sélecteur + redirection chargement | `app.py` | Faible-modéré |
| (Optionnel) backfill stats passées | `update_stats.py` en boucle | Faible |
| Docs / README | `README.md` | Faible |

**Global : modéré, sans réécriture lourde.** Le cœur du risque est concentré dans les
deux lignes de chargement de `app.py` ; le reste est additif.

## 9. Ordre de mise en œuvre suggéré (quand tu donneras le feu vert)

1. Créer `seasons.json` (avec l'état actuel : `current_season=20252026`, `phase=active`).
2. Faire lire cette valeur par `update_stats.py` (retirer le hardcode).
3. Ajouter le sélecteur + redirection dans `app.py` (avec une seule saison au départ →
   comportement identique à aujourd'hui, non régressif).
4. Écrire `season_admin.py` (`close` / `open`).
5. (Optionnel) backfill des stats des saisons passées.
6. Mettre à jour `README.md` (procédure des 2 bascules).
7. Tester : Bascule A à blanc (gel 2025-2026) → vérifier la tampon → simuler Bascule B.
```
