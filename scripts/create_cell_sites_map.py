import argparse
import csv
import html
import json
import random
from collections import defaultdict
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "processed"
SITES_CSV = DATA_DIR / "cellular_sites.csv"
DETAILS_CSV = DATA_DIR / "cellular_site_details.csv"
SAMPLE_CSV = DATA_DIR / "350_CELLS.csv"
OUTPUT_HTML = PROJECT_DIR / "output" / "cellular_sites_map.html"
OPERATOR_ALIASES = {
    "vodafone españa, s.a.": "VODAFONE ESPAÑA, S.A.U.",
    "vodafone españa, s.a.u.": "VODAFONE ESPAÑA, S.A.U.",
}


def normalize_operator(operator):
    value = operator.strip()
    return OPERATOR_ALIASES.get(value.casefold(), value)


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def group_stations(sites, details):
    details_by_code = defaultdict(list)
    technologies_by_code = {}
    classifications_by_code = {}
    operators_by_code = {}
    site_ids_by_code = {}
    band_numbers_by_code = {}
    for row in details:
        code = row.get("Gis_Codigo", "").strip()
        if not code:
            continue
        operator = normalize_operator(row.get("operador", ""))
        operators_by_code[code] = operator
        site_ids_by_code[code] = row.get("Gis_ID", "").strip()
        if "bandas_MHz" in row:
            try:
                bands = json.loads(row["bandas_MHz"] or "[]")
                band_numbers = json.loads(row.get("numero_banda") or "[]")
                numeric_bands = json.loads(row.get("bandas") or "[]")
            except json.JSONDecodeError as error:
                raise ValueError(f"Metadatos de bandas inválidos para Gis_Codigo={code!r}.") from error
            if not isinstance(bands, list) or not all(isinstance(band, str) for band in bands):
                raise ValueError(f"Array de bandas inválido para Gis_Codigo={code!r}.")
            if not isinstance(band_numbers, list) or not all(
                isinstance(item, dict) and isinstance(item.get("frecuencia_MHz"), str)
                for item in band_numbers
            ):
                raise ValueError(f"Array de números de banda inválido para Gis_Codigo={code!r}.")
            if not isinstance(numeric_bands, list) or not all(
                isinstance(number, int) and not isinstance(number, bool)
                for number in numeric_bands
            ):
                raise ValueError(f"Array de bandas inválido para Gis_Codigo={code!r}.")
            band_numbers_by_code[code] = numeric_bands
            band_numbers_by_frequency = {
                item["frecuencia_MHz"]: item for item in band_numbers
            }
            technologies = json.loads(row.get("tecnologia") or "[]")
            if not isinstance(technologies, list) or not all(
                isinstance(item, str) for item in technologies
            ):
                raise ValueError(f"Array de tecnologías inválido para Gis_Codigo={code!r}.")
            technologies_by_code[code] = technologies
            classifications_by_code[code] = row.get(
                "clasificacion_asumida", "SIN_DATOS"
            )
            frequencies = []
            for band in bands:
                metadata = band_numbers_by_frequency.get(band, {})
                labels = [
                    f"{technology} {number}"
                    for technology in ("LTE", "NR")
                    for number in metadata.get(technology, [])
                ]
                frequencies.append((operator, "", band, ", ".join(labels)))
        elif row.get("tipo_registro") == "caracteristica_tecnica":
            band = row.get("banda_asignada_MHz", "").strip()
            frequencies = [(operator, row.get("referencia", "").strip(), band, "")] if band else []
        else:
            frequencies = []
        for frequency in frequencies:
            if frequency not in details_by_code[code]:
                details_by_code[code].append(frequency)

    locations = defaultdict(lambda: {"stations": {}, "addresses": set()})
    for station in sites:
        code = station.get("Gis_Codigo", "").strip()
        try:
            latitude = float(station["latitude"])
            longitude = float(station["longitude"])
        except (KeyError, ValueError) as error:
            raise ValueError(f"Coordenadas inválidas para Gis_Codigo={code!r}.") from error
        if not code:
            raise ValueError("Hay una estación sin Gis_Codigo en cellular_sites.csv.")

        location = locations[(latitude, longitude)]
        location["stations"][code] = {
            "code": code,
            "operator": operators_by_code.get(code, ""),
            "site_id": site_ids_by_code.get(code, ""),
            "bands": band_numbers_by_code.get(code, []),
            "frequencies": details_by_code.get(code, []),
            "technologies": technologies_by_code.get(code, []),
            "classification": classifications_by_code.get(code, "SIN_DATOS"),
        }
        address = station.get("Dirección", "").strip()
        if address:
            location["addresses"].add(address)

    return locations


def escape(value):
    return html.escape(str(value), quote=True)


def make_popup(coordinate, location):
    stations = sorted(
        location["stations"].values(),
        key=lambda station: station["code"].casefold(),
    )
    code_count = len(stations)
    parts = [
        f"<div><b>Emplazamiento compartido</b> ({code_count} "
        f"{'operador/registro' if code_count == 1 else 'operadores/registros'})</div>"
    ]
    addresses = sorted(location["addresses"])
    if addresses:
        parts.append(f"<div><b>Dirección:</b> {escape('; '.join(addresses))}</div>")
    parts.append("<hr style='margin:6px 0'>")

    for station in stations:
        station_label = station["operator"] or station["code"]
        if station["site_id"] and station["site_id"] != station["code"]:
            station_label = f"{station_label} — {station['site_id']}"
        parts.append(f"<div><b>{escape(station_label)}</b></div>")
        parts.append(
            "<div><b>Clasificación asumida:</b> "
            f"{escape(station['classification'])}</div>"
        )
        if station["frequencies"]:
            parts.append("<ul style='margin:2px 0 8px;padding-left:20px'>")
            for operator, reference, band, band_numbers in sorted(set(station["frequencies"])):
                description = " — ".join(
                    part for part in (
                        operator,
                        reference,
                        f"{band} MHz",
                        band_numbers,
                    ) if part
                )
                parts.append(f"<li>{escape(description)}</li>")
            parts.append("</ul>")
            if station["technologies"]:
                parts.append(
                    "<small>Tecnologías compatibles por frecuencia (no confirma "
                    f"despliegue activo): {escape(', '.join(station['technologies']))}</small>"
                )
        else:
            parts.append("<div>Sin datos de bandas.</div>")

    parts.append(
        f"<small>Coordenadas: {coordinate[0]:.6f}, {coordinate[1]:.6f}</small>"
    )
    return "".join(parts)


def eligible_coordinates(locations):
    return sorted(
        coordinate
        for coordinate, location in locations.items()
        if any(
            station["classification"] in {"4G", "5G"}
            for station in location["stations"].values()
        )
    )


def create_sample_csv(locations, coordinates, output_path):
    columns = [
        "Site ID",
        "coordinates",
        "operator 1",
        "operator 2",
        "operator 3",
        "operator 4",
        "technology_op1",
        "technology_op2",
        "technology_op3",
        "technology_op4",
        "frequency bands (band number)",
    ]
    rows = []
    technology_order = {"2G/3G": 0, "4G": 1, "5G": 2}
    for latitude, longitude in coordinates:
        location = locations[(latitude, longitude)]
        site_ids = set()
        operators = defaultdict(set)
        frequency_bands = set()
        for station in location["stations"].values():
            if station["site_id"]:
                site_ids.add(station["site_id"])
            if station["operator"]:
                operators[station["operator"]].add(station["classification"])
            frequency_bands.update(station["bands"])

        ordered_operators = sorted(
            operators,
            key=lambda operator: (
                -max(
                    (technology_order.get(value, -1) for value in operators[operator]),
                    default=-1,
                ),
                operator.casefold(),
            ),
        )
        if len(ordered_operators) > 4:
            raise ValueError(
                f"Hay {len(ordered_operators)} operadores en "
                f"{latitude},{longitude}; el CSV admite hasta cuatro."
            )
        row = {
            "Site ID": json.dumps(sorted(site_ids), ensure_ascii=False),
            "coordinates": f"{latitude:.6f}, {longitude:.6f}",
            "frequency bands (band number)": json.dumps(
                sorted(frequency_bands), ensure_ascii=False
            ),
        }
        for index in range(1, 5):
            if index <= len(ordered_operators):
                operator = ordered_operators[index - 1]
                row[f"operator {index}"] = operator
                row[f"technology_op{index}"] = max(
                    operators[operator],
                    key=lambda value: technology_order.get(value, -1),
                )
            else:
                row[f"operator {index}"] = ""
                row[f"technology_op{index}"] = ""
        rows.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def create_map(locations, count, seed, output_path):
    coordinates = eligible_coordinates(locations)
    if count > len(coordinates):
        raise ValueError(
            f"Se solicitaron {count} ubicaciones, pero solo hay {len(coordinates)} "
            "ubicaciones con celdas clasificadas como 4G o 5G."
        )
    selected = random.Random(seed).sample(coordinates, count)
    create_sample_csv(locations, selected, SAMPLE_CSV)
    center = (
        sum(latitude for latitude, _ in selected) / count,
        sum(longitude for _, longitude in selected) / count,
    )
    markers = [
        {
            "latitude": latitude,
            "longitude": longitude,
            "popup": make_popup((latitude, longitude), locations[(latitude, longitude)]),
            "count": len(locations[(latitude, longitude)]["stations"]),
        }
        for latitude, longitude in selected
    ]
    marker_data = json.dumps(markers, ensure_ascii=False).replace("</", "<\\/")
    page = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mapa de estaciones celulares</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    #tile-status {{
      display: none;
      position: absolute;
      z-index: 1000;
      top: 10px;
      left: 50px;
      max-width: 360px;
      padding: 8px 12px;
      border-radius: 4px;
      background: white;
      box-shadow: 0 1px 5px #666;
      font: 14px/1.4 Arial, sans-serif;
    }}
  </style>
</head>
<body>
  <div id="map" aria-label="Mapa interactivo de estaciones celulares"></div>
  <div id="tile-status">Cargando el mapa base...</div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const center = {json.dumps(center)};
    const stations = {marker_data};
    const map = L.map("map", {{ scrollWheelZoom: true }});
    const status = document.getElementById("tile-status");
    const osm = L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors"
    }});
    const carto = L.tileLayer(
      "https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png",
      {{
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
      }}
    );
    let baseMapLoaded = false;
    let fallbackSelected = false;
    const showMapStatus = (message) => {{
      status.textContent = message;
      status.style.display = "block";
    }};
    const hideMapStatus = () => {{
      status.style.display = "none";
    }};
    osm.on("tileload", () => {{
      baseMapLoaded = true;
      if (!fallbackSelected) hideMapStatus();
    }});
    carto.on("tileload", () => {{
      baseMapLoaded = true;
      hideMapStatus();
    }});
    osm.addTo(map);
    L.control.layers(
      {{ "OpenStreetMap": osm, "Carto claro": carto }},
      null,
      {{ position: "topright" }}
    ).addTo(map);
    showMapStatus("Cargando el mapa base...");
    window.setTimeout(() => {{
      if (!baseMapLoaded) {{
        fallbackSelected = true;
        if (map.hasLayer(osm)) map.removeLayer(osm);
        carto.addTo(map);
        showMapStatus("Probando un proveedor alternativo para el mapa base...");
        window.setTimeout(() => {{
          if (!baseMapLoaded) {{
            showMapStatus("No se pudo cargar el mapa base. Comprueba la conexión a Internet o cambia la capa arriba a la derecha.");
          }}
        }}, 8000);
      }}
    }}, 5000);
    const markers = stations.map((station) => {{
      const marker = L.circleMarker([station.latitude, station.longitude], {{
        radius: 6,
        color: "#145da0",
        weight: 1,
        fillColor: "#2e86de",
        fillOpacity: 0.8
      }});
      marker.bindTooltip(`${{station.count}} registro(s) en este emplazamiento`);
      marker.bindPopup(station.popup, {{ maxWidth: 480 }});
      return marker.addTo(map);
    }});
    const bounds = L.featureGroup(markers).getBounds();
    if (bounds.isValid()) {{
      map.fitBounds(bounds, {{ padding: [24, 24] }});
    }} else {{
      map.setView(center, 10);
    }}
  </script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(page, encoding="utf-8")
    return len(selected)


def main():
    parser = argparse.ArgumentParser(
        description="Crea un mapa interactivo de emplazamientos celulares y sus bandas."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=350,
        help="Número de ubicaciones únicas a seleccionar (por defecto: 350).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Semilla para que la muestra aleatoria sea reproducible (por defecto: 42).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_HTML,
        help="Ruta del mapa HTML de salida.",
    )
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count debe ser al menos 1.")

    sites = read_csv(SITES_CSV)
    details = read_csv(DETAILS_CSV)
    locations = group_stations(sites, details)
    selected_count = create_map(locations, args.count, args.seed, args.output)
    eligible_count = len(eligible_coordinates(locations))
    print(
        f"Mapa creado: {args.output.resolve()} "
        f"({selected_count} ubicaciones 4G/5G seleccionadas aleatoriamente "
        f"de {eligible_count} elegibles; se cargaron {len(locations)} ubicaciones en total). "
        f"Dataset creado: {SAMPLE_CSV.resolve()}."
    )


if __name__ == "__main__":
    main()
