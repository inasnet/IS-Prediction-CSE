"""
16_statistiques_descriptives.py

Analyse descriptive de l'échantillon structurel destiné à prévoir l'IS à t+1.

Le script :
- détecte automatiquement la version la plus récente de
  data/output/echantillon_t_plus_1_structurel_v*.csv ;
- décrit l'échantillon et la cible IS_{t+1} ;
- calcule les statistiques descriptives des variables numériques ;
- analyse les valeurs manquantes, les valeurs infinies et les outliers IQR ;
- calcule les corrélations de Pearson et de Spearman ;
- classe les variables selon leur relation avec la cible ;
- signale les couples de variables fortement corrélées ;
- produit quelques graphiques utiles à l'analyse ;
- exporte les résultats dans data/output/analyse_descriptive/.

Aucun traitement, aucune winsorisation et aucune transformation n'est appliqué
aux données : cette étape sert uniquement à comprendre l'échantillon propre.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
ANALYSIS_DIR = OUTPUT_DIR / "analyse_descriptive"
FIGURES_DIR = ANALYSIS_DIR / "figures"

DEFAULT_PATTERN = "echantillon_t_plus_1_structurel_v*.csv"

TARGET_CANDIDATES = [
    "is_cible_t_plus_1",
    "is_t_plus_1",
    "is_t1",
    "is_t+1",
    "is_annee_suivante",
    "cible_is_t_plus_1",
]

COMPANY_CANDIDATES = [
    "societe",
    "entreprise",
    "company",
    "raison_sociale",
]

YEAR_T_CANDIDATES = [
    "annee",
    "annee_t",
    "year",
]

YEAR_TARGET_CANDIDATES = [
    "annee_cible",
    "annee_t_plus_1",
    "annee_t1",
    "target_year",
]

# Colonnes numériques qui ne doivent normalement pas être interprétées comme
# variables financières explicatives.
NON_FEATURE_PATTERNS = (
    r"^index$",
    r"^id$",
    r"identifiant",
    r"version",
    r"split",
    r"cible_disponible",
    r"manquant$",
)


def normalize_name(value: str) -> str:
    """Normalise un nom de colonne pour faciliter sa détection."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().strip()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def latest_version_key(path: Path) -> tuple[int, float]:
    """Retourne la version numérique et la date de modification d'un fichier."""
    match = re.search(r"_v(\d+)", path.stem, flags=re.IGNORECASE)
    version = int(match.group(1)) if match else -1
    return version, path.stat().st_mtime


def find_input_file(explicit_path: str | None) -> Path:
    """Trouve le fichier fourni ou la dernière version structurelle disponible."""
    if explicit_path:
        path = Path(explicit_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            raise FileNotFoundError(f"Fichier introuvable : {path}")
        return path

    candidates = list(OUTPUT_DIR.glob(DEFAULT_PATTERN))
    if not candidates:
        raise FileNotFoundError(
            "Aucun fichier structurel trouvé avec le motif :\n"
            f"{OUTPUT_DIR / DEFAULT_PATTERN}\n"
            "Utilise --input pour fournir un chemin explicite."
        )
    return max(candidates, key=latest_version_key)


def read_csv_robust(path: Path) -> pd.DataFrame:
    """Lit un CSV en testant les encodages et séparateurs les plus fréquents."""
    attempts = [
        {"encoding": "utf-8-sig", "sep": None, "engine": "python"},
        {"encoding": "utf-8", "sep": None, "engine": "python"},
        {"encoding": "latin-1", "sep": None, "engine": "python"},
    ]
    errors: list[str] = []
    for kwargs in attempts:
        try:
            df = pd.read_csv(path, **kwargs)
            if df.shape[1] == 1:
                continue
            return df
        except Exception as exc:  # pragma: no cover - journalisation utile
            errors.append(f"{kwargs}: {exc}")
    raise RuntimeError(
        f"Impossible de lire {path}.\nTentatives :\n" + "\n".join(errors)
    )


def find_column(
    columns: Iterable[str],
    candidates: Iterable[str],
    required: bool = False,
) -> str | None:
    """Détecte une colonne à partir de plusieurs noms possibles."""
    normalized = {normalize_name(col): col for col in columns}

    for candidate in candidates:
        key = normalize_name(candidate)
        if key in normalized:
            return normalized[key]

    # Recherche plus souple si le nom exact n'existe pas.
    for candidate in candidates:
        key = normalize_name(candidate)
        for normalized_name, original in normalized.items():
            if key and (key in normalized_name or normalized_name in key):
                return original

    if required:
        raise KeyError(
            "Colonne requise introuvable. Candidats testés : "
            + ", ".join(candidates)
        )
    return None


def coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convertit en numérique les colonnes textuelles contenant majoritairement
    des nombres, sans modifier les vraies variables catégorielles.
    """
    result = df.copy()

    for column in result.columns:
        if pd.api.types.is_numeric_dtype(result[column]):
            continue

        series = result[column]
        cleaned = (
            series.astype("string")
            .str.replace("\u202f", "", regex=False)
            .str.replace("\xa0", "", regex=False)
            .str.replace(" ", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.replace(r"^\((.*)\)$", r"-\1", regex=True)
            .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "-": pd.NA})
        )
        converted = pd.to_numeric(cleaned, errors="coerce")

        original_non_missing = series.notna().sum()
        if original_non_missing == 0:
            continue

        conversion_rate = converted.notna().sum() / original_non_missing
        if conversion_rate >= 0.90:
            result[column] = converted

    return result


def safe_cv(mean: float, std: float) -> float:
    """Coefficient de variation, non calculé lorsque la moyenne est quasi nulle."""
    if pd.isna(mean) or abs(mean) < 1e-12:
        return np.nan
    return std / abs(mean)


def descriptive_statistics(df: pd.DataFrame, numeric_columns: list[str]) -> pd.DataFrame:
    """Calcule les statistiques descriptives des colonnes numériques."""
    rows: list[dict[str, object]] = []
    total_rows = len(df)

    for column in numeric_columns:
        raw = pd.to_numeric(df[column], errors="coerce")
        positive_inf = int(np.isposinf(raw).sum())
        negative_inf = int(np.isneginf(raw).sum())
        series = raw.replace([np.inf, -np.inf], np.nan).dropna()

        n = int(series.count())
        missing = total_rows - n
        mean = series.mean() if n else np.nan
        std = series.std(ddof=1) if n > 1 else np.nan

        rows.append(
            {
                "variable": column,
                "n": n,
                "valeurs_manquantes": missing,
                "taux_manquant_pct": 100 * missing / total_rows if total_rows else np.nan,
                "moyenne": mean,
                "mediane": series.median() if n else np.nan,
                "ecart_type": std,
                "coefficient_variation": safe_cv(mean, std),
                "minimum": series.min() if n else np.nan,
                "q1": series.quantile(0.25) if n else np.nan,
                "q3": series.quantile(0.75) if n else np.nan,
                "maximum": series.max() if n else np.nan,
                "asymetrie": series.skew() if n >= 3 else np.nan,
                "kurtosis_exces": series.kurt() if n >= 4 else np.nan,
                "valeurs_uniques": int(series.nunique()) if n else 0,
                "plus_infini": positive_inf,
                "moins_infini": negative_inf,
            }
        )

    return pd.DataFrame(rows)


def iqr_outliers(df: pd.DataFrame, numeric_columns: list[str]) -> pd.DataFrame:
    """Compte les valeurs situées hors de [Q1 - 1,5 IQR ; Q3 + 1,5 IQR]."""
    rows: list[dict[str, object]] = []

    for column in numeric_columns:
        series = (
            pd.to_numeric(df[column], errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .dropna()
        )
        if series.empty:
            continue

        q1 = series.quantile(0.25)
        q3 = series.quantile(0.75)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr

        if iqr == 0:
            count = int(((series < q1) | (series > q3)).sum())
        else:
            count = int(((series < lower) | (series > upper)).sum())

        rows.append(
            {
                "variable": column,
                "n_valide": int(series.size),
                "q1": q1,
                "q3": q3,
                "iqr": iqr,
                "borne_basse": lower,
                "borne_haute": upper,
                "nombre_outliers_iqr": count,
                "taux_outliers_pct": 100 * count / series.size,
            }
        )

    return pd.DataFrame(rows).sort_values(
        ["taux_outliers_pct", "nombre_outliers_iqr"],
        ascending=False,
        ignore_index=True,
    )


def correlation_with_target(
    df: pd.DataFrame,
    numeric_columns: list[str],
    target: str,
) -> pd.DataFrame:
    """Classe les variables selon Pearson et Spearman avec la cible."""
    rows: list[dict[str, object]] = []

    for column in numeric_columns:
        if column == target:
            continue

        pair = (
            df[[column, target]]
            .replace([np.inf, -np.inf], np.nan)
            .apply(pd.to_numeric, errors="coerce")
            .dropna()
        )

        if len(pair) < 3 or pair[column].nunique() < 2 or pair[target].nunique() < 2:
            pearson = np.nan
            spearman = np.nan
        else:
            pearson = pair[column].corr(pair[target], method="pearson")
            spearman = pair[column].corr(pair[target], method="spearman")

        rows.append(
            {
                "variable": column,
                "n_paires": len(pair),
                "correlation_pearson": pearson,
                "correlation_spearman": spearman,
                "abs_pearson": abs(pearson) if pd.notna(pearson) else np.nan,
                "abs_spearman": abs(spearman) if pd.notna(spearman) else np.nan,
            }
        )

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(
            ["abs_spearman", "abs_pearson"],
            ascending=False,
            ignore_index=True,
        )
    return result


def strong_correlation_pairs(
    correlation_matrix: pd.DataFrame,
    threshold: float = 0.90,
) -> pd.DataFrame:
    """Liste une seule fois les couples dont la corrélation absolue dépasse le seuil."""
    rows: list[dict[str, object]] = []
    columns = list(correlation_matrix.columns)

    for i, first in enumerate(columns):
        for second in columns[i + 1 :]:
            value = correlation_matrix.loc[first, second]
            if pd.notna(value) and abs(value) >= threshold:
                rows.append(
                    {
                        "variable_1": first,
                        "variable_2": second,
                        "correlation": value,
                        "correlation_absolue": abs(value),
                    }
                )

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(
            "correlation_absolue", ascending=False, ignore_index=True
        )
    return result


def select_feature_columns(
    numeric_columns: list[str],
    target: str,
    year_columns: Iterable[str | None],
) -> list[str]:
    """Retire la cible, les années et les identifiants numériques évidents."""
    excluded = {target, *(col for col in year_columns if col)}

    features = []
    for column in numeric_columns:
        normalized = normalize_name(column)
        if column in excluded:
            continue
        if any(re.search(pattern, normalized) for pattern in NON_FEATURE_PATTERNS):
            continue
        features.append(column)
    return features


def save_target_figures(df: pd.DataFrame, target: str) -> None:
    """Produit l'histogramme brut, le boxplot et le logarithme signé de la cible."""
    target_dir = FIGURES_DIR / "cible"
    target_dir.mkdir(parents=True, exist_ok=True)

    series = (
        pd.to_numeric(df[target], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    if series.empty:
        return

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(series, bins="auto", edgecolor="black", alpha=0.75)
    ax.axvline(series.mean(), linestyle="--", linewidth=1.5, label="Moyenne")
    ax.axvline(series.median(), linestyle=":", linewidth=1.8, label="Médiane")
    ax.set_title(f"Distribution de la cible : {target}")
    ax.set_xlabel("Montant")
    ax.set_ylabel("Fréquence")
    ax.legend()
    fig.tight_layout()
    fig.savefig(target_dir / "distribution_cible_brute.png", dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 3.5))
    ax.boxplot(series, vert=False, showfliers=True)
    ax.set_title(f"Boxplot de la cible : {target}")
    ax.set_xlabel("Montant")
    fig.tight_layout()
    fig.savefig(target_dir / "boxplot_cible.png", dpi=180)
    plt.close(fig)

    signed_log = np.sign(series) * np.log1p(np.abs(series))
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(signed_log, bins="auto", edgecolor="black", alpha=0.75)
    ax.set_title(f"Distribution logarithmique signée de la cible : {target}")
    ax.set_xlabel("signe(IS) × log(1 + |IS|)")
    ax.set_ylabel("Fréquence")
    fig.tight_layout()
    fig.savefig(target_dir / "distribution_cible_log_signee.png", dpi=180)
    plt.close(fig)


def save_correlation_barplot(target_correlations: pd.DataFrame, limit: int = 15) -> None:
    """Trace les variables ayant les plus fortes corrélations de Spearman."""
    if target_correlations.empty:
        return

    plot_data = (
        target_correlations.dropna(subset=["correlation_spearman"])
        .head(limit)
        .sort_values("correlation_spearman")
    )
    if plot_data.empty:
        return

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    height = max(5, 0.42 * len(plot_data))

    fig, ax = plt.subplots(figsize=(10, height))
    ax.barh(plot_data["variable"], plot_data["correlation_spearman"])
    ax.axvline(0, linewidth=1)
    ax.set_title("Variables les plus corrélées avec IS à t+1 — Spearman")
    ax.set_xlabel("Corrélation de Spearman")
    ax.set_ylabel("")
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "correlations_spearman_avec_cible.png", dpi=180)
    plt.close(fig)


def save_scatterplots(
    df: pd.DataFrame,
    target: str,
    target_correlations: pd.DataFrame,
    limit: int = 8,
) -> None:
    """Produit les nuages de points des variables les plus corrélées à la cible."""
    scatter_dir = FIGURES_DIR / "relations_cible"
    scatter_dir.mkdir(parents=True, exist_ok=True)

    selected = target_correlations.dropna(subset=["abs_spearman"]).head(limit)

    for _, row in selected.iterrows():
        variable = str(row["variable"])
        pair = (
            df[[variable, target]]
            .replace([np.inf, -np.inf], np.nan)
            .apply(pd.to_numeric, errors="coerce")
            .dropna()
        )
        if len(pair) < 3:
            continue

        x = pair[variable].to_numpy(dtype=float)
        y = pair[target].to_numpy(dtype=float)

        fig, ax = plt.subplots(figsize=(7.5, 5))
        ax.scatter(x, y, alpha=0.75)

        # Droite descriptive seulement si le calcul est numériquement stable.
        if np.unique(x).size >= 2 and np.isfinite(x).all() and np.isfinite(y).all():
            try:
                slope, intercept = np.polyfit(x, y, deg=1)
                x_line = np.linspace(x.min(), x.max(), 100)
                ax.plot(x_line, intercept + slope * x_line, linestyle="--")
            except (np.linalg.LinAlgError, ValueError, FloatingPointError):
                pass

        ax.set_title(
            f"{variable} et {target}\n"
            f"Spearman = {row['correlation_spearman']:.3f}"
        )
        ax.set_xlabel(variable)
        ax.set_ylabel(target)
        fig.tight_layout()

        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", variable)
        fig.savefig(scatter_dir / f"{safe_name}_vs_cible.png", dpi=180)
        plt.close(fig)


def build_sample_summary(
    df: pd.DataFrame,
    input_file: Path,
    target: str,
    company_col: str | None,
    year_t_col: str | None,
    year_target_col: str | None,
) -> pd.DataFrame:
    """Construit un résumé compact de l'échantillon."""
    target_numeric = pd.to_numeric(df[target], errors="coerce")

    values: list[tuple[str, object]] = [
        ("fichier_source", input_file.name),
        ("observations", len(df)),
        ("colonnes", df.shape[1]),
        ("cible", target),
        ("cibles_non_manquantes", int(target_numeric.notna().sum())),
        ("cibles_manquantes", int(target_numeric.isna().sum())),
        ("cibles_negatives", int((target_numeric < 0).sum())),
        ("cibles_nulles", int((target_numeric == 0).sum())),
        ("cibles_positives", int((target_numeric > 0).sum())),
        ("doublons_lignes_completes", int(df.duplicated().sum())),
    ]

    if company_col:
        values.append(("nombre_entreprises", int(df[company_col].nunique(dropna=True))))

    selected_year = year_target_col or year_t_col
    if selected_year:
        years = pd.to_numeric(df[selected_year], errors="coerce").dropna()
        values.extend(
            [
                ("colonne_annee_analysee", selected_year),
                ("annee_min", int(years.min()) if not years.empty else np.nan),
                ("annee_max", int(years.max()) if not years.empty else np.nan),
                ("nombre_annees", int(years.nunique()) if not years.empty else 0),
            ]
        )

    if company_col and year_t_col:
        key = [company_col, year_t_col]
        values.append(
            (
                "doublons_entreprise_annee_t",
                int(df.duplicated(key, keep=False).sum()),
            )
        )

    return pd.DataFrame(values, columns=["indicateur", "valeur"])


def print_key_results(
    input_file: Path,
    sample_summary: pd.DataFrame,
    target_stats: pd.Series,
    target_correlations: pd.DataFrame,
    strong_pairs: pd.DataFrame,
) -> None:
    """Affiche dans le terminal les résultats les plus utiles."""
    summary = dict(zip(sample_summary["indicateur"], sample_summary["valeur"]))

    print("\n" + "=" * 78)
    print("ANALYSE DESCRIPTIVE DE L'ÉCHANTILLON STRUCTUREL")
    print("=" * 78)
    print(f"Fichier : {input_file}")
    print(f"Observations : {summary.get('observations')}")
    if "nombre_entreprises" in summary:
        print(f"Entreprises : {summary.get('nombre_entreprises')}")
    if "annee_min" in summary:
        print(f"Période cible : {summary.get('annee_min')}–{summary.get('annee_max')}")

    print("\nCible IS à t+1")
    for label, key in [
        ("N", "n"),
        ("Moyenne", "moyenne"),
        ("Médiane", "mediane"),
        ("Écart-type", "ecart_type"),
        ("Minimum", "minimum"),
        ("Maximum", "maximum"),
        ("Asymétrie", "asymetrie"),
        ("Kurtosis (excès)", "kurtosis_exces"),
    ]:
        value = target_stats.get(key, np.nan)
        if isinstance(value, (float, np.floating)) and pd.notna(value):
            print(f"  {label:<20}: {value:,.4f}")
        else:
            print(f"  {label:<20}: {value}")

    print("\nPrincipales corrélations de Spearman avec la cible")
    display_columns = ["variable", "n_paires", "correlation_spearman"]
    print(
        target_correlations[display_columns]
        .head(10)
        .to_string(index=False, float_format=lambda value: f"{value:.3f}")
    )

    print(f"\nCouples de prédicteurs avec |corrélation de Spearman| ≥ 0,90 : {len(strong_pairs)}")
    print(f"Résultats enregistrés dans : {ANALYSIS_DIR}")
    print("=" * 78 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Statistiques descriptives de l'échantillon structurel t+1."
    )
    parser.add_argument(
        "--input",
        help=(
            "Chemin du CSV à analyser. Sans cet argument, le script choisit "
            "automatiquement la version v* la plus récente."
        ),
    )
    parser.add_argument(
        "--target",
        help="Nom exact de la cible. Par défaut, détection automatique.",
    )
    parser.add_argument(
        "--top-scatter",
        type=int,
        default=8,
        help="Nombre de relations avec la cible à représenter (défaut : 8).",
    )
    args = parser.parse_args()

    try:
        input_file = find_input_file(args.input)
        raw_df = read_csv_robust(input_file)
        df = coerce_numeric_columns(raw_df)
        print("\n===== INFORMATIONS SUR LA BASE =====")
        print("Dimensions :", df.shape)

        print("\nColonnes :")
        for col in df.columns:
               print(col)

        print("\nVariables numériques :")
        print(df.select_dtypes(include="number").columns.tolist())

        print("====================================\n")

        target = args.target or find_column(
            df.columns, TARGET_CANDIDATES, required=True
        )
        company_col = find_column(df.columns, COMPANY_CANDIDATES)
        year_t_col = find_column(df.columns, YEAR_T_CANDIDATES)
        year_target_col = find_column(df.columns, YEAR_TARGET_CANDIDATES)

        numeric_columns = list(df.select_dtypes(include=[np.number, "bool"]).columns)
        if target not in numeric_columns:
            df[target] = pd.to_numeric(df[target], errors="coerce")
            numeric_columns = list(
                df.select_dtypes(include=[np.number, "bool"]).columns
            )

        if target not in numeric_columns:
            raise TypeError(f"La cible {target!r} ne peut pas être convertie en numérique.")

        feature_columns = select_feature_columns(
            numeric_columns,
            target,
            [year_t_col, year_target_col],
        )
        analysis_columns = feature_columns + [target]

        ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
        FIGURES_DIR.mkdir(parents=True, exist_ok=True)

        sample_summary = build_sample_summary(
            df=df,
            input_file=input_file,
            target=target,
            company_col=company_col,
            year_t_col=year_t_col,
            year_target_col=year_target_col,
        )

        descriptives = descriptive_statistics(df, analysis_columns)
        target_stats = descriptives.loc[
            descriptives["variable"].eq(target)
        ].iloc[0]

        missing = (
            descriptives[
                ["variable", "n", "valeurs_manquantes", "taux_manquant_pct"]
            ]
            .sort_values(
                ["taux_manquant_pct", "valeurs_manquantes"],
                ascending=False,
                ignore_index=True,
            )
        )

        outliers = iqr_outliers(df, analysis_columns)

        clean_numeric = (
            df[analysis_columns]
            .apply(pd.to_numeric, errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
        )
        pearson = clean_numeric.corr(method="pearson")
        spearman = clean_numeric.corr(method="spearman")

        target_correlations = correlation_with_target(
            df=df,
            numeric_columns=feature_columns + [target],
            target=target,
        )

        # On analyse la multicolinéarité entre prédicteurs, sans inclure la cible.
        predictor_spearman = spearman.loc[feature_columns, feature_columns]
        strong_pairs_090 = strong_correlation_pairs(
            predictor_spearman, threshold=0.90
        )
        strong_pairs_095 = strong_correlation_pairs(
            predictor_spearman, threshold=0.95
        )

        constants = descriptives.loc[
            descriptives["valeurs_uniques"] <= 1,
            ["variable", "n", "valeurs_uniques"],
        ].reset_index(drop=True)

        # Export Excel central, utile pour lire les résultats sans produire de rapport.
        workbook = ANALYSIS_DIR / "statistiques_descriptives_structurelles.xlsx"
        with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
            sample_summary.to_excel(writer, sheet_name="Resume_echantillon", index=False)
            descriptives.to_excel(writer, sheet_name="Descriptives", index=False)
            missing.to_excel(writer, sheet_name="Valeurs_manquantes", index=False)
            outliers.to_excel(writer, sheet_name="Outliers_IQR", index=False)
            target_correlations.to_excel(
                writer, sheet_name="Correlations_avec_cible", index=False
            )
            pearson.to_excel(writer, sheet_name="Matrice_Pearson")
            spearman.to_excel(writer, sheet_name="Matrice_Spearman")
            strong_pairs_090.to_excel(
                writer, sheet_name="Paires_corr_090", index=False
            )
            strong_pairs_095.to_excel(
                writer, sheet_name="Paires_corr_095", index=False
            )
            constants.to_excel(writer, sheet_name="Variables_constantes", index=False)

        # CSV pratiques pour les étapes de sélection et de modélisation.
        descriptives.to_csv(
            ANALYSIS_DIR / "descriptives_variables.csv",
            index=False,
            encoding="utf-8-sig",
        )
        target_correlations.to_csv(
            ANALYSIS_DIR / "correlations_avec_is_t_plus_1.csv",
            index=False,
            encoding="utf-8-sig",
        )
        outliers.to_csv(
            ANALYSIS_DIR / "diagnostic_outliers_iqr.csv",
            index=False,
            encoding="utf-8-sig",
        )
        strong_pairs_090.to_csv(
            ANALYSIS_DIR / "paires_fortement_correllees_090.csv",
            index=False,
            encoding="utf-8-sig",
        )

        metadata = {
            "fichier_source": str(input_file),
            "cible": target,
            "colonne_societe": company_col,
            "colonne_annee_t": year_t_col,
            "colonne_annee_cible": year_target_col,
            "variables_numeriques_analysees": analysis_columns,
            "variables_explicatives_numeriques": feature_columns,
            "dossier_resultats": str(ANALYSIS_DIR),
        }
        with (ANALYSIS_DIR / "parametres_analyse.json").open(
            "w", encoding="utf-8"
        ) as file:
            json.dump(metadata, file, ensure_ascii=False, indent=2)

        save_target_figures(df, target)
        save_correlation_barplot(target_correlations)
        save_scatterplots(
            df=df,
            target=target,
            target_correlations=target_correlations,
            limit=max(0, args.top_scatter),
        )

        print_key_results(
            input_file=input_file,
            sample_summary=sample_summary,
            target_stats=target_stats,
            target_correlations=target_correlations,
            strong_pairs=strong_pairs_090,
        )
        return 0

    except Exception as exc:
        print(f"\nERREUR : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())