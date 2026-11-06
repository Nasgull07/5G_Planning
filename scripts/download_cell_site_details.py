import argparse
import csv
import json
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
    "operador",
    "bandas_MHz",
    "numero_banda",
    "bandas",
    "tecnologia",
    "clasificacion_asumida",
    "estado",
    "error",
]
SUCCESS_STATUSES = {"OK", "SIN_DATOS"}
BAND_FREQUENCY_RANGES = {
    "LTE": {
        "B1": ((1920, 1980), (2110, 2170)),
        "B3": ((1710, 1785), (1805, 1880)),
        "B4": ((1710, 1755), (2110, 2155)),
        "B7": ((2500, 2570), (2620, 2690)),
        "B8": ((880, 915), (925, 960)),
        "B20": ((791, 821), (832, 862)),
        "B28": ((703, 748), (758, 803)),
        "B38": ((2570, 2620),),
        "B40": ((2300, 2400),),
        "B41": ((2496, 2690),),
        "B42": ((3400, 3600),),
        "B43": ((3600, 3800),),
    },
    "NR": {
        "n1": ((1920, 1980), (2110, 2170)),
        "n3": ((1710, 1785), (1805, 1880)),
        "n7": ((2500, 2570), (2620, 2690)),
        "n8": ((880, 915), (925, 960)),
        "n20": ((791, 821), (832, 862)),
        "n28": ((703, 748), (758, 803)),
        "n38": ((2570, 2620),),
        "n77": ((3300, 4200),),
        "n78": ((3300, 3800),),
    },
}


def band_metadata(bands):
    numbers = []
    technologies = set()
    band_numbers = set()
    for band in bands:
        matched_bands = {"frecuencia_MHz": band, "LTE": [], "NR": []}
        for segment in band.split(";"):
            lower, upper = (
                float(value.strip())
                for value in segment.split("-", maxsplit=1)
            )
            for technology, frequency_bands in BAND_FREQUENCY_RANGES.items():
                for number, ranges in frequency_bands.items():
                    if any(start <= lower and upper <= end for start, end in ranges):
                        if number not in matched_bands[technology]:
                            matched_bands[technology].append(number)
                        band_numbers.add(int(number.lstrip("Bn")))
                        technologies.add(technology)
        numbers.append(matched_bands)
    return (
        json.dumps(numbers, ensure_ascii=False),
        json.dumps(sorted(band_numbers), ensure_ascii=False),
        json.dumps(sorted(technologies), ensure_ascii=False),
    )


def classify_bands(band_numbers):
    if any("n78" in item["NR"] for item in band_numbers):
        return "5G"
    lte_4g_bands = {"B4", "B7", "B20", "B28", "B38", "B40", "B41"}
    if any(lte_4g_bands.intersection(item["LTE"]) for item in band_numbers):
        return "4G"
    return "2G/3G"


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

    for rows in parser.tables:
        if not rows:
            continue
        headings = [value.casefold() for value in rows[0]]
        if "operador" in headings and "referencia" in headings and any("banda asignada" in value for value in headings):
            technical = rows[1:]
        elif any("codigo" in value or "código" in value for value in headings) and any("direccion" in value or "dirección" in value for value in headings):
            location = rows[1:]

    address = location[0][1] if location and len(location[0]) > 1 else ""
    return address, technical


def make_records(station, address, technical):
    base = {
        "Gis_ID": station["Gis_ID"],
        "Gis_Codigo": station.get("Gis_Codigo", ""),
        "direccion": address,
        "operador": "",
        "bandas_MHz": "[]",
        "numero_banda": "[]",
        "bandas": "[]",
        "tecnologia": "[]",
        "clasificacion_asumida": "EXCLUIR",
        "estado": "SIN_DATOS",
        "error": "",
    }
    operators = []
    bands = []
    for row in technical:
        values = (row + [""] * 3)[:3]
        if any(values):
            if values[0] and values[0] not in operators:
                operators.append(values[0])
            if values[2] and values[2] not in bands:
                bands.append(values[2])
    base["operador"] = "; ".join(operators)
    base["bandas_MHz"] = json.dumps(bands, ensure_ascii=False)
    (
        base["numero_banda"],
        base["bandas"],
        base["tecnologia"],
    ) = band_metadata(bands)
    base["clasificacion_asumida"] = classify_bands(
        json.loads(base["numero_banda"])
    )
    if bands:
        base["estado"] = "OK"
    return [base]


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
            address, technical = identify_tables(html)
            return make_records(station, address, technical)
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
        reader = csv.DictReader(source)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    if "bandas_MHz" in fieldnames:
        successful = [row for row in rows if row.get("estado") in SUCCESS_STATUSES]
        for row in successful:
            bands = json.loads(row.get("bandas_MHz") or "[]")
            (
                row["numero_banda"],
                row["bandas"],
                row["tecnologia"],
            ) = band_metadata(bands)
            row["clasificacion_asumida"] = classify_bands(
                json.loads(row["numero_banda"])
            )
    else:
        stations = {}
        for row in rows:
            if row.get("estado") not in SUCCESS_STATUSES:
                continue
            code = row.get("Gis_Codigo", "")
            if not code:
                continue
            station = stations.setdefault(code, {
                "Gis_ID": row.get("Gis_ID", ""),
                "Gis_Codigo": code,
                "direccion": row.get("direccion", ""),
                "operador": "",
                "bandas_MHz": "[]",
                "numero_banda": "[]",
                "bandas": "[]",
                "tecnologia": "[]",
                "clasificacion_asumida": "2G/3G",
                "estado": "SIN_DATOS",
                "error": "",
            })
            operators = [value for value in station["operador"].split("; ") if value]
            operator = row.get("operador", "").strip()
            if operator and operator not in operators:
                operators.append(operator)
                station["operador"] = "; ".join(operators)
            band = row.get("banda_asignada_MHz", "").strip()
            bands = json.loads(station["bandas_MHz"])
            if band and band not in bands:
                bands.append(band)
                station["bandas_MHz"] = json.dumps(bands, ensure_ascii=False)
                station["estado"] = "OK"
            (
                station["numero_banda"],
                station["bandas"],
                station["tecnologia"],
            ) = band_metadata(json.loads(station["bandas_MHz"]))
            station["clasificacion_asumida"] = classify_bands(
                json.loads(station["numero_banda"]),
            )
        successful = list(stations.values())
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
