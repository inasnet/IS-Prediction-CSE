"""Identifier et valider le site officiel de chaque entreprise étudiée.

Le script combine une recherche web, un score de similarité et une validation
du contenu de la page d'accueil. Un cache local évite de répéter les recherches
déjà effectuées. La sortie conserve le meilleur candidat ainsi que les éléments
qui permettent d'auditer ce choix.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse, urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS


# =============================================================================
# CONFIGURATION DES ENTRÉES, SORTIES ET PARAMÈTRES DE RECHERCHE
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "data" / "input" / "societes.xlsx"
OUTPUT_FILE = BASE_DIR / "data" / "intermediate" / "societes_sites.xlsx"
CACHE_FILE = BASE_DIR / "data" / "intermediate" / "site_search_cache.json"
LOG_DIR = BASE_DIR / "logs"

REQUEST_TIMEOUT = 15
SEARCH_DELAY_SECONDS = 1.2
MIN_ACCEPTED_SCORE = 80
MAX_RESULTS_PER_QUERY = 8
MAX_CANDIDATES_TO_VALIDATE = 12

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# Sites qui ne peuvent pas être considérés comme sites officiels d'entreprise.
EXCLUDED_DOMAINS = {
    "ammc.ma",
    "casablanca-bourse.com",
    "casabourse.ma",
    "boursenews.ma",
    "fnh.ma",
    "medias24.com",
    "leconomiste.com",
    "challenge.ma",
    "lavieeco.com",
    "lematin.ma",
    "telquel.ma",
    "fr-academic.com",
    "welipro.com",
    "maroc.welipro.com",
    "kerix.net",
    "charika.ma",
    "societe.com",
    "verif.com",
    "kompass.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "x.com",
    "twitter.com",
    "wikipedia.org",
    "bloomberg.com",
    "reuters.com",
    "marketscreener.com",
    "zonebourse.com",
    "indeed.com",
    "glassdoor.com",
    "creditdumaroc.ma",
    "ccisfm.ma",
}

GENERIC_WORDS = {
    "maroc", "morocco", "groupe", "group", "holding", "societe", "société",
    "compagnie", "company", "sa", "sca", "sarl", "bank", "banque",
    "les", "du", "de", "des", "la", "le", "et", "of", "the"
}

OFFICIAL_HINTS = {
    "site officiel", "official website", "accueil", "à propos", "about us",
    "notre groupe", "nos activités", "contact", "mentions légales",
    "relations investisseurs", "investor relations", "informations financières",
    "publications financières", "rapport annuel", "annual report"
}

BAD_PAGE_HINTS = {
    "annuaire", "actualité", "actualités", "news", "article", "fiche entreprise",
    "profil entreprise", "cours de bourse", "cotations", "emploi", "recrutement",
    "encyclopédie", "wiki"
}


# =============================================================================
# STRUCTURE D'UN SITE CANDIDAT
# =============================================================================

@dataclass
class Candidate:
    """Informations et scores associés à un site potentiellement officiel."""
    company: str
    url: str
    domain: str
    title: str = ""
    snippet: str = ""
    search_score: int = 0
    validation_score: int = 0
    final_score: int = 0
    status_code: int | None = None
    final_url: str = ""
    company_found_on_page: bool = False
    investor_page_found: bool = False
    excluded: bool = False
    reason: str = ""


# =============================================================================
# NORMALISATION ET CALCUL DES SCORES
# =============================================================================

def normalize_text(value: str) -> str:
    """Normaliser un texte pour rendre les comparaisons robustes aux accents."""
    value = str(value or "")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def company_tokens(company: str) -> list[str]:
    """Extraire les mots distinctifs du nom d'une entreprise."""
    tokens = [
        token for token in normalize_text(company).split()
        if len(token) >= 3 and token not in GENERIC_WORDS
    ]
    return tokens


def canonical_domain(url: str) -> str:
    """Extraire un nom de domaine canonique, sans préfixe technique."""
    try:
        domain = urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return ""
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def root_url(url: str) -> str:
    """Ramener une URL vers la racine de son domaine."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f"{parsed.scheme}://{parsed.netloc}"


def is_excluded_domain(domain: str) -> bool:
    """Écarter les médias, annuaires et plateformes qui ne sont pas officiels."""
    domain = domain.lower()
    return any(domain == bad or domain.endswith("." + bad) for bad in EXCLUDED_DOMAINS)


def domain_similarity(company: str, domain: str) -> int:
    """Mesurer la proximité entre la dénomination et le domaine candidat."""
    compact_domain = re.sub(r"[^a-z0-9]", "", normalize_text(domain))
    tokens = company_tokens(company)
    if not tokens:
        return 0

    score = 0
    joined = "".join(tokens)

    if joined and joined in compact_domain:
        score += 55

    matched = [token for token in tokens if token in compact_domain]
    if matched:
        score += min(45, len(matched) * 18)

    longest = max(tokens, key=len)
    if longest in compact_domain:
        score += 20

    return min(score, 100)


def text_similarity(company: str, text: str) -> int:
    """Mesurer la présence des termes distinctifs de l'entreprise dans un texte."""
    text_n = normalize_text(text)
    tokens = company_tokens(company)
    if not tokens:
        return 0

    matched = sum(1 for token in tokens if token in text_n)
    coverage = matched / len(tokens)

    score = int(coverage * 60)
    if normalize_text(company) in text_n:
        score += 35

    return min(score, 100)


def search_queries(company: str) -> list[str]:
    """Construire plusieurs requêtes complémentaires pour une même société."""
    return [
        f'"{company}" site officiel Maroc',
        f'"{company}" official website Morocco',
        f'"{company}" relations investisseurs',
        f'"{company}" publications financières',
        f'"{company}" rapport annuel',
    ]


def safe_search(query: str) -> list[dict]:
    """Exécuter une recherche web en contrôlant les erreurs et le délai."""
    try:
        return DDGS(timeout=12).text(
            query,
            region="fr-fr",
            safesearch="moderate",
            max_results=MAX_RESULTS_PER_QUERY,
            backend="auto",
        )
    except Exception as exc:
        print(f"      Erreur de recherche : {exc}")
        return []


def build_candidates(company: str) -> list[Candidate]:
    """Fusionner les résultats de recherche en candidats uniques et scorés."""
    by_domain: dict[str, Candidate] = {}

    for query in search_queries(company):
        print(f'    Recherche : {query}')
        for result in safe_search(query):
            url = result.get("href") or result.get("url") or ""
            title = result.get("title") or ""
            snippet = result.get("body") or result.get("snippet") or ""
            domain = canonical_domain(url)

            if not url or not domain:
                continue

            if is_excluded_domain(domain):
                continue

            domain_score = domain_similarity(company, domain)
            title_score = text_similarity(company, title)
            snippet_score = text_similarity(company, snippet)

            search_score = (
                domain_score
                + int(title_score * 0.45)
                + int(snippet_score * 0.20)
            )

            combined = normalize_text(f"{title} {snippet}")
            if any(hint in combined for hint in BAD_PAGE_HINTS):
                search_score -= 25
            if any(hint in combined for hint in OFFICIAL_HINTS):
                search_score += 10

            candidate = Candidate(
                company=company,
                url=root_url(url),
                domain=domain,
                title=title,
                snippet=snippet,
                search_score=search_score,
            )

            existing = by_domain.get(domain)
            if existing is None or candidate.search_score > existing.search_score:
                by_domain[domain] = candidate

        time.sleep(SEARCH_DELAY_SECONDS)

    return sorted(
        by_domain.values(),
        key=lambda c: c.search_score,
        reverse=True
    )[:MAX_CANDIDATES_TO_VALIDATE]


def fetch_page(session: requests.Session, url: str) -> tuple[int | None, str, str]:
    """Télécharger une page et renvoyer son statut, son URL finale et son texte."""
    try:
        response = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type:
            return response.status_code, response.url, ""
        return response.status_code, response.url, response.text[:2_000_000]
    except requests.RequestException:
        return None, "", ""


def inspect_homepage(company: str, candidate: Candidate, session: requests.Session) -> Candidate:
    """Valider un candidat à partir du contenu réel de sa page d'accueil."""
    status_code, final_url, html = fetch_page(session, candidate.url)
    candidate.status_code = status_code
    candidate.final_url = final_url or candidate.url

    final_domain = canonical_domain(candidate.final_url)
    if final_domain and is_excluded_domain(final_domain):
        candidate.excluded = True
        candidate.reason = "Redirection vers un domaine exclu"
        candidate.final_score = -100
        return candidate

    if status_code is None:
        candidate.reason = "Site inaccessible"
        candidate.final_score = candidate.search_score - 60
        return candidate

    if status_code >= 400:
        candidate.reason = f"Erreur HTTP {status_code}"
        candidate.final_score = candidate.search_score - 45
        return candidate

    if not html:
        candidate.reason = "Page non HTML ou vide"
        candidate.final_score = candidate.search_score - 35
        return candidate

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body_text = soup.get_text(" ", strip=True)
    body_text_n = normalize_text(body_text[:250_000])
    title_n = normalize_text(title)

    tokens = company_tokens(company)
    matched_tokens = [token for token in tokens if token in body_text_n or token in title_n]
    coverage = len(matched_tokens) / max(1, len(tokens))
    exact_name = normalize_text(company) in f"{title_n} {body_text_n}"

    candidate.company_found_on_page = exact_name or coverage >= 0.60

    validation_score = 0

    if exact_name:
        validation_score += 55
    elif coverage >= 0.80:
        validation_score += 42
    elif coverage >= 0.60:
        validation_score += 25
    else:
        validation_score -= 45

    page_domain_score = domain_similarity(company, final_domain or candidate.domain)
    validation_score += int(page_domain_score * 0.45)

    combined_page = f"{title_n} {body_text_n[:100000]}"

    if any(hint in combined_page for hint in OFFICIAL_HINTS):
        validation_score += 15

    if any(hint in combined_page for hint in BAD_PAGE_HINTS):
        validation_score -= 20

    links = []
    for a in soup.find_all("a", href=True):
        label = normalize_text(a.get_text(" ", strip=True))
        href = a.get("href", "")
        links.append((label, href))

    investor_keywords = (
        "investisseur", "investisseurs", "investor", "finance",
        "financiere", "financier", "publication", "rapport annuel",
        "annual report"
    )
    if any(any(keyword in label for keyword in investor_keywords) for label, _ in links):
        candidate.investor_page_found = True
        validation_score += 12

    # Un site officiel a généralement un lien de contact ou des mentions légales.
    if any("contact" in label for label, _ in links):
        validation_score += 5
    if "mentions legales" in combined_page or "legal notice" in combined_page:
        validation_score += 5

    candidate.validation_score = validation_score
    candidate.final_score = candidate.search_score + validation_score

    if not candidate.company_found_on_page:
        candidate.reason = "Nom de l'entreprise insuffisamment présent sur le site"
    elif candidate.final_score < MIN_ACCEPTED_SCORE:
        candidate.reason = "Score insuffisant"
    else:
        candidate.reason = "Candidat validé"

    return candidate


def confidence_label(score: int) -> str:
    """Traduire le score numérique en niveau de confiance interprétable."""
    if score >= 135:
        return "Élevée"
    if score >= MIN_ACCEPTED_SCORE:
        return "Moyenne"
    return "Faible"


def load_cache() -> dict:
    """Relire les recherches déjà effectuées, si le cache existe."""
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache: dict) -> None:
    """Persister le cache de manière lisible et compatible avec les accents."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_companies() -> pd.DataFrame:
    """Charger et contrôler la liste des entreprises à rechercher."""
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Fichier introuvable : {INPUT_FILE}\n"
            "Place societes.xlsx dans data/input/."
        )

    df = pd.read_excel(INPUT_FILE, sheet_name=0)
    required = {"Société", "Secteur", "Année début", "Année fin"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(
            "Colonnes manquantes dans societes.xlsx : "
            + ", ".join(sorted(missing))
        )

    df = df.dropna(subset=["Société"]).copy()
    df["Société"] = df["Société"].astype(str).str.strip()
    return df


def choose_best_candidate(candidates: Iterable[Candidate]) -> Candidate | None:
    """Sélectionner le candidat admissible ayant obtenu le meilleur score."""
    valid = [
        c for c in candidates
        if not c.excluded
        and c.company_found_on_page
        and c.final_score >= MIN_ACCEPTED_SCORE
    ]
    if not valid:
        return None
    return max(valid, key=lambda c: c.final_score)


def main() -> None:
    """Orchestrer la recherche, la validation et l'export des sites officiels."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print("ÉTAPE 01 — RECHERCHE ROBUSTE DES SITES OFFICIELS — VERSION 2")
    print("=" * 70)
    print(f"Fichier d'entrée : {INPUT_FILE}\n")

    df = load_companies()
    cache = load_cache()
    session = requests.Session()

    output_rows = []
    review_rows = []

    total = len(df)

    for index, row in df.reset_index(drop=True).iterrows():
        company = row["Société"]
        print(f"\n[{index + 1}/{total}] Recherche pour : {company}")

        cached = cache.get(company)
        if cached and cached.get("version") == 2:
            print("    Résultat récupéré depuis le cache.")
            output_rows.append(cached["output"])
            review_rows.extend(cached.get("candidates", []))
            continue

        candidates = build_candidates(company)

        if not candidates:
            print("    Aucun candidat trouvé.")
            selected_row = {
                **row.to_dict(),
                "Site officiel": "",
                "Domaine": "",
                "Score": 0,
                "Confiance": "Faible",
                "Statut": "À vérifier manuellement",
                "Page investisseurs détectée": "Non",
            }
            output_rows.append(selected_row)
            cache[company] = {
                "version": 2,
                "output": selected_row,
                "candidates": [],
            }
            save_cache(cache)
            continue

        validated = []
        for candidate in candidates:
            candidate = inspect_homepage(company, candidate, session)
            validated.append(candidate)

            print(
                f"    Candidat : {candidate.final_url or candidate.url} "
                f"| recherche={candidate.search_score} "
                f"| validation={candidate.validation_score} "
                f"| total={candidate.final_score}"
            )

            review_rows.append(asdict(candidate))

        best = choose_best_candidate(validated)

        if best:
            site = root_url(best.final_url or best.url)
            print(f"    Résultat retenu : {site}")
            print(f"    Niveau de confiance : {confidence_label(best.final_score)}")
            selected_row = {
                **row.to_dict(),
                "Site officiel": site,
                "Domaine": canonical_domain(site),
                "Score": best.final_score,
                "Confiance": confidence_label(best.final_score),
                "Statut": "Trouvé automatiquement",
                "Page investisseurs détectée": (
                    "Oui" if best.investor_page_found else "Non"
                ),
            }
        else:
            print("    Aucun site suffisamment fiable. Vérification manuelle requise.")
            selected_row = {
                **row.to_dict(),
                "Site officiel": "",
                "Domaine": "",
                "Score": max((c.final_score for c in validated), default=0),
                "Confiance": "Faible",
                "Statut": "À vérifier manuellement",
                "Page investisseurs détectée": "Non",
            }

        output_rows.append(selected_row)

        cache[company] = {
            "version": 2,
            "output": selected_row,
            "candidates": [asdict(c) for c in validated],
        }
        save_cache(cache)

    output_df = pd.DataFrame(output_rows)
    review_df = pd.DataFrame(review_rows)

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        output_df.to_excel(writer, sheet_name="Sites retenus", index=False)
        review_df.to_excel(writer, sheet_name="Candidats à contrôler", index=False)

        ws = writer.book["Sites retenus"]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        for column_cells in ws.columns:
            max_length = max(
                len(str(cell.value)) if cell.value is not None else 0
                for cell in column_cells
            )
            ws.column_dimensions[column_cells[0].column_letter].width = min(
                max(max_length + 2, 12), 45
            )

        ws2 = writer.book["Candidats à contrôler"]
        ws2.freeze_panes = "A2"
        ws2.auto_filter.ref = ws2.dimensions

    print("\n" + "=" * 70)
    print("TERMINÉ")
    print("=" * 70)
    print(f"Fichier créé : {OUTPUT_FILE}")
    print("Feuille 1 : Sites retenus")
    print("Feuille 2 : Candidats à contrôler")
    print("\nLes lignes « À vérifier manuellement » sont volontairement laissées vides")
    print("plutôt que d'enregistrer un faux site officiel.")


if __name__ == "__main__":
    main()
