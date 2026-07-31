"""Outil temporaire de regroupement des anciens scripts du projet.

Ce programme lit la copie de sécurité des scripts puis crée des fichiers Python
thématiques. Le contenu original de chaque script est conservé textuellement.
Chaque groupe fournit une petite interface permettant de lister ou d'exécuter
une ancienne étape, par exemple :

    python 10_collecte_et_extraction.py --list
    python 10_collecte_et_extraction.py 56_telecharger_rapports_2025_lot_17

Ce générateur peut être supprimé après contrôle des fichiers produits.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / "archives_code" / "scripts_apres_rapport_3.zip"

REPORT_3_SCRIPTS = {
    "01_find_company_websites.py",
    "02_download_reports.py",
    "03_extract_financials.py",
    "04_build_dataset.py",
    "05_audit_coverage.py",
    "06_clean_manifest.py",
    "07_extract_all.py",
}


def group_for(name: str) -> str:
    """Classe un ancien script dans un groupe fonctionnel stable."""
    stem = Path(name).stem
    if stem in {"AMMC", "scraper_ammc", "main"}:
        return "08_sources_et_scraping.py"
    if stem == "diagnostic_taille":
        return "09_validation_et_diagnostics.py"

    try:
        number = int(stem.split("_", 1)[0])
    except ValueError:
        return "09_validation_et_diagnostics.py"

    if 8 <= number <= 23:
        return "09_validation_et_diagnostics.py"
    if 24 <= number <= 33:
        return "10_construction_du_panel.py"
    if 34 <= number <= 43:
        return "11_analyse_modelisation_prevision.py"
    if 44 <= number <= 55:
        return "12_variables_externes_et_univers.py"
    if 56 <= number <= 84:
        return "13_collecte_et_integration_2024_2025.py"
    return "14_integration_historique.py"


HEADER = '''\
"""Regroupement thématique des scripts de travail du projet.

IMPORTANT
---------
Les codes historiques n'ont pas été supprimés : leur contenu original est
conservé dans ``SCRIPTS``. Cette organisation évite d'avoir plus de cent
fichiers à la racine tout en maintenant la traçabilité et la reproductibilité.

Utilisation :
    python {filename} --list
    python {filename} NOM_DU_SCRIPT_SANS_EXTENSION

L'exécution se fait depuis la racine du projet afin de conserver les chemins
de fichiers attendus par les programmes historiques.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Chaque valeur contient, sans modification, le code source de l'ancien script.
SCRIPTS = {{
'''

FOOTER = '''\
}


def list_scripts() -> None:
    """Affiche les étapes historiques disponibles dans ce regroupement."""
    for script_name in sorted(SCRIPTS):
        print(script_name)


def run_script(script_name: str) -> None:
    """Exécute un script historique dans un espace de noms isolé."""
    normalized = script_name if script_name.endswith(".py") else f"{script_name}.py"
    if normalized not in SCRIPTS:
        choices = ", ".join(sorted(SCRIPTS))
        raise SystemExit(f"Script inconnu : {script_name}\\nChoix disponibles : {choices}")

    previous_directory = Path.cwd()
    os.chdir(PROJECT_ROOT)
    namespace = {
        "__name__": "__main__",
        "__file__": str(PROJECT_ROOT / normalized),
        "__package__": None,
    }
    try:
        source = SCRIPTS[normalized]
        exec(compile(source, namespace["__file__"], "exec"), namespace)
    finally:
        os.chdir(previous_directory)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lister ou exécuter une étape historique regroupée."
    )
    parser.add_argument("script", nargs="?", help="Nom du script à exécuter.")
    parser.add_argument(
        "--list", action="store_true", help="Afficher les scripts disponibles."
    )
    args = parser.parse_args()

    if args.list or not args.script:
        list_scripts()
        return
    run_script(args.script)


if __name__ == "__main__":
    main()
'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT,
        help="Dossier où créer les scripts regroupés.",
    )
    args = parser.parse_args()

    if not ARCHIVE.exists():
        raise SystemExit(f"Archive de sécurité introuvable : {ARCHIVE}")

    groups: dict[str, list[tuple[str, str]]] = {}
    with zipfile.ZipFile(ARCHIVE) as archive:
        for info in archive.infolist():
            name = Path(info.filename).name
            if not name.endswith(".py") or name in REPORT_3_SCRIPTS:
                continue
            source = archive.read(info).decode("utf-8-sig")
            groups.setdefault(group_for(name), []).append((name, source))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, list[str]] = {}
    for filename, scripts in sorted(groups.items()):
        output = args.output_dir / filename
        chunks = [HEADER.format(filename=filename)]
        for name, source in sorted(scripts):
            # repr garantit une conservation exacte et sûre du code, même si
            # celui-ci contient des guillemets ou des chaînes multilignes.
            chunks.append(f"    {name!r}: {source!r},\n")
        chunks.append(FOOTER)
        output.write_text("".join(chunks), encoding="utf-8")
        manifest[filename] = [name for name, _ in sorted(scripts)]

    manifest_path = args.output_dir / "REGROUPEMENT_SCRIPTS.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    total = sum(map(len, manifest.values()))
    print(f"{total} anciens scripts regroupés dans {len(groups)} fichiers.")
    print(f"Manifeste créé : {manifest_path}")


if __name__ == "__main__":
    main()
