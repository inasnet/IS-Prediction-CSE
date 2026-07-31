"""Classement et consolidation prudente des candidats financiers."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
INTERMEDIATE_DIR = BASE_DIR / "data" / "intermediate"
OUTPUT_DIR = BASE_DIR / "data" / "output"


def score_candidate(row: pd.Series) -> int:
    score = 0
    label = str(row.get("libelle_trouve", "")).lower()
    context = str(row.get("contexte", "")).lower()
    account_type = str(row.get("type_comptes", ""))
    unit = str(row.get("unite", ""))
    year = row.get("annee_rapport")
    context_years = str(row.get("annees_contexte", ""))
    value = row.get("valeur_numerique")
    position = row.get("position_nombre")

    if account_type in {"sociaux", "consolides"}:
        score += 20
    if unit != "inconnue":
        score += 15
    if pd.notna(year) and str(int(year)) in context_years:
        score += 20
    if re.search(r"total|resultat|chiffre|capitaux|tresorerie|immobilisations", label):
        score += 8
    if "tableau" in context or "etat " in context or "bilan" in context:
        score += 5
    if pd.notna(value) and abs(float(value)) >= 1:
        score += 4
    value_mad = row.get("valeur_mad")
    variable = str(row.get("variable", ""))
    if pd.notna(value_mad):
        absolute_mad = abs(float(value_mad))
        if variable in {
            "chiffre_affaires",
            "total_actif",
            "capitaux_propres",
            "immobilisations_corporelles",
            "total_dettes",
        }:
            score += 18 if absolute_mad >= 1_000_000 else -35
        elif absolute_mad >= 100_000:
            score += 8
    if pd.notna(position) and int(position) <= 4:
        score += 4
    if "%" in str(row.get("valeur_brute", "")):
        score -= 30
    if pd.notna(value) and 1900 <= abs(float(value)) <= 2100:
        score -= 25
    if pd.notna(value) and float(value).is_integer() and 0 <= abs(float(value)) <= 31:
        score -= 18
    if pd.notna(value) and abs(float(value)) > 1e13:
        score -= 15
    if account_type == "a_controler":
        score -= 5
    return score


def add_validation_flags(best: pd.DataFrame) -> pd.DataFrame:
    best = best.copy()
    best["alertes_validation"] = ""
    best["nombre_alertes"] = 0

    def flag(index: int, message: str) -> None:
        existing = best.at[index, "alertes_validation"]
        best.at[index, "alertes_validation"] = (
            f"{existing}; {message}".strip("; ") if existing else message
        )
        best.at[index, "nombre_alertes"] += 1

    structural = {
        "chiffre_affaires",
        "total_actif",
        "capitaux_propres",
        "immobilisations_corporelles",
        "total_dettes",
    }
    for index, row in best.iterrows():
        value = row.get("valeur_mad")
        raw = row.get("valeur_numerique")
        if row.get("type_comptes") == "a_controler":
            flag(index, "type de comptes ambigu")
        if row.get("unite") == "inconnue":
            flag(index, "unité inconnue")
        if pd.isna(value):
            flag(index, "montant absent")
            continue
        if row.get("variable") in structural and abs(float(value)) < 1_000_000:
            flag(index, "montant structurel invraisemblablement faible")
        if pd.notna(raw) and float(raw).is_integer() and 0 <= abs(float(raw)) <= 31:
            flag(index, "nombre probablement issu d'une date ou d'un rang")
        if pd.notna(raw) and 1900 <= abs(float(raw)) <= 2100:
            flag(index, "nombre correspondant probablement à une année")
        if row.get("variable") in {
            "chiffre_affaires",
            "total_actif",
            "capitaux_propres",
            "immobilisations_corporelles",
        } and float(value) < 0:
            flag(index, "signe négatif inhabituel")

    group_keys = ["societe", "type_comptes", "variable"]
    ordered = best.sort_values(group_keys + ["annee_rapport"])
    for _, group in ordered.groupby(group_keys, dropna=False):
        previous: tuple[int, float] | None = None
        for index, row in group.iterrows():
            value = abs(float(row["valeur_mad"])) if pd.notna(row["valeur_mad"]) else 0
            year = int(row["annee_rapport"])
            if previous and year - previous[0] == 1 and value and previous[1]:
                ratio = max(value / previous[1], previous[1] / value)
                if ratio > 10:
                    flag(index, f"variation annuelle extrême x{ratio:.1f}")
            previous = (year, value)

    best["statut_validation"] = best.apply(
        lambda row: (
            "preselection_forte_a_confirmer"
            if row["nombre_alertes"] == 0 and row["score_fiabilite"] >= 65
            else "controle_manuel_requis"
        ),
        axis=1,
    )
    return best


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", default="cosumar")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help="Dossier de sortie des classements.",
    )
    args = parser.parse_args()
    slug = args.company.lower().replace(" ", "_")
    source = INTERMEDIATE_DIR / f"{slug}_financial_candidates.xlsx"
    if not source.exists():
        raise SystemExit(f"Fichier absent : {source}")

    data = pd.read_excel(source)
    data = data[data["annee_rapport"].between(2000, 2025, inclusive="both")].copy()
    data["score_fiabilite"] = data.apply(score_candidate, axis=1)
    keys = ["societe", "annee_rapport", "type_comptes", "variable"]
    data.sort_values(keys + ["score_fiabilite"], ascending=[True] * 4 + [False], inplace=True)
    data["rang_candidat"] = data.groupby(keys, dropna=False).cumcount() + 1

    shortlist = data[data["rang_candidat"] <= args.top].copy()
    best = shortlist[shortlist["rang_candidat"] == 1].copy()
    best = add_validation_flags(best)
    coverage = (
        best.groupby(["annee_rapport", "type_comptes"])["variable"]
        .nunique()
        .rename("nombre_variables_reperes")
        .reset_index()
    )
    wide = best.pivot_table(
        index=["societe", "annee_rapport", "type_comptes"],
        columns="variable",
        values="valeur_mad",
        aggfunc="first",
    ).reset_index()
    strong = best[best["statut_validation"] == "preselection_forte_a_confirmer"].copy()
    manual = best[best["statut_validation"] == "controle_manuel_requis"].copy()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{slug}_variables_controle.xlsx"
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        best.to_excel(writer, sheet_name="Meilleurs candidats", index=False)
        strong.to_excel(writer, sheet_name="Préselection forte", index=False)
        manual.to_excel(writer, sheet_name="Contrôle manuel", index=False)
        wide.to_excel(writer, sheet_name="Base large provisoire", index=False)
        shortlist.to_excel(writer, sheet_name="Top candidats", index=False)
        coverage.to_excel(writer, sheet_name="Couverture", index=False)
    print(f"Meilleurs candidats: {len(best):,}")
    print(f"Top candidats conservés: {len(shortlist):,}")
    print(f"Sortie: {output}")


if __name__ == "__main__":
    main()
