# Prévision de l’Impôt sur les Sociétés des entreprises cotées à la Bourse de Casablanca

Ce projet de stage développe une chaîne complète de traitement destinée à analyser
et à prévoir l’Impôt sur les Sociétés (IS) des entreprises cotées à la Bourse de
Casablanca. Il associe collecte de données financières, contrôle de qualité,
construction d’un panel entreprise–année, analyse statistique et Machine Learning.

## Problématique

L’objectif est d’expliquer et d’anticiper l’évolution annuelle de l’IS à partir
des caractéristiques comptables et financières propres à chaque entreprise.
L’utilisation d’un panel permet de combiner :

- la dimension temporelle, à travers plusieurs exercices ;
- la dimension individuelle, à travers plusieurs entreprises ;
- la dimension sectorielle, à travers les différentes branches d’activité.

Cette organisation augmente le nombre d’observations disponibles comparativement
à une unique série annuelle agrégée et permet d’étudier l’hétérogénéité entre
les entreprises.

## Variable cible

La variable à prédire est le montant annuel de l’Impôt sur les Sociétés associé
à chaque couple `entreprise–année`.

## Variables explicatives

Les principales variables financières retenues sont :

| Dimension | Variables |
|---|---|
| Activité | Chiffre d’affaires |
| Rentabilité | EBIT, EBITDA, résultat financier, RCAI, résultat net |
| Taille | Total de l’actif, capitaux propres |
| Structure financière | Total des dettes, dettes financières |
| Liquidité | Trésorerie et équivalents |
| Investissement | Immobilisations corporelles |
| Fiscalité | Impôt différé, taux effectif d’imposition |
| Contrôle | Société, année, secteur, nature des comptes |

Le RCAI désigne le **résultat courant avant impôt**. Le taux effectif
d’imposition est calculé à partir des variables fiscales et du résultat avant
impôt lorsque les données nécessaires sont disponibles.

## Démarche méthodologique

Le pipeline suit les étapes suivantes :

1. identification des entreprises et des sources financières ;
2. collecte et extraction des données ;
3. normalisation des dénominations, unités et formats ;
4. distinction entre comptes sociaux et comptes consolidés ;
5. détection des doublons, incohérences et valeurs manquantes ;
6. rapprochement avec la base annuelle d’IS ;
7. construction du panel entreprise–année ;
8. production de statistiques descriptives ;
9. création et sélection des variables explicatives ;
10. validation temporelle et comparaison des modèles ;
11. génération de prévisions individuelles par entreprise.

La séparation chronologique des ensembles d’entraînement et de test est
privilégiée afin d’éviter d’utiliser, lors de l’apprentissage, des informations
postérieures à la période prédite.

## Modélisation

Le projet compare plusieurs familles de méthodes :

- références naïves servant de points de comparaison ;
- modèles économétriques adaptés aux données de panel ;
- régularisation linéaire avec Ridge et Elastic Net ;
- algorithmes de Machine Learning supervisé ;
- modèles intégrant les effets temporels, individuels et sectoriels.

Les performances sont évaluées hors échantillon avec des indicateurs adaptés,
notamment la MAE, la RMSE et, lorsque son interprétation est pertinente, la
variation relative de l’erreur.

## Organisation du projet

```text
.
├── 01_find_company_websites.py
├── 02_download_reports.py
├── 03_extract_financials.py
├── 04_build_dataset.py
├── 05_audit_coverage.py
├── 06_clean_manifest.py
├── 07_extract_all.py
├── 08_sources_et_scraping.py
├── 09_validation_et_diagnostics.py
├── 10_construction_du_panel.py
├── 11_analyse_modelisation_prevision.py
├── 12_variables_externes_et_univers.py
├── 13_collecte_et_integration_2024_2025.py
├── 14_integration_historique.py
├── data/
│   ├── input/
│   ├── intermediate/
│   └── output/
├── REGROUPEMENT_SCRIPTS.json
└── requirements.txt
```

Les scripts `01_` à `07_` forment le pipeline initial. Les scripts `08_` à
`14_` regroupent les traitements complémentaires par thème afin de conserver
une base de code lisible et maintenable. Le fichier
`REGROUPEMENT_SCRIPTS.json` assure la traçabilité des anciennes étapes après
leur regroupement.

## Installation

Prérequis : Python 3.10 ou une version ultérieure.

```bash
python -m venv .venv
```

Sous Windows :

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Sous Linux ou macOS :

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Exécution

Le pipeline initial peut être exécuté dans l’ordre suivant :

```bash
python 01_find_company_websites.py
python 02_download_reports.py
python 03_extract_financials.py
python 04_build_dataset.py
python 05_audit_coverage.py
python 06_clean_manifest.py
python 07_extract_all.py
```

Pour afficher les traitements disponibles dans un script regroupé :

```bash
python 13_collecte_et_integration_2024_2025.py --list
```

## Principes de qualité

- traçabilité de la source et de l’année de chaque observation ;
- conservation de la nature sociale ou consolidée des comptes ;
- harmonisation des noms d’entreprises et des unités monétaires ;
- contrôle des doublons et des valeurs aberrantes ;
- mesure explicite du taux de couverture de chaque variable ;
- prévention des fuites de données pendant la validation ;
- comparaison systématique avec un modèle naïf ;
- reproductibilité des transformations et des résultats.

## Technologies utilisées

- Python
- pandas et NumPy
- scikit-learn
- statsmodels
- openpyxl

## Autrice

**Inas Ait Benaddi**

Projet réalisé dans le cadre d’un stage consacré à la modélisation et à la
prévision de l’Impôt sur les Sociétés.
