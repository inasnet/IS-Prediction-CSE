"""Repérage traçable des variables financières dans les rapports PDF.

La sortie est volontairement une table de *candidats*. Une valeur n'est validée
que dans l'étape 04, après contrôle du tableau, de l'année et du périmètre.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import unicodedata
from pathlib import Path

import fitz
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "reports" / "downloaded"
OUTPUT_DIR = BASE_DIR / "data" / "intermediate"
MANIFEST_FILE = OUTPUT_DIR / "ammc_reports_manifest_clean.csv"

VARIABLE_PATTERNS: dict[str, tuple[str, ...]] = {
    "chiffre_affaires": (r"chiffre\s+d['’ ]?affaires", r"ventes nettes"),
    "ebit": (
        r"resultat\s+d['’ ]?exploitation(?:\s+courant)?",
        r"resultat\s+operationnel(?:\s+courant)?",
    ),
    "resultat_financier": (r"resultat\s+financier",),
    "resultat_avant_impot": (
        r"resultat\s+avant\s+impot",
        r"resultat\s+courant\s+avant\s+impot",
    ),
    "resultat_net": (
        r"resultat\s+net(?:\s+de\s+l['’ ]?exercice)?",
        r"benefice\s+net",
    ),
    "total_actif": (r"total\s+(?:de\s+l['’ ]?)?actif",),
    "capitaux_propres": (
        r"total\s+des\s+capitaux\s+propres",
        r"capitaux\s+propres(?:\s+de\s+l['’ ]?ensemble)?",
    ),
    "total_dettes": (r"total\s+(?:des\s+)?dettes",),
    "dettes_financieres": (
        r"dettes?\s+financieres?",
        r"dettes?\s+de\s+financement",
        r"emprunts\s+et\s+dettes",
    ),
    "tresorerie_equivalents": (
        r"tresorerie\s+et\s+equivalents?\s+de\s+tresorerie",
        r"tresorerie\s+actif",
        r"disponibilites",
    ),
    "ebitda": (
        r"\bebitda\b",
        r"excedent\s+brut\s+d['’ ]?exploitation",
    ),
    "immobilisations_corporelles": (r"immobilisations?\s+corporelles?",),
    "impot_differe": (r"impots?\s+differes?",),
}

NUMBER_RE = re.compile(
    r"(?<![\w])(?:\(\s*)?-?\d{1,3}(?:[ .\u00a0]\d{3})*(?:[,.]\d+)?(?:\s*\))?"
)
YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
ILLEGAL_EXCEL_RE = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.lower().replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def parse_number(raw: str) -> float | None:
    value = raw.strip()
    negative_parentheses = value.startswith("(") and value.endswith(")")
    value = value.strip("() ").replace("\u00a0", "").replace(" ", "")
    if not value or YEAR_RE.fullmatch(value):
        return None
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        value = value.replace(",", ".")
    try:
        number = float(value)
        return -number if negative_parentheses else number
    except ValueError:
        return None


def detect_unit(page_text: str, context: str) -> tuple[str, float]:
    search = normalize(context + " " + page_text[:2500])
    if re.search(r"en\s+(?:milliards?|mds?)\s+(?:de\s+)?dirhams|gdh", search):
        return "milliards MAD", 1_000_000_000.0
    if re.search(r"en\s+millions?\s+(?:de\s+)?dirhams|\bmmad\b|\bmdh\b", search):
        return "millions MAD", 1_000_000.0
    if re.search(r"en\s+milliers?\s+(?:de\s+)?dirhams|\bkmad\b|\bkdh\b", search):
        return "milliers MAD", 1_000.0
    if re.search(r"\bmad\b|\bdirhams?\b|\bdhs?\b", search):
        return "MAD", 1.0
    return "inconnue", 1.0


def detect_accounts(page_text: str, line_index: int, lines: list[str]) -> str:
    local = normalize(" ".join(lines[max(0, line_index - 18): line_index + 5]))
    page = normalize(page_text)
    if "consolide" in local:
        return "consolides"
    if re.search(r"\bsocial(?:e|es)?\b|comptes sociaux", local):
        return "sociaux"
    consolidated = page.count("consolide")
    social = len(re.findall(r"\bsocial(?:e|es)?\b|comptes sociaux", page))
    if consolidated >= 2 and consolidated > social:
        return "consolides"
    if social >= 2 and social > consolidated:
        return "sociaux"
    return "a_controler"


def filename_year(path: Path) -> int | None:
    years = [int(x) for x in YEAR_RE.findall(path.stem)]
    years = [x for x in years if 2000 <= x <= 2025]
    return years[0] if years else None


def detect_document_year(document: fitz.Document, path: Path) -> tuple[int | None, str]:
    indexes = list(range(min(15, len(document))))
    indexes += list(range(max(0, len(document) - 4), len(document)))
    text = normalize(" ".join(document[index].get_text("text") for index in sorted(set(indexes))))
    scores: dict[int, int] = {}
    strong_patterns = (
        r"rapport\s+(?:financier\s+)?annuel\s+(20\d{2})",
        r"exercice\s+(?:clos\s+)?(?:au\s+)?(?:31\s+decembre\s+)?(20\d{2})",
        r"etats?\s+financiers?\s+(?:consolides?\s+)?(?:au\s+)?31\s+decembre\s+(20\d{2})",
        r"rfa\s+(20\d{2})",
    )
    for pattern in strong_patterns:
        for match in re.findall(pattern, text):
            year = int(match)
            if 2000 <= year <= 2025:
                scores[year] = scores.get(year, 0) + 12
    for match in YEAR_RE.findall(text):
        year = int(match)
        if 2000 <= year <= 2025:
            scores[year] = scores.get(year, 0) + 1
    metadata = normalize(document.metadata.get("title") or "")
    for match in YEAR_RE.findall(metadata):
        year = int(match)
        if 2000 <= year <= 2025:
            scores[year] = scores.get(year, 0) + 20
    name_year = filename_year(path)
    if name_year:
        scores[name_year] = scores.get(name_year, 0) + 2
    if not scores:
        return name_year, "nom_fichier" if name_year else "inconnue"
    best_score = max(scores.values())
    best_year = max(year for year, score in scores.items() if score == best_score)
    source = "contenu_pdf" if best_year != name_year or best_score > 2 else "nom_fichier"
    return best_year, source


def extract_pdf(path: Path, company: str) -> list[dict]:
    rows: list[dict] = []
    document = fitz.open(path)
    document_year, year_source = detect_document_year(document, path)
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    for page_number, page in enumerate(document, start=1):
        page_text = page.get_text("text")
        if not page_text.strip():
            continue
        lines = [x.strip() for x in page_text.splitlines() if x.strip()]
        normalized_lines = [normalize(x) for x in lines]
        for line_index, normalized_line in enumerate(normalized_lines):
            matched_variables = [
                variable
                for variable, patterns in VARIABLE_PATTERNS.items()
                if any(re.search(pattern, normalized_line) for pattern in patterns)
            ]
            if not matched_variables:
                continue
            context_lines = lines[max(0, line_index - 2): line_index + 9]
            context = " | ".join(context_lines)
            raw_numbers = NUMBER_RE.findall(context)
            numbers = [
                (raw, parsed)
                for raw in raw_numbers
                if (parsed := parse_number(raw)) is not None
            ]
            unit, multiplier = detect_unit(page_text, context)
            years = [int(x) for x in YEAR_RE.findall(context)]
            accounts = detect_accounts(page_text, line_index, lines)
            for variable in matched_variables:
                for position, (raw, value) in enumerate(numbers, start=1):
                    rows.append(
                        {
                            "societe": company,
                            "annee_rapport": document_year,
                            "source_annee": year_source,
                            "type_comptes": accounts,
                            "variable": variable,
                            "valeur_brute": raw,
                            "valeur_numerique": value,
                            "unite": unit,
                            "multiplicateur_mad": multiplier,
                            "valeur_mad": value * multiplier,
                            "annees_contexte": ",".join(map(str, sorted(set(years)))),
                            "position_nombre": position,
                            "page_pdf": page_number,
                            "libelle_trouve": lines[line_index],
                            "contexte": context[:1500],
                            "source_pdf": str(path.relative_to(BASE_DIR)),
                            "sha256": file_hash,
                            "statut_validation": "a_controler",
                        }
                    )
    document.close()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", default="cosumar")
    args = parser.parse_args()
    company_slug = normalize(args.company).replace(" ", "_")
    company_dir = REPORTS_DIR / company_slug
    if MANIFEST_FILE.exists():
        manifest = pd.read_csv(MANIFEST_FILE)
        selected = manifest[
            manifest["requested_company"].map(
                lambda value: normalize(value).replace(" ", "_") == company_slug
            )
            & manifest["status"].eq("downloaded")
            & manifest["local_path"].notna()
        ]
        pdfs = sorted(
            BASE_DIR / str(local_path)
            for local_path in selected["local_path"].drop_duplicates()
            if (BASE_DIR / str(local_path)).exists()
        )
    else:
        pdfs = sorted(company_dir.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"Aucun PDF trouvé dans {company_dir}")

    all_rows: list[dict] = []
    seen_hashes: set[str] = set()
    duplicates = 0
    for index, pdf in enumerate(pdfs, start=1):
        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        if digest in seen_hashes:
            duplicates += 1
            continue
        seen_hashes.add(digest)
        print(f"[{index}/{len(pdfs)}] {pdf.name}")
        all_rows.extend(extract_pdf(pdf, args.company))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / f"{company_slug}_financial_candidates.xlsx"
    dataframe = pd.DataFrame(all_rows)
    if not dataframe.empty:
        for column in dataframe.select_dtypes(include="object").columns:
            dataframe[column] = dataframe[column].map(
                lambda value: ILLEGAL_EXCEL_RE.sub("", value)
                if isinstance(value, str)
                else value
            )
        dataframe.sort_values(
            ["annee_rapport", "source_pdf", "page_pdf", "variable"],
            inplace=True,
            na_position="first",
        )
    dataframe.to_excel(output, index=False)
    print(f"Candidats: {len(dataframe):,}")
    print(f"PDF uniques: {len(seen_hashes)}; doublons ignorés: {duplicates}")
    print(f"Sortie: {output}")


if __name__ == "__main__":
    main()
