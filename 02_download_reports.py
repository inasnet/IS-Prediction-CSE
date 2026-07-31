from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup


# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent

INPUT_FILE = BASE_DIR / "data" / "intermediate" / "societes_sites.xlsx"
OUTPUT_DIR = BASE_DIR / "reports" / "downloaded"
INTERMEDIATE_DIR = BASE_DIR / "data" / "intermediate"
LOG_DIR = BASE_DIR / "logs"

MANIFEST_CSV = INTERMEDIATE_DIR / "ammc_reports_manifest.csv"
MANIFEST_XLSX = INTERMEDIATE_DIR / "ammc_reports_manifest.xlsx"
CATALOG_XLSX = INTERMEDIATE_DIR / "ammc_catalog.xlsx"
LOG_FILE = LOG_DIR / "02_download_reports_ammc.log"

AMMC_BASE = "https://www.ammc.ma"
AMMC_LIST_URL = f"{AMMC_BASE}/fr/liste-etats-financiers-emetteurs"

REQUEST_TIMEOUT = 35
CONNECT_TIMEOUT = 15
PAGE_DELAY = 0.20
DETAIL_DELAY = 0.15
DOWNLOAD_RETRIES = 3
MAX_PDF_BYTES = 150 * 1024 * 1024
MIN_PDF_BYTES = 10_000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
}

# L'AMMC utilise plusieurs libellés selon les années.
ANNUAL_INCLUDE = (
    "rapports annuels",
    "rapport annuel",
    "rapports sociaux annuels",
    "rapport social annuel",
    "rapports consolides annuels",
    "rapport consolide annuel",
)

EXCLUDE = (
    "1er semestre",
    "premier semestre",
    "2e semestre",
    "deuxieme semestre",
    "semestriel",
    "trimestriel",
)

# Correspondances utiles entre le fichier utilisateur et les noms AMMC.
# Tu peux compléter cette table si une société n'est pas reconnue.
COMPANY_ALIASES = {
    "attijari wafa bank": ["attijariwafa bank"],
    "bank of africa": ["bank of africa groupe bmce", "boa", "bmce"],
    "bmce bank": ["bank of africa groupe bmce", "boa", "bmce"],
    "attijariwafa bank": ["attijariwafa bank"],
    "banque centrale populaire": ["banque centrale populaire", "bcp"],
    "maroc telecom": ["maroc telecom"],
    "cosumar": ["cosumar"],
    "lafargeholcim maroc": ["lafargeholcim maroc", "holcim maroc"],
    "ciments du maroc": ["ciments du maroc"],
    "societe des boissons du maroc": [
        "societe des boissons du maroc",
        "sbm",
    ],
    "brasseries du maroc": ["societe des boissons du maroc", "sbm"],
    "cnia saada": ["sanlam maroc", "saham assurance", "cnia saada"],
    "disty rfa": ["disty technologies"],
    "ennakl en dinar tunisien": ["ennakl automobiles"],
    "lafarge ciments": ["lafargeholcim maroc", "holcim maroc"],
    "les grandes marques et conserveries cherifiennes": ["lgmc"],
    "miniere touissit": ["compagnie miniere de touissit", "cmt"],
    "maroctelecom": ["maroc telecom"],
    "res dar saada": ["residences dar saada", "rds"],
    "residences dar saada": [
        "residences dar saada",
        "rds",
    ],
    "alliances developpement immobilier": [
        "alliances developpement immobilier",
        "adi",
    ],
    "sanlam maroc": [
        "sanlam maroc",
        "saham assurance",
    ],
    "taqa morocco": [
        "taqa morocco",
        "jlec",
    ],
    "credit du maroc": [
        "credit du maroc",
        "cdm",
    ],
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Company:
    """Entreprise cible et période annuelle à couvrir."""
    name: str
    sector: str
    year_start: int
    year_end: int

    @property
    def slug(self) -> str:
        """Identifiant de l'entreprise compatible avec un nom de dossier."""
        return slugify(self.name)


@dataclass
class CatalogRow:
    """Référence d'une publication repérée dans le catalogue AMMC."""
    issuer: str
    year: int
    report_type: str
    detail_url: str
    source_page: int


@dataclass
class DetailRow:
    """Publication enrichie avec le lien direct vers sa pièce jointe."""
    issuer: str
    year: int
    report_type: str
    detail_url: str
    pdf_url: str
    attachment_name: str


@dataclass
class ManifestRow:
    """Trace complète d'une tentative de téléchargement et de son résultat."""
    requested_company: str
    ammc_issuer: str
    sector: str
    year: int
    report_type: str
    detail_url: str
    pdf_url: str
    attachment_name: str
    local_path: str
    status: str
    size_bytes: int
    sha256: str
    duplicate_of: str
    error: str
    downloaded_at: str


# =============================================================================
# HELPERS
# =============================================================================

def normalize_text(value: str) -> str:
    """Normaliser les libellés avant les opérations de rapprochement."""
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(
        character
        for character in value
        if not unicodedata.combining(character)
    )
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def slugify(value: str) -> str:
    """Produire un identifiant stable utilisable dans un chemin de fichier."""
    return normalize_text(value).replace(" ", "_").strip("_")


def sanitize_filename(value: str) -> str:
    """Nettoyer un nom de fichier reçu depuis une source distante."""
    value = requests.utils.unquote(value)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        character
        for character in value
        if not unicodedata.combining(character)
    )
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", "_", value).strip("._")
    return value[:180]


def is_annual_type(report_type: str) -> bool:
    """Retenir les publications annuelles et exclure les fréquences infra-annuelles."""
    normalized = normalize_text(report_type)

    if any(normalize_text(item) in normalized for item in EXCLUDE):
        return False

    return any(
        normalize_text(item) in normalized
        for item in ANNUAL_INCLUDE
    )


def issuer_matches(company_name: str, issuer_name: str) -> bool:
    """Rapprocher une société cible d'un émetteur en tenant compte des alias."""
    company = normalize_text(company_name)
    issuer = normalize_text(issuer_name)

    if not company or not issuer:
        return False

    if company == issuer:
        return True

    # Les sous-chaînes très courtes créent des faux positifs graves :
    # par exemple « ona » est contenu dans « sonasid ». On n'autorise une
    # correspondance partielle que pour des libellés suffisamment spécifiques.
    company_compact = company.replace(" ", "")
    issuer_compact = issuer.replace(" ", "")
    if company_compact == issuer_compact:
        return True
    if issuer.startswith(company + " ") or company.startswith(issuer + " "):
        return True
    if min(len(company_compact), len(issuer_compact)) >= 6 and (
        company in issuer or issuer in company
    ):
        return True

    aliases = COMPANY_ALIASES.get(company, [])
    for alias in aliases:
        alias_n = normalize_text(alias)
        alias_compact = alias_n.replace(" ", "")
        if alias_n == issuer or alias_compact == issuer_compact:
            return True
        if min(len(alias_compact), len(issuer_compact)) >= 4 and (
            alias_n in issuer or issuer in alias_n
        ):
            return True
    return False


def sha256_file(path: Path) -> str:
    """Calculer l'empreinte du fichier pour détecter les doublons exacts."""
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def setup_logging(verbose: bool) -> None:
    """Configurer la journalisation vers le terminal et le fichier de suivi."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def create_session() -> requests.Session:
    """Créer une session HTTP réutilisable avec les en-têtes du projet."""
    session = requests.Session()
    session.headers.update(HEADERS)

    adapter = requests.adapters.HTTPAdapter(
        pool_connections=10,
        pool_maxsize=10,
        max_retries=0,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def load_companies() -> list[Company]:
    """Charger l'univers des sociétés et valider leurs bornes temporelles."""
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Fichier introuvable : {INPUT_FILE}")

    dataframe = pd.read_excel(INPUT_FILE, sheet_name="Sites retenus")

    required = {
        "Société",
        "Secteur",
        "Année début",
        "Année fin",
    }
    missing = required.difference(dataframe.columns)

    if missing:
        raise ValueError(
            "Colonnes manquantes : " + ", ".join(sorted(missing))
        )

    companies: list[Company] = []

    for _, row in dataframe.iterrows():
        if pd.isna(row["Société"]):
            continue

        companies.append(
            Company(
                name=str(row["Société"]).strip(),
                sector=str(row.get("Secteur", "") or "").strip(),
                year_start=int(row.get("Année début", 2010)),
                year_end=int(row.get("Année fin", 2025)),
            )
        )

    return companies


# =============================================================================
# AMMC CATALOG
# =============================================================================

def get_soup(
    session: requests.Session,
    url: str,
) -> BeautifulSoup:
    """Télécharger une page HTML et la convertir en arbre analysable."""
    response = session.get(
        url,
        timeout=(CONNECT_TIMEOUT, REQUEST_TIMEOUT),
    )
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def detect_last_page(soup: BeautifulSoup) -> int:
    """Déduire la dernière page disponible dans la pagination du catalogue."""
    pages = [0]

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        match = re.search(r"[?&]page=(\d+)", href)

        if match:
            pages.append(int(match.group(1)))

    return max(pages)


def parse_catalog_page(
    soup: BeautifulSoup,
    source_page: int,
) -> list[CatalogRow]:
    """Extraire les publications présentes sur une page du catalogue."""
    rows: list[CatalogRow] = []

    # La vue Drupal de l'AMMC contient généralement un tableau.
    for table_row in soup.select("table tbody tr"):
        cells = table_row.find_all("td")

        if len(cells) < 3:
            continue

        # La première cellule du tableau Drupal est une colonne technique vide.
        # Les trois cellules utiles sont toujours les trois dernières :
        # émetteur, année, type de rapport.
        issuer_cell, year_cell, report_cell = cells[-3:]
        issuer = issuer_cell.get_text(" ", strip=True)
        year_text = year_cell.get_text(" ", strip=True)
        report_type = report_cell.get_text(" ", strip=True)

        year_match = re.search(r"\b(20\d{2}|19\d{2})\b", year_text)
        link = report_cell.find(
            "a",
            href=re.compile(r"/espace-emetteurs/etats-financiers/"),
        )

        if not year_match or link is None:
            continue

        detail_url = urljoin(
            AMMC_BASE,
            link.get("href", ""),
        )

        rows.append(
            CatalogRow(
                issuer=issuer,
                year=int(year_match.group(1)),
                report_type=report_type,
                detail_url=detail_url,
                source_page=source_page,
            )
        )

    # Secours pour une structure Drupal sans balises <table>.
    if not rows:
        detail_links = soup.find_all(
            "a",
            href=re.compile(r"/espace-emetteurs/etats-financiers/"),
        )

        for link in detail_links:
            container = link.find_parent(
                ["tr", "article", "div", "li"]
            )
            if container is None:
                continue

            text = container.get_text(" ", strip=True)
            year_match = re.search(r"\b(20\d{2}|19\d{2})\b", text)

            if not year_match:
                continue

            issuer = link.get_text(" ", strip=True)
            normalized = normalize_text(text)

            report_type = ""
            for annual_label in ANNUAL_INCLUDE:
                if normalize_text(annual_label) in normalized:
                    report_type = annual_label
                    break

            if not report_type:
                for exclusion in EXCLUDE:
                    if normalize_text(exclusion) in normalized:
                        report_type = exclusion
                        break

            rows.append(
                CatalogRow(
                    issuer=issuer,
                    year=int(year_match.group(1)),
                    report_type=report_type,
                    detail_url=urljoin(
                        AMMC_BASE,
                        link.get("href", ""),
                    ),
                    source_page=source_page,
                )
            )

    unique: dict[str, CatalogRow] = {}

    for row in rows:
        unique[row.detail_url] = row

    return list(unique.values())


def crawl_catalog(
    session: requests.Session,
    max_pages: int | None = None,
) -> list[CatalogRow]:
    """Extraire les publications présentes sur une page du catalogue."""
    """Parcourir le catalogue AMMC et collecter les publications annuelles."""
    logging.info("Lecture de la première page du catalogue AMMC…")
    first_soup = get_soup(session, AMMC_LIST_URL)
    last_page = detect_last_page(first_soup)

    if max_pages is not None:
        last_page = min(last_page, max_pages - 1)

    logging.info(
        "Pagination AMMC détectée : pages 0 à %d.",
        last_page,
    )

    catalog: list[CatalogRow] = parse_catalog_page(first_soup, 0)
    logging.info(
        "Page AMMC 1/%d : %d ligne(s).",
        last_page + 1,
        len(catalog),
    )

    def fetch_catalog_page(page: int) -> tuple[int, list[CatalogRow]]:
        """Collecter une page avec une session HTTP propre au traitement."""
        # Une session par tâche évite de partager l'état HTTP entre threads.
        local_session = create_session()
        url = f"{AMMC_LIST_URL}?page={page}"
        soup = get_soup(local_session, url)
        time.sleep(PAGE_DELAY)
        return page, parse_catalog_page(soup, page)

    # Quatre travailleurs restent raisonnables pour le serveur public AMMC.
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(fetch_catalog_page, page): page
            for page in range(1, last_page + 1)
        }
        for future in as_completed(futures):
            page, rows = future.result()
            catalog.extend(rows)
            logging.info(
                "Page AMMC %d/%d : %d ligne(s).",
                page + 1,
                last_page + 1,
                len(rows),
            )

    unique = {
        row.detail_url: row
        for row in catalog
    }

    result = list(unique.values())

    logging.info(
        "Catalogue AMMC : %d fiche(s) unique(s).",
        len(result),
    )

    return result


def save_catalog(rows: list[CatalogRow]) -> None:
    """Enregistrer le catalogue collecté sous une forme tabulaire contrôlable."""
    dataframe = pd.DataFrame([asdict(row) for row in rows])

    CATALOG_XLSX.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(CATALOG_XLSX, engine="openpyxl") as writer:
        dataframe.to_excel(
            writer,
            sheet_name="Catalogue AMMC",
            index=False,
        )

        worksheet = writer.book["Catalogue AMMC"]
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

        widths = {
            "A": 36,
            "B": 12,
            "C": 34,
            "D": 75,
            "E": 14,
        }
        for column, width in widths.items():
            worksheet.column_dimensions[column].width = width


def load_or_build_catalog(
    session: requests.Session,
    refresh: bool,
    max_pages: int | None,
) -> list[CatalogRow]:
    """Réutiliser le catalogue local ou le reconstruire selon les options."""
    if CATALOG_XLSX.exists() and not refresh:
        dataframe = pd.read_excel(
            CATALOG_XLSX,
            sheet_name="Catalogue AMMC",
        )

        rows = [
            CatalogRow(
                issuer=str(row["issuer"]),
                year=int(row["year"]),
                report_type=str(row["report_type"]),
                detail_url=str(row["detail_url"]),
                source_page=int(row["source_page"]),
            )
            for _, row in dataframe.iterrows()
        ]

        logging.info(
            "Catalogue AMMC chargé depuis le cache : %d fiche(s).",
            len(rows),
        )
        return rows

    rows = crawl_catalog(
        session=session,
        max_pages=max_pages,
    )
    save_catalog(rows)
    return rows


# =============================================================================
# DETAIL PAGES
# =============================================================================

def parse_detail_page(
    session: requests.Session,
    catalog_row: CatalogRow,
) -> DetailRow:
    """Extraire le lien direct depuis la fiche d'une publication."""
    soup = get_soup(session, catalog_row.detail_url)

    page_text = soup.get_text(" ", strip=True)

    issuer = catalog_row.issuer
    year = catalog_row.year
    report_type = catalog_row.report_type

    # Lien PDF : priorité aux liens explicitement situés près de
    # "Pièce jointe", puis à tout lien PDF de la fiche.
    pdf_links: list[tuple[str, str]] = []

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        text = link.get_text(" ", strip=True)

        if (
            ".pdf" in href.lower()
            or "sites/default/files" in href.lower()
        ):
            pdf_links.append(
                (
                    urljoin(AMMC_BASE, href),
                    text,
                )
            )

    if not pdf_links:
        raise ValueError(
            f"Aucune pièce jointe trouvée : {catalog_row.detail_url}"
        )

    # Éviter les PDF génériques de la navigation du site.
    preferred = [
        item
        for item in pdf_links
        if (
            ".pdf" in item[0].lower()
            or ".pdf" in item[1].lower()
        )
    ]

    pdf_url, attachment_name = (
        preferred[0]
        if preferred
        else pdf_links[0]
    )

    if not attachment_name:
        attachment_name = Path(
            urlparse(pdf_url).path
        ).name

    # Vérification secondaire des métadonnées textuelles.
    year_match = re.search(
        r"Ann[ée]e\s*(20\d{2}|19\d{2})",
        page_text,
        re.IGNORECASE,
    )
    if year_match:
        year = int(year_match.group(1))

    return DetailRow(
        issuer=issuer,
        year=year,
        report_type=report_type,
        detail_url=catalog_row.detail_url,
        pdf_url=pdf_url,
        attachment_name=attachment_name,
    )


# =============================================================================
# DOWNLOAD
# =============================================================================

def load_manifest() -> pd.DataFrame:
    """Charger le manifeste existant pour permettre une reprise sans perte."""
    if MANIFEST_CSV.exists():
        try:
            return pd.read_csv(MANIFEST_CSV)
        except Exception:
            pass

    return pd.DataFrame()


def manifest_dataframe_to_rows(
    dataframe: pd.DataFrame,
) -> list[ManifestRow]:
    """Restaurer les objets du manifeste depuis leur table sauvegardée."""
    if dataframe.empty:
        return []

    rows: list[ManifestRow] = []

    for _, row in dataframe.iterrows():
        values = {}

        for field_name in ManifestRow.__dataclass_fields__:
            value = row.get(field_name, "")

            if pd.isna(value):
                value = ""

            values[field_name] = value

        try:
            values["year"] = int(values["year"])
            values["size_bytes"] = int(values["size_bytes"] or 0)
            # Écarter les anciennes associations créées par une règle de
            # sous-chaîne trop permissive (ex. Sonacid associé à ONA).
            if not issuer_matches(
                str(values["requested_company"]),
                str(values["ammc_issuer"]),
            ):
                logging.warning(
                    "Entrée de manifeste ignorée : %s <> %s (%s).",
                    values["requested_company"],
                    values["ammc_issuer"],
                    values["year"],
                )
                continue
            rows.append(ManifestRow(**values))
        except Exception:
            continue

    return rows


def save_manifest(rows: list[ManifestRow]) -> None:
    """Sauvegarder l'état détaillé des téléchargements en CSV et en Excel."""
    dataframe = pd.DataFrame([asdict(row) for row in rows])

    MANIFEST_CSV.parent.mkdir(parents=True, exist_ok=True)
    dataframe.to_csv(
        MANIFEST_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    with pd.ExcelWriter(MANIFEST_XLSX, engine="openpyxl") as writer:
        dataframe.to_excel(
            writer,
            sheet_name="Rapports AMMC",
            index=False,
        )

        if not dataframe.empty:
            summary = (
                dataframe.groupby(
                    [
                        "requested_company",
                        "year",
                        "report_type",
                        "status",
                    ]
                )
                .size()
                .reset_index(name="nombre")
            )
        else:
            summary = pd.DataFrame()

        summary.to_excel(
            writer,
            sheet_name="Résumé",
            index=False,
        )

        worksheet = writer.book["Rapports AMMC"]
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

        widths = {
            "A": 30, "B": 34, "C": 28, "D": 12, "E": 34,
            "F": 72, "G": 72, "H": 45, "I": 55, "J": 18,
            "K": 15, "L": 68, "M": 55, "N": 55, "O": 22,
        }
        for column, width in widths.items():
            worksheet.column_dimensions[column].width = width


def download_pdf(
    session: requests.Session,
    company: Company,
    detail: DetailRow,
    known_hashes: dict[str, str],
) -> ManifestRow:
    """Télécharger, contrôler et référencer une pièce jointe financière."""
    temp_dir = INTERMEDIATE_DIR / "tmp_ammc"
    temp_dir.mkdir(parents=True, exist_ok=True)

    temp_path = temp_dir / (
        hashlib.md5(detail.pdf_url.encode("utf-8")).hexdigest()
        + ".part"
    )

    last_error = ""

    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            with session.get(
                detail.pdf_url,
                timeout=(CONNECT_TIMEOUT, REQUEST_TIMEOUT),
                allow_redirects=True,
                stream=True,
            ) as response:
                response.raise_for_status()

                total = 0
                first_bytes = b""

                with temp_path.open("wb") as output:
                    for chunk in response.iter_content(128 * 1024):
                        if not chunk:
                            continue

                        if not first_bytes:
                            first_bytes = chunk[:8]

                        total += len(chunk)

                        if total > MAX_PDF_BYTES:
                            raise ValueError(
                                "Taille maximale dépassée"
                            )

                        output.write(chunk)

                if total < MIN_PDF_BYTES:
                    raise ValueError(
                        f"Fichier trop petit : {total} octets"
                    )

                if not first_bytes.startswith(b"%PDF"):
                    raise ValueError(
                        "Le contenu téléchargé n'est pas un PDF"
                    )

                file_hash = sha256_file(temp_path)

                if file_hash in known_hashes:
                    temp_path.unlink(missing_ok=True)

                    return ManifestRow(
                        requested_company=company.name,
                        ammc_issuer=detail.issuer,
                        sector=company.sector,
                        year=detail.year,
                        report_type=detail.report_type,
                        detail_url=detail.detail_url,
                        pdf_url=response.url,
                        attachment_name=detail.attachment_name,
                        local_path="",
                        status="duplicate",
                        size_bytes=total,
                        sha256=file_hash,
                        duplicate_of=known_hashes[file_hash],
                        error="",
                        downloaded_at=datetime.now().isoformat(
                            timespec="seconds"
                        ),
                    )

                report_kind = (
                    "consolide"
                    if "consolid" in normalize_text(detail.report_type)
                    else "social"
                    if "social" in normalize_text(detail.report_type)
                    else "annuel"
                )

                company_dir = (
                    OUTPUT_DIR
                    / company.slug
                    / str(detail.year)
                )
                company_dir.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                filename = sanitize_filename(
                    f"{company.slug}_{detail.year}_"
                    f"{report_kind}_{file_hash[:10]}.pdf"
                )
                destination = company_dir / filename

                temp_path.replace(destination)

                local_path = str(
                    destination.relative_to(BASE_DIR)
                )
                known_hashes[file_hash] = local_path

                return ManifestRow(
                    requested_company=company.name,
                    ammc_issuer=detail.issuer,
                    sector=company.sector,
                    year=detail.year,
                    report_type=detail.report_type,
                    detail_url=detail.detail_url,
                    pdf_url=response.url,
                    attachment_name=detail.attachment_name,
                    local_path=local_path,
                    status="downloaded",
                    size_bytes=total,
                    sha256=file_hash,
                    duplicate_of="",
                    error="",
                    downloaded_at=datetime.now().isoformat(
                        timespec="seconds"
                    ),
                )

        except Exception as exc:
            last_error = str(exc)
            temp_path.unlink(missing_ok=True)

            if attempt < DOWNLOAD_RETRIES:
                time.sleep(2 ** attempt)

    return ManifestRow(
        requested_company=company.name,
        ammc_issuer=detail.issuer,
        sector=company.sector,
        year=detail.year,
        report_type=detail.report_type,
        detail_url=detail.detail_url,
        pdf_url=detail.pdf_url,
        attachment_name=detail.attachment_name,
        local_path="",
        status="failed",
        size_bytes=0,
        sha256="",
        duplicate_of="",
        error=last_error,
        downloaded_at=datetime.now().isoformat(
            timespec="seconds"
        ),
    )


# =============================================================================
# ORCHESTRATION
# =============================================================================

def select_catalog_rows(
    company: Company,
    catalog: list[CatalogRow],
) -> list[CatalogRow]:
    """Sélectionner les publications correspondant à l'entreprise et à sa période."""
    selected = [
        row
        for row in catalog
        if (
            company.year_start <= row.year <= company.year_end
            and issuer_matches(company.name, row.issuer)
            and is_annual_type(row.report_type)
        )
    ]

    # Garder au maximum une fiche par année et par type.
    unique: dict[tuple[int, str], CatalogRow] = {}

    for row in selected:
        key = (
            row.year,
            normalize_text(row.report_type),
        )
        unique[key] = row

    return sorted(
        unique.values(),
        key=lambda row: (
            row.year,
            row.report_type,
        ),
        reverse=True,
    )


def process_company(
    company: Company,
    catalog: list[CatalogRow],
    manifest_rows: list[ManifestRow],
    preview: bool,
) -> list[ManifestRow]:
    """Restaurer les objets du manifeste depuis leur table sauvegardée."""
    """Traiter toutes les publications compatibles avec une société cible."""
    logging.info("=" * 74)
    logging.info("ENTREPRISE : %s", company.name)
    logging.info("=" * 74)

    selected = select_catalog_rows(
        company=company,
        catalog=catalog,
    )

    logging.info(
        "[%s] %d fiche(s) annuelle(s) AMMC retenue(s).",
        company.name,
        len(selected),
    )

    for row in selected:
        logging.info(
            "[%s] %s | %s | %s",
            company.name,
            row.year,
            row.report_type,
            row.detail_url,
        )

    if preview:
        return []

    processed_detail_urls = {
        row.detail_url
        for row in manifest_rows
        if (
            row.requested_company == company.name
            and row.detail_url
            and row.status in {"downloaded", "duplicate"}
        )
    }

    known_hashes = {
        row.sha256: row.local_path
        for row in manifest_rows
        if row.sha256 and row.local_path
    }

    session = create_session()
    new_rows: list[ManifestRow] = []

    for index, catalog_row in enumerate(selected, start=1):
        if catalog_row.detail_url in processed_detail_urls:
            logging.info(
                "[%s] %d/%d | déjà traité | %s",
                company.name,
                index,
                len(selected),
                catalog_row.detail_url,
            )
            continue

        try:
            detail = parse_detail_page(
                session=session,
                catalog_row=catalog_row,
            )

            row = download_pdf(
                session=session,
                company=company,
                detail=detail,
                known_hashes=known_hashes,
            )

        except Exception as exc:
            row = ManifestRow(
                requested_company=company.name,
                ammc_issuer=catalog_row.issuer,
                sector=company.sector,
                year=catalog_row.year,
                report_type=catalog_row.report_type,
                detail_url=catalog_row.detail_url,
                pdf_url="",
                attachment_name="",
                local_path="",
                status="failed",
                size_bytes=0,
                sha256="",
                duplicate_of="",
                error=str(exc),
                downloaded_at=datetime.now().isoformat(
                    timespec="seconds"
                ),
            )

        new_rows.append(row)

        logging.info(
            "[%s] %d/%d | %s | %s | %s",
            company.name,
            index,
            len(selected),
            row.status,
            row.year,
            row.local_path or row.error,
        )

        time.sleep(DETAIL_DELAY)

    return new_rows


def parse_arguments() -> argparse.Namespace:
    """Lire les options de reprise, de parallélisme et de filtrage."""
    parser = argparse.ArgumentParser(
        description=(
            "Télécharge les états financiers annuels depuis l'AMMC."
        )
    )

    parser.add_argument(
        "--company",
        help="Traiter seulement une société contenant ce texte.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Afficher les fiches AMMC sans télécharger les PDF.",
    )
    parser.add_argument(
        "--refresh-catalog",
        action="store_true",
        help="Reconstruire le catalogue AMMC complet.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignorer le manifeste de téléchargement existant.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limiter le nombre de sociétés.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help=(
            "Limiter temporairement le nombre de pages AMMC "
            "pour un test. 0 = toutes les pages."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
    )

    return parser.parse_args()


def main() -> None:
    """Orchestrer la collecte et produire un manifeste reproductible."""
    arguments = parse_arguments()
    setup_logging(arguments.verbose)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logging.info("")
    logging.info("=" * 74)
    logging.info("ÉTAPE 02 — VERSION 5 AMMC")
    logging.info("=" * 74)
    logging.info("Source principale : %s", AMMC_LIST_URL)
    logging.info("États semestriels et trimestriels : exclus")
    logging.info("États sociaux et consolidés annuels : inclus")

    companies = load_companies()

    if arguments.company:
        query = normalize_text(arguments.company)
        companies = [
            company
            for company in companies
            if query in normalize_text(company.name)
        ]

    if arguments.limit > 0:
        companies = companies[:arguments.limit]

    if not companies:
        raise SystemExit(
            "Aucune société ne correspond aux critères."
        )

    session = create_session()

    catalog = load_or_build_catalog(
        session=session,
        refresh=arguments.refresh_catalog,
        max_pages=(
            arguments.max_pages
            if arguments.max_pages > 0
            else None
        ),
    )

    if arguments.fresh:
        manifest_rows: list[ManifestRow] = []
    else:
        manifest_rows = manifest_dataframe_to_rows(
            load_manifest()
        )

    for position, company in enumerate(companies, start=1):
        logging.info("")
        logging.info(
            "[%d/%d] %s",
            position,
            len(companies),
            company.name,
        )

        try:
            new_rows = process_company(
                company=company,
                catalog=catalog,
                manifest_rows=manifest_rows,
                preview=arguments.preview,
            )
            manifest_rows.extend(new_rows)

            if not arguments.preview:
                save_manifest(manifest_rows)

        except KeyboardInterrupt:
            if not arguments.preview:
                save_manifest(manifest_rows)
            raise

        except Exception as exc:
            logging.exception(
                "[%s] Erreur non bloquante : %s",
                company.name,
                exc,
            )

    if not arguments.preview:
        save_manifest(manifest_rows)

    logging.info("")
    logging.info("=" * 74)
    logging.info("TERMINÉ")
    logging.info("=" * 74)

    if arguments.preview:
        logging.info(
            "Mode aperçu : aucun PDF téléchargé."
        )
    else:
        logging.info(
            "Rapports téléchargés : %d",
            sum(row.status == "downloaded" for row in manifest_rows),
        )
        logging.info(
            "Doublons : %d",
            sum(row.status == "duplicate" for row in manifest_rows),
        )
        logging.info(
            "Échecs : %d",
            sum(row.status == "failed" for row in manifest_rows),
        )
        logging.info(
            "Manifeste : %s",
            MANIFEST_XLSX,
        )

    logging.info(
        "Catalogue AMMC : %s",
        CATALOG_XLSX,
    )


if __name__ == "__main__":
    main()
