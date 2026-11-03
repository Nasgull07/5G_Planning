import argparse
import csv
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path


DETAIL_URL = "https://geoportal.minetur.gob.es/VCTEL/detalleEstacion.do"
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "processed"
SOURCE_CSV = DATA_DIR / "cellular_sites.csv"
OUTPUT_CSV = DATA_DIR / "cellular_site_details.csv"
CHECKPOINT_CSV = OUTPUT_CSV.with_suffix(".partial.csv")
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2
DEFAULT_WORKERS = 2
OUTPUT_COLUMNS = [
    "Gis_ID",
    "Gis_Codigo",
    "direccion",
    "tipo_registro",
    "operador",
    "referencia",
    "banda_asignada_MHz",
    "distancia_m",
    "acimut_grados",
    "valor_medido_uW_cm2",
    "estado",
    "error",
]
SUCCESS_STATUSES = {"OK", "SIN_DATOS"}


class StationTablesParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._table_depth = 0
        self._current_table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "table":
            if self._current_table is None and attributes.get("class") == "tablaDetalleEstacionNivelesMedidos":
                self._current_table = []
                self._table_depth = 1
            elif self._current_table is not None:
                self._table_depth += 1
        elif self._current_table is not None and self._table_depth == 1:
            if tag == "tr":
                self._row = []
            elif tag in {"td", "th"} and self._row is not None:
                self._cell = []
            elif tag == "br" and self._cell is not None:
                self._cell.append(" ")

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if self._current_table is None:
            return
        if tag in {"td", "th"} and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self._current_table.append(self._row)
            self._row = None
        elif tag == "table":
            self._table_depth -= 1
            if self._table_depth == 0:
                self.tables.append(self._current_table)
                self._current_table = None


def identify_tables(html):
    parser = StationTablesParser()
    parser.feed(html)
    location = []
    technical = []
    measurements = []

    for rows in parser.tables:
        if not rows:
            continue
        headings = [value.casefold() for value in rows[0]]
        if "operador" in headings and "referencia" in headings and any("banda asignada" in value for value in headings):
            technical = rows[1:]
        elif any("distancia" in value for value in headings) and any("acimut" in value for value in headings) and any("valor medido" in value for value in headings):
            measurements = rows[1:]
        elif any("codigo" in value or "código" in value for value in headings) and any("direccion" in value or "dirección" in value for value in headings):
            location = rows[1:]

    address = location[0][1] if location and len(location[0]) > 1 else ""
    return address, technical, measurements


def make_records(station, address, technical, measurements):
    base = {
        "Gis_ID": station["Gis_ID"],
        "Gis_Codigo": station.get("Gis_Codigo", ""),
        "direccion": address,
        "estado": "OK",
        "error": "",
    }
    records = []
    for row in technical:
        values = (row + [""] * 3)[:3]
        if any(values):
            records.append({
                **base,
                "tipo_registro": "caracteristica_tecnica",
                "operador": values[0],
                "referencia": values[1],
                "banda_asignada_MHz": values[2],
            })
    for row in measurements:
        values = (row + [""] * 3)[:3]
        if any(values):
            records.append({
                **base,
                "tipo_registro": "nivel_medido",
                "distancia_m": values[0],
                "acimut_grados": values[1],
                "valor_medido_uW_cm2": values[2],
            })
    if not records:
        return [{**base, "tipo_registro": "sin_datos", "estado": "SIN_DATOS"}]
    return records


def fetch_station(station):
    identifier = station.get("Gis_ID", "").strip()
    code = station.get("Gis_Codigo", "").strip()
    if not identifier or not code:
        return [{
            "Gis_ID": identifier,
            "Gis_Codigo": code,
            "tipo_registro": "error",
            "estado": "ERROR",
            "error": "Falta Gis_ID o Gis_Codigo en el CSV de entrada.",
        }]

    query = urllib.parse.urlencode({
        "emplazamiento": identifier,
        "codEmplazamiento": code,
    })
    request = urllib.request.Request(
        f"{DETAIL_URL}?{query}",
        headers={"User-Agent": "cellular-site-details-export/1.0", "Accept": "text/html"},
    )
    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                html = response.read().decode(charset)
            address, technical, measurements = identify_tables(html)
            return make_records(station, address, technical, measurements)
        except (OSError, UnicodeError) as error:
            last_error = error
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS * (2 ** attempt))

    return [{
        "Gis_ID": identifier,
        "Gis_Codigo": code,
        "tipo_registro": "error",
        "estado": "ERROR",
        "error": str(last_error),
    }]


def read_successful_rows(path):
    if not path.exists():
        return [], set()
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    successful = [row for row in rows if row.get("estado") in SUCCESS_STATUSES]
    completed_codes = {
        row["Gis_Codigo"] for row in successful if row.get("Gis_Codigo")
    }
    return successful, completed_codes


def main():
    argument_parser = argparse.ArgumentParser(
        description="Descarga los datos técnicos de las estaciones del CSV de entrada."
    )
    argument_parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Consultas simultáneas (por defecto: {DEFAULT_WORKERS}).",
    )
    argument_parser.add_argument(
        "--limit",
        type=int,
        help="Limita el número de estaciones para una prueba.",
    )
    args = argument_parser.parse_args()
    if args.workers < 1:
        argument_parser.error("--workers debe ser al menos 1.")

    with SOURCE_CSV.open("r", newline="", encoding="utf-8-sig") as source:
        stations = list(csv.DictReader(source))
    if any(not station.get("Gis_Codigo", "").strip() for station in stations):
        raise ValueError(f"Hay filas sin Gis_Codigo en {SOURCE_CSV}.")
    if args.limit is not None:
        if args.limit < 1:
            argument_parser.error("--limit debe ser al menos 1.")
        stations = stations[:args.limit]

    prior_path = CHECKPOINT_CSV if CHECKPOINT_CSV.exists() else OUTPUT_CSV
    retained_rows, completed_codes = read_successful_rows(prior_path)
    stations_by_code = {
        station.get("Gis_Codigo", "").strip(): station
        for station in stations
        if station.get("Gis_Codigo", "").strip()
    }
    pending = [
        station
        for code, station in stations_by_code.items()
        if code not in completed_codes
    ]
    CHECKPOINT_CSV.parent.mkdir(parents=True, exist_ok=True)

    with CHECKPOINT_CSV.open("w", newline="", encoding="utf-8-sig") as checkpoint:
        writer = csv.DictWriter(checkpoint, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(retained_rows)
        checkpoint.flush()

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(fetch_station, station): station for station in pending}
            total = len(pending)
            failed = 0
            new_successful_codes = set()
            for completed, future in enumerate(as_completed(futures), start=1):
                records = future.result()
                if records and records[0].get("estado") == "ERROR":
                    failed += 1
                else:
                    code = records[0].get("Gis_Codigo", "").strip() if records else ""
                    if code:
                        new_successful_codes.add(code)
                writer.writerows(records)
                checkpoint.flush()
                if completed % 25 == 0 or completed == total:
                    print(
                        f"Consultas completadas: {completed}/{total}; errores: {failed}",
                        flush=True,
                    )

    os.replace(CHECKPOINT_CSV, OUTPUT_CSV)
    successful_codes = {
        row.get("Gis_Codigo", "").strip()
        for row in retained_rows
        if row.get("estado") in SUCCESS_STATUSES and row.get("Gis_Codigo", "").strip()
    }
    successful_codes.update(new_successful_codes)
    print(
        f"CSV creado: {OUTPUT_CSV} "
        f"({len(successful_codes)} Gis_Codigo completados; {failed} errores nuevos)."
    )


if __name__ == "__main__":
    main()
