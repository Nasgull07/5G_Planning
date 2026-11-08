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
KML_FILE = PROJECT_DIR / "data" / "input" / "selected_area.kml"
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



def read_synthetic_sites(path):
    """Read synthetic RRHs generated by add_synthetic_rrhs.py."""
    if not path.exists():
        return []
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {"latitude", "longitude"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(
                f"{path.name} debe contener las columnas latitude y longitude."
            )
        for index, row in enumerate(reader, start=1):
            try:
                latitude = float(row["latitude"])
                longitude = float(row["longitude"])
            except (TypeError, ValueError):
                raise ValueError(
                    f"Coordenadas sintéticas inválidas en la fila {index + 1}."
                )
            rows.append({
                "rrh_id": row.get("rrh_id") or f"SYN_RRH_{index:05d}",
                "latitude": latitude,
                "longitude": longitude,
                "source_type": row.get("source_type") or "SYNTHETIC",
                "operator": row.get("operator") or "SYNTHETIC",
                "technology": row.get("technology") or "ASSUMED_FOR_SIMULATION",
            })
    return rows


def read_kml_geometry(path):
    """
    Read Polygon geometries from the KML and return GeoJSON-compatible
    polygon coordinates. Supports inner rings (holes) and multiple polygons.
    """
    import xml.etree.ElementTree as ET

    if not path.exists():
        return None

    root = ET.parse(path).getroot()
    polygons = []

    def parse_ring(coordinates_text):
        points = []
        for token in coordinates_text.split():
            values = token.split(",")
            if len(values) >= 2:
                # GeoJSON order is [longitude, latitude].
                points.append([float(values[0]), float(values[1])])
        return points

    for polygon in root.findall(".//{*}Polygon"):
        outer_node = polygon.find(
            "./{*}outerBoundaryIs/{*}LinearRing/{*}coordinates"
        )
        if outer_node is None or not outer_node.text:
            continue
        outer = parse_ring(outer_node.text)
        if len(outer) < 4:
            continue
        rings = [outer]
        for inner_node in polygon.findall(
            "./{*}innerBoundaryIs/{*}LinearRing/{*}coordinates"
        ):
            if inner_node.text:
                inner = parse_ring(inner_node.text)
                if len(inner) >= 4:
                    rings.append(inner)
        polygons.append(rings)

    if not polygons:
        return None

    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def create_map(locations, synthetic_rows, count, seed, output_path):
    # Keep the team's original random 4G/5G sample export.
    eligible = eligible_coordinates(locations)
    if count > len(eligible):
        raise ValueError(
            f"Se solicitaron {count} ubicaciones para 350_CELLS.csv, pero solo "
            f"hay {len(eligible)} ubicaciones clasificadas como 4G o 5G."
        )
    selected_sample = random.Random(seed).sample(eligible, count)
    create_sample_csv(locations, selected_sample, SAMPLE_CSV)

    # Map all real Infoantenas coordinate locations, not only the 350-row sample.
    real_markers = []
    for latitude, longitude in sorted(locations):
        location = locations[(latitude, longitude)]
        real_markers.append({
            "latitude": latitude,
            "longitude": longitude,
            "popup": make_popup((latitude, longitude), location),
            "count": len(location["stations"]),
        })

    synthetic_markers = []
    for row in synthetic_rows:
        synthetic_markers.append({
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "rrh_id": row["rrh_id"],
            "source_type": row["source_type"],
            "operator": row["operator"],
            "technology": row["technology"],
        })

    all_coordinates = (
        [(m["latitude"], m["longitude"]) for m in real_markers]
        + [(m["latitude"], m["longitude"]) for m in synthetic_markers]
    )
    if all_coordinates:
        center = (
            sum(lat for lat, _ in all_coordinates) / len(all_coordinates),
            sum(lon for _, lon in all_coordinates) / len(all_coordinates),
        )
    else:
        center = (41.3851, 2.1734)

    marker_data = json.dumps(real_markers, ensure_ascii=False).replace("</", "<\\/")
    synthetic_data = json.dumps(synthetic_markers, ensure_ascii=False).replace("</", "<\\/")
    boundary = read_kml_geometry(KML_FILE)
    boundary_data = json.dumps(boundary, ensure_ascii=False) if boundary else "null"

    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Barcelona C-RAN: Real and Synthetic RRHs</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    .map-legend {{
      background: white; padding: 10px 12px; line-height: 1.7;
      border-radius: 5px; box-shadow: 0 1px 5px #777;
      font: 13px/1.7 Arial, sans-serif;
    }}
    .legend-dot {{
      display: inline-block; width: 11px; height: 11px;
      border-radius: 50%; margin-right: 7px;
    }}
    #tile-status {{
      display: none; position: absolute; z-index: 1000; top: 10px;
      left: 50px; max-width: 360px; padding: 8px 12px;
      border-radius: 4px; background: white; box-shadow: 0 1px 5px #666;
      font: 14px/1.4 Arial, sans-serif;
    }}
  </style>
</head>
<body>
  <div id="map" aria-label="Interactive map of real and synthetic cellular sites"></div>
  <div id="tile-status">Loading map tiles...</div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const center = {json.dumps(center)};
    const realSites = {marker_data};
    const syntheticSites = {synthetic_data};
    const studyBoundary = {boundary_data};

    const map = L.map("map", {{ scrollWheelZoom: true }});
    const status = document.getElementById("tile-status");
    // Esri World Street Map basemap. This avoids the blocked OSM
    // standard tile endpoint and CARTO tiles that requested an API key.
    const esriStreets = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{{z}}/{{y}}/{{x}}",
      {{
        maxZoom: 19,
        attribution: "Tiles &copy; Esri, HERE, Garmin, USGS, Intermap, increment P Corp., and the GIS user community"
      }}
    );
    const showMapStatus = (message) => {{
      status.textContent = message; status.style.display = "block";
    }};
    const hideMapStatus = () => {{ status.style.display = "none"; }};
    esriStreets.on("tileload", hideMapStatus);
    esriStreets.on("tileerror", () => {{
      showMapStatus("Esri basemap tiles could not load. Check your Internet connection.");
    }});
    esriStreets.addTo(map);
    L.control.layers({{ "Esri World Street Map": esriStreets }}, null,
      {{ position: "topright" }}).addTo(map);
    showMapStatus("Loading Esri map tiles...");

    const realLayer = L.layerGroup();
    realSites.forEach((site) => {{
      const marker = L.circleMarker([site.latitude, site.longitude], {{
        radius: 5, color: "#145da0", weight: 1,
        fillColor: "#2e86de", fillOpacity: 0.78
      }});
      marker.bindTooltip(`${{site.count}} real station record(s) at this location`);
      marker.bindPopup(site.popup, {{ maxWidth: 480 }});
      marker.addTo(realLayer);
    }});

    const syntheticLayer = L.layerGroup();
    syntheticSites.forEach((site) => {{
      const popup = `
        <div><b>Synthetic RRH</b></div>
        <div><b>ID:</b> ${{site.rrh_id}}</div>
        <div><b>Source:</b> ${{site.source_type}}</div>
        <div><b>Technology:</b> ${{site.technology}}</div>
        <div><b>Coordinates:</b> ${{site.latitude.toFixed(6)}}, ${{site.longitude.toFixed(6)}}</div>
        <div><i>Simulation-only location; not a real deployed antenna.</i></div>`;
      const marker = L.circleMarker([site.latitude, site.longitude], {{
        radius: 7, color: "#a85b00", weight: 1.5,
        fillColor: "#f59e0b", fillOpacity: 0.9
      }});
      marker.bindTooltip(site.rrh_id);
      marker.bindPopup(popup);
      marker.addTo(syntheticLayer);
    }});

    const overlays = {{}};
    overlays["Real Infoantenas sites (" + realSites.length + ")"] = realLayer;
    overlays["Synthetic RRHs (" + syntheticSites.length + ")"] = syntheticLayer;
    realLayer.addTo(map);
    syntheticLayer.addTo(map);
    L.control.layers(null, overlays, {{ collapsed: false }}).addTo(map);

    if (studyBoundary) {{
      const boundaryLayer = L.geoJSON(studyBoundary, {{
        style: {{ color: "#e0a000", weight: 3, fillColor: "#facc15", fillOpacity: 0.07 }}
      }}).addTo(map);
      L.control.layers(null, {{ "KML study boundary": boundaryLayer }},
        {{ collapsed: false }}).addTo(map);
    }}

    // Calculate map bounds from actual site coordinates.
    // Do not pass LayerGroup objects to FeatureGroup.getBounds().
    const allLatLngs = realSites.concat(syntheticSites).map(
      site => L.latLng(site.latitude, site.longitude)
    );
    if (allLatLngs.length > 0) {{
      map.fitBounds(L.latLngBounds(allLatLngs), {{ padding: [24, 24] }});
    }} else {{
      map.setView(center, 10);
    }}

    const legend = L.control({{ position: "bottomright" }});
    legend.onAdd = function() {{
      const div = L.DomUtil.create("div", "map-legend");
      div.innerHTML =
        '<b>Map legend</b><br>' +
        '<span class="legend-dot" style="background:#2e86de"></span>Real Infoantenas sites<br>' +
        '<span class="legend-dot" style="background:#f59e0b"></span>Synthetic RRHs<br>' +
        '<span style="color:#e0a000;font-weight:bold">━━</span> KML study boundary';
      return div;
    }};
    legend.addTo(map);
  </script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(page, encoding="utf-8")
    return len(real_markers), len(synthetic_markers), len(selected_sample)


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
    synthetic_rows = read_synthetic_sites(DATA_DIR / "synthetic_rrhs.csv")
    real_count, synthetic_count, sample_count = create_map(
        locations, synthetic_rows, args.count, args.seed, args.output
    )
    eligible_count = len(eligible_coordinates(locations))
    print(f"Interactive map created: {args.output.resolve()}")
    print(f"Real Infoantenas locations displayed: {real_count}")
    print(f"Synthetic RRHs displayed: {synthetic_count}")
    print(
        f"Original 4G/5G sample exported: {sample_count} of "
        f"{eligible_count} eligible locations to {SAMPLE_CSV.resolve()}."
    )
    if KML_FILE.exists():
        print(f"KML study boundary loaded: {KML_FILE}")
    else:
        print(f"Warning: KML boundary not found: {KML_FILE}")


if __name__ == "__main__":
    main()
