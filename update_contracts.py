# update_contracts.py
"""Récupère contrats + statuts depuis l'API JSON de PuckPedia.

Source unique : https://puckpedia.com/players/api
- role=1 renvoie TOUS les joueurs (patineurs + gardiens confondus).
- data['p'] est parfois une liste, parfois un dict {'100': {...}} (quirk PHP)
  -> normalisé par _as_list.
- Jointure avec les stats NHL via nhl_id == playerId.

Récupération via une simple requête HTTP `requests` (l'API répond en HTTP 200
à un GET avec un User-Agent navigateur — plus besoin de Playwright/Chromium).

Deux modes :
- update_contracts()                 -> rafraîchit TOUS les contrats
- update_contracts_for(player_ids)   -> ne met à jour QUE les joueurs ciblés
"""

import json
import math
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests
import urllib3
import config

# Vérification SSL (désactivable via .env pour les réseaux d'entreprise qui
# inspectent le HTTPS avec un certificat racine maison).
VERIFY_SSL = config.get("VERIFY_SSL", "true").lower() == "true"
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CONTRACTS_FILE = "nhl_contracts.json"
STATS_FILE = "nhl_stats.json"  # sert à repérer les joueurs absents du flux liste
PAGE_SIZE = 100  # l'API PuckPedia plafonne à 100 joueurs par page
DELAY_SEC = 0.3  # pause entre pages (rester bien sous le timeout d'ouverture de l'app)
PROFILE_DELAY = 0.2  # pause entre fetchs de fiches individuelles (fallback)
TIMEOUT = 30  # secondes pour requests
API_BASE = "https://puckpedia.com/players/api?q="
PROFILE_BASE = "https://puckpedia.com/player/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}

# PuckPedia encode chaque saison par un id entier :
#   2025-2026 -> 162, 2026-2027 -> 163, 2030-2031 -> 167, ...
#   soit id = (année de début de saison) - SEASON_ID_BASE.
# La saison "contrat" NHL bascule le 1er juillet ; avant on code en dur "162"
# (2025-2026), ce qui renvoyait les contrats d'une saison périmée (ex. l'ancien
# ELC de Brandt Clarke au lieu de sa prolongation). On calcule donc la saison
# courante à partir de la date.
SEASON_ID_BASE = 1863  # 2025 - 162


def current_season_id(today=None):
    """Id PuckPedia de la saison de contrat courante (bascule le 1er juillet)."""
    d = today or datetime.now(timezone.utc)
    start_year = d.year if d.month >= 7 else d.year - 1
    return start_year - SEASON_ID_BASE


def build_url(role, page, size=PAGE_SIZE, season=None):
    if season is None:
        season = current_season_id()
    # NB : PAS de filtre "player_active". Le limiter à ["1"] écartait les
    # joueurs sous contrat mais non-actifs sur le roster NHL (assignés aux
    # mineures, ex. Reinbacher) — on veut 100% des contrats du flux.
    q = {
        "player_role": role,
        "sortBy": "cap_hit",
        "sortDirection": "DESC",
        "curPage": page,
        "pageSize": size,
        "focus_season": str(season),
        "stat_season": str(season),
    }
    return API_BASE + quote(json.dumps(q))


def _as_list(p):
    if isinstance(p, dict):
        return list(p.values())
    return p


def _to_int(v):
    """Convertit un cap_hit PuckPedia (entier ou décimal, ex. '11567857.14') en int."""
    if not v:
        return 0
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


class _BrowserFetcher:
    """Fallback Chromium (Playwright) quand Cloudflare bloque `requests` avec un
    403/challenge JS. Un vrai navigateur résout le challenge ; on garde la page
    ouverte pour réutiliser les cookies de clearance sur toute la pagination."""

    def __init__(self):
        self._pw = None
        self._browser = None
        self._page = None

    def _ensure_page(self):
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._page = self._browser.new_page(user_agent=HEADERS["User-Agent"])

    def fetch(self, url):
        self._ensure_page()
        resp = self._page.goto(url, timeout=TIMEOUT * 1000)
        # Laisse le challenge Cloudflare (redirection JS) se résoudre.
        self._page.wait_for_timeout(3000)
        if resp is not None and resp.status == 403:
            resp = self._page.goto(url, timeout=TIMEOUT * 1000)
        body = self._page.inner_text("body")
        return json.loads(body)

    def close(self):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()


_browser_fetcher = None


def _fetch_via_browser(url):
    global _browser_fetcher
    try:
        import playwright  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "Cloudflare bloque `requests` (403/challenge JS) et Playwright "
            "n'est pas installé pour basculer en mode navigateur. Installe-le : "
            "pip install playwright && playwright install chromium"
        )
    if _browser_fetcher is None:
        _browser_fetcher = _BrowserFetcher()
    return _browser_fetcher.fetch(url)


def _close_browser_fetcher():
    global _browser_fetcher
    if _browser_fetcher is not None:
        _browser_fetcher.close()
        _browser_fetcher = None


def _fetch(url):
    """GET l'API PuckPedia et renvoie le JSON décodé.

    Bascule sur Chromium (Playwright) si Cloudflare renvoie un 403 — signe
    d'un blocage que `requests` ne peut pas résoudre seul."""
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, verify=VERIFY_SSL)
    if r.status_code == 403:
        return _fetch_via_browser(url)
    r.raise_for_status()
    return r.json()


def fetch_role(role, progress_cb=None, label=""):
    """Récupère tous les joueurs d'un rôle via pagination (requests)."""
    out = []

    data = _fetch(build_url(role, 1))["data"]
    out.extend(_as_list(data["p"]))
    total = data["meta"]["count"]
    pages = math.ceil(total / PAGE_SIZE)

    if progress_cb:
        progress_cb(1, pages, f"{label} page 1/{pages}")

    for p in range(2, pages + 1):
        time.sleep(DELAY_SEC)
        d = _fetch(build_url(role, p))["data"]
        out.extend(_as_list(d["p"]))
        if progress_cb:
            progress_cb(p, pages, f"{label} page {p}/{pages}")

    return out, total


def parse_player(p):
    return {
        "nhl_id": p.get("nhl_id"),
        "name": f"{p.get('p_fn', '')} {p.get('p_ln', '')}".strip(),
        "pos": p.get("pos"),
        "age": p.get("age"),
        "cap_hit_value": _to_int(p.get("cap_hit")),
        "signing_status": p.get("sts_sign"),
        "expiry_status": p.get("sts_exp"),
        "expiry_year": p.get("exp"),
        "clauses": p.get("clauses"),
        "contract_level": p.get("lvl"),
        "years_left": p.get("yr_left"),
        "ppg_points": p.get("st_ppg"),
        "puckpedia_url": p.get("p_url"),
    }


def load_cache():
    try:
        with open(CONTRACTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"contracts": {}}


def save_cache(contracts):
    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "contracts": contracts,
    }
    with open(CONTRACTS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------
# Fallback « fiche individuelle »
#
# Certains joueurs pourtant sous contrat sont ABSENTS du flux liste de
# PuckPedia (players/api), quel que soit le filtre — ex. Sam Malinski. Leur
# contrat n'existe que sur leur page profil (/player/<slug>). On la parse en
# dernier recours pour les joueurs qu'on connaît (nhl_stats.json) mais que le
# flux n'a pas renvoyés. Le HTML de la fiche est orienté affichage : on en tire
# de façon fiable cap hit / statut d'expiration / année d'expiration / statut de
# signature / position / âge. Les clauses ne sont PAS extractibles proprement
# (ventilées par année) -> laissées vides plutôt qu'erronées.
# ----------------------------------------------------------------------
def _load_stats_players():
    """Joueurs connus (nhl_stats.json) avec leur slug PuckPedia."""
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            return json.load(f).get("players", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def parse_profile(html, nhl_id, name=None, slug=None):
    """Extrait un contrat de la page profil PuckPedia. None si aucun contrat."""
    # Cap hit exact du contrat courant (onglet affiché par défaut sur la fiche).
    m = re.search(r"val-lg['\"]>\$([\d,]+)", html)
    if not m:
        return None  # pas de contrat courant affiché -> rien à enregistrer
    cap = int(m.group(1).replace(",", ""))

    # Titre du contrat sélectionné (ex. "2026-2030") depuis le bloc d'onglets.
    title = ""
    tm = re.search(r"options:\s*(\[.*?\]),\s*tabSelected:\s*(\d+)", html, re.S)
    if tm:
        try:
            opts = json.loads(tm.group(1))
            sel = int(tm.group(2))
            cur = next((o for o in opts if o.get("value") == sel),
                       opts[-1] if opts else {})
            title = cur.get("title", "") or ""
        except (ValueError, KeyError, TypeError):
            pass

    # expiry_year = dernière saison du contrat ("2026-2030" -> "2029-2030").
    # years_left = saisons restantes à partir de la saison courante.
    expiry_year = ""
    years_left = ""
    ym = re.search(r"(\d{4})-(\d{4})", title)
    if ym:
        end = int(ym.group(2))
        expiry_year = f"{end - 1}-{end}"
        start_year = current_season_id() + SEASON_ID_BASE
        years_left = str(max(0, end - start_year))

    def _tip(label):
        mm = re.search(label + r":\s*([A-Za-z0-9]+)", html)
        return mm.group(1) if mm else ""

    expiry_status = _tip("Expiry Status")
    signing_status = _tip("Signing Status")

    pm = re.search(r'pp_subset">pos</span><span[^>]*>([^<]+)</span>', html)
    am = re.search(r'pp_subset">age</span><span[^>]*>([^<]+)</span>', html)
    pos = pm.group(1).strip() if pm else ""
    age = am.group(1).strip() if am else ""

    return {
        "nhl_id": str(nhl_id),
        "name": name or "",
        "pos": pos,
        "age": age,
        "cap_hit_value": cap,
        "signing_status": signing_status,
        "expiry_status": expiry_status,
        "expiry_year": expiry_year,
        "clauses": "",              # non extractible proprement depuis la fiche
        "contract_level": "",
        "years_left": years_left,
        "ppg_points": "0.00",
        "puckpedia_url": slug or "",
        "source": "profile",        # trace: contrat venu du fallback fiche
    }


def fetch_contract_from_profile(slug, nhl_id, name=None):
    """GET la fiche profil et renvoie le contrat parsé (ou None)."""
    r = requests.get(PROFILE_BASE + slug, headers=HEADERS,
                     timeout=TIMEOUT, verify=VERIFY_SSL)
    r.raise_for_status()
    return parse_profile(r.text, nhl_id, name=name, slug=slug)


def _fill_missing_from_profiles(parsed_by_id, progress_cb=None):
    """Complète parsed_by_id via les fiches, pour les joueurs connus absents.

    Renvoie (added, failed) où failed est une liste de (nhl_id, message).
    """
    missing = [
        p for p in _load_stats_players()
        if str(p.get("playerId")) not in parsed_by_id and p.get("puckpedia_slug")
    ]
    total = len(missing)
    added = 0
    failed = []
    now = datetime.now(timezone.utc).isoformat()
    for i, p in enumerate(missing, 1):
        pid = str(p.get("playerId"))
        if progress_cb:
            progress_cb(i, total, f"Fiche {i}/{total} : {p.get('name', pid)}")
        try:
            rec = fetch_contract_from_profile(p["puckpedia_slug"], pid,
                                              name=p.get("name"))
        except Exception as e:
            failed.append((pid, f"{type(e).__name__}: {e}"))
            rec = None
        if rec:
            rec["scraped_at"] = now
            parsed_by_id[pid] = rec
            added += 1
        time.sleep(PROFILE_DELAY)
    return added, failed


def _fetch_all_parsed(progress_cb=None):
    all_players = []
    errors = []
    try:
        players, _ = fetch_role("1", progress_cb, "Joueurs")
        all_players.extend(players)
    except Exception as e:
        errors.append(("Joueurs", f"{type(e).__name__}: {e}"))

    _close_browser_fetcher()

    parsed_by_id = {}
    no_id = 0
    now = datetime.now(timezone.utc).isoformat()
    for p in all_players:
        if not isinstance(p, dict):
            continue
        parsed = parse_player(p)
        nhl_id = parsed["nhl_id"]
        if not nhl_id:
            no_id += 1
            continue
        parsed["scraped_at"] = now
        parsed_by_id[str(nhl_id)] = parsed
    return parsed_by_id, errors, no_id


def update_contracts(progress_cb=None, with_profile_fallback=True):
    parsed_by_id, errors, no_id = _fetch_all_parsed(progress_cb)
    if not parsed_by_id:
        raise RuntimeError(f"Aucun contrat récupéré — fichier non modifié. Erreurs : {errors}")

    from_feed = len(parsed_by_id)

    # Fallback fiches : rattrape les joueurs connus absents du flux liste.
    profile_added = 0
    profile_failed = []
    if with_profile_fallback:
        profile_added, profile_failed = _fill_missing_from_profiles(
            parsed_by_id, progress_cb)

    save_cache(parsed_by_id)
    return {
        "scraped": len(parsed_by_id),
        "from_feed": from_feed,
        "profile_added": profile_added,
        "profile_failed": profile_failed,
        "errors": errors,
        "no_id": no_id,
        "total_cached": len(parsed_by_id),
    }


def update_contracts_for(player_ids, progress_cb=None):
    targets = {str(pid) for pid in player_ids if pid is not None}
    parsed_by_id, errors, no_id = _fetch_all_parsed(progress_cb)

    db = load_cache()
    cache = db.get("contracts", {})

    updated = 0
    not_found = []
    for pid in targets:
        if pid in parsed_by_id:
            cache[pid] = parsed_by_id[pid]
            updated += 1
        else:
            not_found.append(pid)

    # Fallback fiches pour les cibles absentes du flux (ex. Malinski).
    profile_added = 0
    if not_found:
        slugs = {str(p.get("playerId")): (p.get("puckpedia_slug"), p.get("name"))
                 for p in _load_stats_players()}
        now = datetime.now(timezone.utc).isoformat()
        still_missing = []
        for pid in not_found:
            slug, name = slugs.get(pid, (None, None))
            rec = None
            if slug:
                try:
                    rec = fetch_contract_from_profile(slug, pid, name=name)
                except Exception:
                    rec = None
                time.sleep(PROFILE_DELAY)
            if rec:
                rec["scraped_at"] = now
                cache[pid] = rec
                profile_added += 1
            else:
                still_missing.append(pid)
        not_found = still_missing

    save_cache(cache)
    return {
        "scraped": updated + profile_added,
        "profile_added": profile_added,
        "errors": errors,
        "not_found": not_found,
        "total_cached": len(cache),
    }


if __name__ == "__main__":
    print("Récupération complète des contrats PuckPedia (requests)...")

    def cb(done, total, msg):
        print(f"  {msg}")

    s = update_contracts(progress_cb=cb)
    print(f"\nTerminé : {s['scraped']} contrats "
          f"({s['from_feed']} via le flux + {s['profile_added']} via fiches), "
          f"{len(s['errors'])} erreurs, {s['no_id']} sans nhl_id, "
          f"{len(s['profile_failed'])} fiches en échec")
    for e in s["errors"]:
        print("  ERREUR:", e)
