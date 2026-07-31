"""Produit un manifeste de référence sans modifier le manifeste historique."""

from __future__ import annotations

import runpy
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
SOURCE = BASE_DIR / "data" / "intermediate" / "ammc_reports_manifest.csv"
OUTPUT_CSV = BASE_DIR / "data" / "intermediate" / "ammc_reports_manifest_clean.csv"
OUTPUT_XLSX = BASE_DIR / "data" / "intermediate" / "ammc_reports_manifest_clean.xlsx"


def main() -> None:
    """Dédupliquer le manifeste et isoler les associations société–émetteur invalides."""
    downloader = runpy.run_path(str(BASE_DIR / "02_download_reports.py"))
    issuer_matches = downloader["issuer_matches"]
    data = pd.read_csv(SOURCE)
    data["downloaded_at_parsed"] = pd.to_datetime(
        data["downloaded_at"], errors="coerce"
    )
    # En cas de doublon, la tentative la plus récente constitue la référence.
    data.sort_values("downloaded_at_parsed", inplace=True, na_position="first")
    data = data.drop_duplicates(
        ["requested_company", "detail_url"], keep="last"
    ).copy()
    data["association_valide"] = data.apply(
        lambda row: issuer_matches(
            str(row["requested_company"]),
            str(row["ammc_issuer"]),
        ),
        axis=1,
    )
    # Les associations rejetées sont conservées dans une feuille dédiée à l'audit.
    invalid = data[~data["association_valide"]].copy()
    clean = data[data["association_valide"]].copy()
    clean.drop(columns=["downloaded_at_parsed"], inplace=True)
    invalid.drop(columns=["downloaded_at_parsed"], inplace=True)

    clean.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        clean.to_excel(writer, sheet_name="Manifeste propre", index=False)
        invalid.to_excel(writer, sheet_name="Associations exclues", index=False)
        clean.groupby("status").size().rename("nombre").reset_index().to_excel(
            writer, sheet_name="Résumé", index=False
        )
    print(f"Lignes propres : {len(clean)}")
    print(f"Associations exclues : {len(invalid)}")
    print(clean.groupby("status").size().to_string())
    print(f"Sortie : {OUTPUT_XLSX}")


if __name__ == "__main__":
    main()
