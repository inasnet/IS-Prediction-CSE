"""Audit de couverture du catalogue AMMC pour les sociétés de l'étude."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
COMPANIES_FILE = BASE_DIR / "data" / "input" / "societes.xlsx"
CATALOG_FILE = BASE_DIR / "data" / "intermediate" / "ammc_catalog.xlsx"
OUTPUT_FILE = BASE_DIR / "data" / "output" / "audit_couverture_ammc.xlsx"

ALIASES = {
    "attijari wafa bank": ["attijariwafa bank"],
    "bank of africa": ["bank of africa groupe bmce", "bmce", "boa"],
    "bmce bank": ["bank of africa groupe bmce", "bmce", "boa"],
    "banque centrale populaire": ["banque centrale populaire", "bcp"],
    "bcp": ["banque centrale populaire", "bcp"],
    "brasseries du maroc": ["societe des boissons du maroc", "sbm"],
    "societe des boissons du maroc": ["societe des boissons du maroc", "sbm"],
    "centrale laitiere": ["centrale danone", "centrale laitiere"],
    "cnia saada": ["sanlam maroc", "saham assurance", "cnia saada"],
    "disty rfa": ["disty technologies"],
    "ennakl en dinar tunisien": ["ennakl automobiles"],
    "lafargeholcim maroc": ["lafargeholcim maroc", "holcim maroc"],
    "lafarge ciments": ["lafargeholcim maroc", "holcim maroc", "lafarge ciments"],
    "les grandes marques et conserveries cherifiennes": ["lgmc"],
    "miniere touissit": ["compagnie miniere de touissit", "cmt"],
    "maroctelecom": ["maroc telecom"],
    "res dar saada": ["residences dar saada", "rds"],
    "sanlam maroc": ["sanlam maroc", "saham assurance"],
    "taqa morocco": ["taqa morocco", "jlec"],
}


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def matches(company: str, issuer: str) -> bool:
    company_n = normalize(company)
    issuer_n = normalize(issuer)
    if not company_n or not issuer_n:
        return False
    if company_n == issuer_n:
        return True
    company_compact = company_n.replace(" ", "")
    issuer_compact = issuer_n.replace(" ", "")
    if company_compact == issuer_compact:
        return True
    if issuer_n.startswith(company_n + " ") or company_n.startswith(issuer_n + " "):
        return True
    if min(len(company_compact), len(issuer_compact)) >= 6 and (
        company_n in issuer_n or issuer_n in company_n
    ):
        return True
    for alias in ALIASES.get(company_n, []):
        alias_n = normalize(alias)
        alias_compact = alias_n.replace(" ", "")
        if alias_n == issuer_n or alias_compact == issuer_compact:
            return True
        if min(len(alias_compact), len(issuer_compact)) >= 4 and (
            alias_n in issuer_n or issuer_n in alias_n
        ):
            return True
    return False


def main() -> None:
    companies = pd.read_excel(COMPANIES_FILE, sheet_name="Societes")
    catalog = pd.read_excel(CATALOG_FILE, sheet_name="Catalogue AMMC")
    catalog = catalog[
        catalog["report_type"].map(normalize).str.contains("annuel", na=False)
    ].copy()
    catalog["year"] = pd.to_numeric(catalog["year"], errors="coerce")

    detail_rows: list[dict] = []
    summary_rows: list[dict] = []
    for _, company_row in companies.iterrows():
        company = str(company_row["Société"]).strip()
        start = max(2000, int(company_row.get("Année début", 2000)))
        end = min(2025, int(company_row.get("Année fin", 2025)))
        target_years = set(range(start, end + 1))
        selected = catalog[catalog["issuer"].map(lambda issuer: matches(company, issuer))]
        available = {
            int(year)
            for year in selected["year"].dropna()
            if start <= int(year) <= end
        }
        missing = sorted(target_years - available)
        summary_rows.append(
            {
                "societe": company,
                "secteur": company_row.get("Secteur", ""),
                "annee_debut": start,
                "annee_fin": end,
                "annees_attendues": len(target_years),
                "annees_disponibles_ammc": len(available),
                "taux_couverture": len(available) / len(target_years) if target_years else 0,
                "annees_disponibles": ", ".join(map(str, sorted(available))),
                "annees_manquantes": ", ".join(map(str, missing)),
                "nombre_fiches_ammc": len(selected),
            }
        )
        for _, report in selected.iterrows():
            detail_rows.append(
                {
                    "societe_cible": company,
                    "emetteur_ammc": report["issuer"],
                    "annee": report["year"],
                    "type_rapport": report["report_type"],
                    "url_fiche": report["detail_url"],
                }
            )

    summary = pd.DataFrame(summary_rows).sort_values(
        ["taux_couverture", "societe"], ascending=[False, True]
    )
    details = pd.DataFrame(detail_rows)
    missing_rows = []
    for row in summary_rows:
        for year in filter(None, str(row["annees_manquantes"]).split(", ")):
            missing_rows.append(
                {
                    "societe": row["societe"],
                    "secteur": row["secteur"],
                    "annee_manquante": int(year),
                    "source_a_rechercher": "rapport société / archive CDVM / autre",
                }
            )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Résumé", index=False)
        details.to_excel(writer, sheet_name="Fiches rapprochées", index=False)
        pd.DataFrame(missing_rows).to_excel(writer, sheet_name="Années manquantes", index=False)
    print(f"Sociétés auditées : {len(summary)}")
    print(f"Fiches annuelles rapprochées : {len(details)}")
    print(f"Sortie : {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
