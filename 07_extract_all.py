"""Exécute l'extracteur financier pour toutes les sociétés téléchargées."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
MANIFEST = BASE_DIR / "data" / "intermediate" / "ammc_reports_manifest_clean.csv"
LOG_FILE = BASE_DIR / "logs" / "extract_all.log"


def main() -> None:
    manifest = pd.read_csv(MANIFEST)
    companies = sorted(
        manifest.loc[manifest["status"].eq("downloaded"), "requested_company"]
        .dropna()
        .unique()
    )
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as log:
        for index, company in enumerate(companies, start=1):
            message = f"[{index}/{len(companies)}] {company}"
            print(message, flush=True)
            log.write(message + "\n")
            log.flush()
            result = subprocess.run(
                [
                    sys.executable,
                    str(BASE_DIR / "03_extract_financials.py"),
                    "--company",
                    str(company),
                ],
                cwd=BASE_DIR,
                stdout=log,
                stderr=log,
            )
            if result.returncode:
                error = f"ERREUR {result.returncode} : {company}"
                print(error, flush=True)
                log.write(error + "\n")
                log.flush()


if __name__ == "__main__":
    main()
