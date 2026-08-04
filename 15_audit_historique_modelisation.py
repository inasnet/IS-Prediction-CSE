"""Auditer le panel historique et préparer les échantillons de modélisation.

Cette étape ne remplit aucune valeur manquante. Elle mesure la qualité réelle
du panel, signale les points à contrôler et construit des échantillons dont la
chronologie empêche l'utilisation accidentelle d'informations futures.
"""

from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
SOURCE = BASE_DIR / "data" / "output" / "panel_historique_final_v27.csv"
OUTPUT = BASE_DIR / "data" / "output" / "audit_historique_modelisation_v27.xlsx"
SAMPLE_STRUCTURAL = (
    BASE_DIR / "data" / "output" / "echantillon_t_plus_1_structurel_v27.csv"
)
SAMPLE_EXTENDED = (
    BASE_DIR / "data" / "output" / "echantillon_t_plus_1_etendu_v27.csv"
)

TARGET = "is_mad"
PRIORITY_VARIABLES = [
    "resultat_net",
    "capitaux_propres",
    "resultat_avant_impot",
    "chiffre_affaires",
    "immobilisations_corporelles",
]
ALL_FINANCIAL_VARIABLES = [
    "is_mad",
    "chiffre_affaires",
    "ebit",
    "resultat_financier",
    "resultat_avant_impot",
    "resultat_net",
    "total_actif",
    "capitaux_propres",
    "total_dettes",
    "dettes_financieres",
    "tresorerie_equivalents",
    "ebitda",
    "immobilisations_corporelles",
    "impot_differe",
]

# Pour expliquer IS(t+1), ces variables sont observées à la date t. RCAI(t) et
# résultat net(t) ne constituent donc pas une fuite de la cible future.
STRUCTURAL_FEATURES = [
    "is_mad",
    "chiffre_affaires",
    "capitaux_propres",
    "immobilisations_corporelles",
]
EXTENDED_FEATURES = STRUCTURAL_FEATURES + [
    "resultat_avant_impot",
    "resultat_net",
]
KEY = ["societe", "annee", "type_comptes_resolu"]


def coverage_by_variable(panel: pd.DataFrame) -> pd.DataFrame:
    """Mesurer la disponibilité sur les lignes effectivement observées."""
    rows = []
    for variable in ALL_FINANCIAL_VARIABLES:
        available = int(panel[variable].notna().sum())
        rows.append(
            {
                "variable": variable,
                "valeurs_disponibles": available,
                "valeurs_manquantes": int(len(panel) - available),
                "taux_couverture_pct": round(100 * available / len(panel), 2),
                "premiere_annee_disponible": panel.loc[
                    panel[variable].notna(), "annee"
                ].min(),
                "derniere_annee_disponible": panel.loc[
                    panel[variable].notna(), "annee"
                ].max(),
            }
        )
    return pd.DataFrame(rows)


def coverage_by_year(panel: pd.DataFrame) -> pd.DataFrame:
    """Présenter la profondeur du panel et la complétude annuelle."""
    rows = []
    for year in range(2000, 2026):
        frame = panel[panel["annee"].eq(year)]
        row = {
            "annee": year,
            "observations": len(frame),
            "societes": frame["societe"].nunique(),
        }
        for variable in [TARGET, *PRIORITY_VARIABLES]:
            row[f"{variable}_disponibles"] = int(frame[variable].notna().sum())
            row[f"{variable}_couverture_pct"] = (
                round(100 * frame[variable].notna().mean(), 2)
                if len(frame)
                else 0.0
            )
        rows.append(row)
    return pd.DataFrame(rows)


def coverage_by_company(panel: pd.DataFrame) -> pd.DataFrame:
    """Résumer les années et les variables disponibles pour chaque société."""
    rows = []
    for company, frame in panel.groupby("societe", sort=True):
        observed_years = sorted(frame["annee"].astype(int).unique())
        first, last = min(observed_years), max(observed_years)
        internal_missing = sorted(set(range(first, last + 1)) - set(observed_years))
        types = sorted(frame["type_comptes_resolu"].dropna().astype(str).unique())
        row = {
            "societe": company,
            "secteur": frame["secteur"].dropna().iloc[-1]
            if frame["secteur"].notna().any()
            else pd.NA,
            "premiere_annee": first,
            "derniere_annee": last,
            "observations": len(frame),
            "annees_distinctes": len(observed_years),
            "annees_manquantes_internes": len(internal_missing),
            "liste_annees_manquantes_internes": ", ".join(map(str, internal_missing)),
            "types_comptes_observes": " | ".join(types),
            "rupture_type_comptes": len(types) > 1,
        }
        for variable in [TARGET, *PRIORITY_VARIABLES]:
            row[f"{variable}_n"] = int(frame[variable].notna().sum())
            row[f"{variable}_pct"] = round(
                100 * frame[variable].notna().mean(), 2
            )
        rows.append(row)
    return pd.DataFrame(rows)


def build_missing_register(panel: pd.DataFrame) -> pd.DataFrame:
    """Lister chaque valeur prioritaire manquante sans l'imputer."""
    long = panel.melt(
        id_vars=["societe", "secteur", "annee", "type_comptes_resolu"],
        value_vars=[TARGET, *PRIORITY_VARIABLES],
        var_name="variable",
        value_name="valeur",
    )
    missing = long[long["valeur"].isna()].drop(columns="valeur").copy()
    missing["priorite"] = missing["variable"].map(
        {TARGET: 0, **{v: i + 1 for i, v in enumerate(PRIORITY_VARIABLES)}}
    )
    return missing.sort_values(["priorite", "annee", "societe"])


def accounting_checks(panel: pd.DataFrame) -> pd.DataFrame:
    """Calculer des contrôles comptables indicatifs, jamais des corrections."""
    checks = panel[KEY + [
        TARGET,
        "resultat_avant_impot",
        "resultat_net",
        "total_actif",
        "capitaux_propres",
        "total_dettes",
    ]].copy()
    checks["ecart_fiscal_calcule"] = (
        checks["resultat_avant_impot"] - checks["resultat_net"] - checks[TARGET]
    )
    checks["ecart_bilan_calcule"] = (
        checks["total_actif"]
        - checks["capitaux_propres"]
        - checks["total_dettes"]
    )
    checks["alerte_fiscale"] = (
        checks["ecart_fiscal_calcule"].abs()
        > np.maximum(1_000, checks[TARGET].abs() * 0.01)
    )
    checks["alerte_bilan"] = (
        checks["ecart_bilan_calcule"].abs()
        > np.maximum(1_000, checks["total_actif"].abs() * 0.001)
    )
    checks["note_interpretation"] = np.where(
        checks["type_comptes_resolu"].eq("consolides"),
        "Un écart fiscal peut provenir de l'impôt différé ou du résultat des sociétés mises en équivalence.",
        "Contrôler l'unité, le périmètre et les contributions fiscales annexes.",
    )
    return checks


def detect_scale_breaks(panel: pd.DataFrame) -> pd.DataFrame:
    """Signaler les ruptures annuelles de facteur 100, sans conclure à une erreur."""
    rows = []
    for variable in [TARGET, *PRIORITY_VARIABLES]:
        # Une variation entre comptes sociaux et consolidés n'est pas une
        # rupture d'échelle : les comparaisons restent dans le même périmètre.
        ordered = panel.sort_values(
            ["societe", "type_comptes_resolu", "annee"]
        ).copy()
        groups = ordered.groupby(["societe", "type_comptes_resolu"])
        ordered["valeur_precedente"] = groups[variable].shift()
        ordered["annee_precedente"] = groups["annee"].shift()
        valid = (
            ordered[variable].notna()
            & ordered["valeur_precedente"].notna()
            & ordered[variable].ne(0)
            & ordered["valeur_precedente"].ne(0)
            & ordered["annee"].sub(ordered["annee_precedente"]).eq(1)
        )
        candidates = ordered.loc[valid, KEY + [
            variable, "valeur_precedente", "annee_precedente"
        ]].copy()
        candidates["ratio_absolu"] = (
            candidates[variable].abs() / candidates["valeur_precedente"].abs()
        )
        candidates = candidates[
            candidates["ratio_absolu"].ge(100)
            | candidates["ratio_absolu"].le(0.01)
        ]
        candidates["variable"] = variable
        candidates.rename(columns={variable: "valeur_actuelle"}, inplace=True)
        rows.append(candidates)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def build_t_plus_one_sample(
    panel: pd.DataFrame, features: list[str], label: str
) -> pd.DataFrame:
    """Créer des paires consécutives t -> t+1 au même périmètre comptable."""
    current = panel.copy()
    future = panel[["societe", "annee", "type_comptes_resolu", TARGET]].copy()
    future["annee"] = future["annee"] - 1
    future.rename(columns={TARGET: "is_cible_t_plus_1"}, inplace=True)
    sample = current.merge(future, on=KEY, how="inner")
    sample = sample.dropna(subset=[*features, "is_cible_t_plus_1"]).copy()
    sample["annee_cible"] = sample["annee"] + 1
    sample["variation_is_cible"] = sample["is_cible_t_plus_1"] - sample[TARGET]
    sample["scenario"] = label
    sample["decoupage_temporel"] = np.select(
        [
            sample["annee_cible"].le(2021),
            sample["annee_cible"].between(2022, 2023),
            sample["annee_cible"].between(2024, 2025),
        ],
        ["apprentissage", "validation", "test"],
        default="hors_fenetre",
    )
    columns = [
        "societe", "secteur", "type_comptes_resolu", "annee", "annee_cible",
        *features, "is_cible_t_plus_1", "variation_is_cible", "scenario",
        "decoupage_temporel",
    ]
    return sample[columns].sort_values(["annee_cible", "societe"])


def sample_summary(*samples: pd.DataFrame) -> pd.DataFrame:
    """Comparer les tailles des scénarios avant tout entraînement."""
    rows = []
    for sample in samples:
        scenario = sample["scenario"].iloc[0] if len(sample) else "vide"
        for split in ["ensemble", "apprentissage", "validation", "test"]:
            frame = sample if split == "ensemble" else sample[
                sample["decoupage_temporel"].eq(split)
            ]
            rows.append(
                {
                    "scenario": scenario,
                    "decoupage": split,
                    "observations": len(frame),
                    "societes": frame["societe"].nunique(),
                    "premiere_annee_cible": frame["annee_cible"].min(),
                    "derniere_annee_cible": frame["annee_cible"].max(),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    """Exécuter l'audit, exporter les résultats et arrêter sur erreur critique."""
    panel = pd.read_csv(SOURCE)
    panel["annee"] = pd.to_numeric(panel["annee"], errors="raise").astype(int)
    for variable in ALL_FINANCIAL_VARIABLES:
        panel[variable] = pd.to_numeric(panel[variable], errors="coerce")

    duplicates = panel[panel.duplicated(KEY, keep=False)].sort_values(KEY)
    if not duplicates.empty:
        raise ValueError("Le panel contient des doublons sur la clé société-année-type.")

    variable_coverage = coverage_by_variable(panel)
    yearly_coverage = coverage_by_year(panel)
    company_coverage = coverage_by_company(panel)
    missing = build_missing_register(panel)
    checks = accounting_checks(panel)
    scale_alerts = detect_scale_breaks(panel)

    structural = build_t_plus_one_sample(
        panel, STRUCTURAL_FEATURES, "Structurel sans variables fiscales contemporaines"
    )
    extended = build_t_plus_one_sample(
        panel, EXTENDED_FEATURES, "Étendu pour prévision t+1"
    )
    summary_samples = sample_summary(structural, extended)

    summary = pd.DataFrame(
        [
            ("Période théorique demandée", "2000-2025"),
            ("Période réellement observée", f"{panel.annee.min()}-{panel.annee.max()}"),
            ("Observations entreprise-année", len(panel)),
            ("Sociétés historiques", panel["societe"].nunique()),
            ("Doublons critiques", len(duplicates)),
            ("Valeurs prioritaires manquantes", len(missing)),
            ("Sociétés avec rupture de type de comptes", int(company_coverage["rupture_type_comptes"].sum())),
            ("Alertes de rupture d'échelle", len(scale_alerts)),
            ("Paires t+1 structurelles", len(structural)),
            ("Paires t+1 étendues", len(extended)),
        ],
        columns=["indicateur", "valeur"],
    )

    recommendations = pd.DataFrame(
        [
            (1, "Conserver 2003-2025 comme période observée; ne pas annoncer de données 2000-2002."),
            (2, "Utiliser le scénario t+1 : toutes les variables explicatives proviennent de l'année t."),
            (3, "Présenter le scénario structurel comme modèle principal anti-fuite."),
            (4, "Tester RCAI(t) et résultat net(t) seulement dans un scénario étendu séparé."),
            (5, "Ne pas imputer avant la séparation temporelle; ajuster toute imputation sur l'apprentissage uniquement."),
            (6, "Examiner les alertes d'échelle et de périmètre, sans correction automatique."),
            (7, "Comparer chaque modèle au naïf IS(t+1)=IS(t) sur le test 2024-2025."),
        ],
        columns=["ordre", "recommandation"],
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    structural.to_csv(SAMPLE_STRUCTURAL, index=False, encoding="utf-8-sig")
    extended.to_csv(SAMPLE_EXTENDED, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Synthese", index=False)
        variable_coverage.to_excel(writer, sheet_name="Couverture variables", index=False)
        yearly_coverage.to_excel(writer, sheet_name="Couverture annees", index=False)
        company_coverage.to_excel(writer, sheet_name="Couverture societes", index=False)
        missing.to_excel(writer, sheet_name="Registre manquants", index=False)
        checks.to_excel(writer, sheet_name="Controles comptables", index=False)
        scale_alerts.to_excel(writer, sheet_name="Alertes echelle", index=False)
        summary_samples.to_excel(writer, sheet_name="Echantillons", index=False)
        recommendations.to_excel(writer, sheet_name="Recommandations", index=False)

    print(summary.to_string(index=False))
    print("\nÉchantillons de modélisation :")
    print(summary_samples.to_string(index=False))
    print(f"\nCréé : {OUTPUT}")
    print(f"Créé : {SAMPLE_STRUCTURAL}")
    print(f"Créé : {SAMPLE_EXTENDED}")


if __name__ == "__main__":
    main()
