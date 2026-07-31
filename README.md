# Prédiction de l’Impôt sur les Sociétés — Bourse de Casablanca

Projet de stage consacré à la construction d’un panel financier et à la
prédiction exploratoire de l’impôt sur les sociétés (IS) des entreprises
cotées à la Bourse de Casablanca.

## Objectifs

- collecter les rapports et états financiers publiés ;
- extraire et harmoniser les variables financières ;
- rapprocher les variables explicatives de la base annuelle d’IS ;
- construire un panel entreprise–année ;
- réaliser les statistiques descriptives et les contrôles de qualité ;
- comparer des modèles naïfs, économétriques et de Machine Learning ;
- préparer des prévisions exploratoires de l’IS.

## Variables principales

La base couvre notamment le chiffre d’affaires, l’EBIT, l’EBITDA, le
résultat financier, le résultat avant impôt (RCAI), le résultat net, le
total de l’actif, les capitaux propres, les dettes, la trésorerie, les
immobilisations corporelles et l’impôt différé.

## Organisation du code

Les scripts `01_` à `07_` constituent le pipeline initial présenté dans
le rapport hebdomadaire n°3. Les scripts `08_` à `14_` regroupent les
étapes ultérieures par thème :

- sources et scraping ;
- validation et diagnostics ;
- construction du panel ;
- analyse, modélisation et prévision ;
- variables externes et univers boursier ;
- intégrations 2024–2025 ;
- intégrations historiques.

Le fichier `REGROUPEMENT_SCRIPTS.json` indique dans quel module thématique
se trouve chaque ancienne étape.

## Données et reproductibilité

Les rapports PDF bruts, caches et fichiers temporaires ne sont pas
versionnés en raison de leur volume. Ils peuvent être reconstruits à
l’aide des scripts de collecte. Les données d’entrée légères et les
résultats tabulaires utiles sont conservés dans `data/`.

Les premiers résultats de modélisation sont exploratoires : ils ont été
calculés sur une version antérieure et plus restreinte de la base. Les
modèles doivent être réentraînés après consolidation du panel récent.

## Installation

```bash
python -m venv .venv
python -m pip install -r requirements.txt
```

## Exécution du pipeline initial

```bash
python 01_find_company_websites.py
python 02_download_reports.py
python 03_extract_financials.py
python 04_build_dataset.py
python 05_audit_coverage.py
python 06_clean_manifest.py
python 07_extract_all.py
```

Les scripts regroupés peuvent lister leurs anciennes étapes avec :

```bash
python 13_collecte_et_integration_2024_2025.py --list
```

## Avertissement

Les données proviennent de publications financières publiques. Les
prévisions produites dans ce projet sont expérimentales et ne constituent
ni une information fiscale officielle ni un conseil financier.

