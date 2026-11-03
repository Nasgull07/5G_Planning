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
OUTPUT_HTML = PROJECT_DIR / "output" / "cellular_sites_map.html"


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def group_stations(sites, details):
    details_by_code = defaultdict(list)
    for row in details:
        if row.get("tipo_registro") != "caracteristica_tecnica":
            continue
        code = row.get("Gis_Codigo", "").strip()
        band = row.get("banda_asignada_MHz", "").strip()
        if code and band:
            frequency = (
                row.get("operador", "").strip(),
                row.get("referencia", "").strip(),
                band,
            )
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
            "frequencies": details_by_code.get(code, []),
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
        parts.append(f"<div><b>{escape(station['code'])}</b></div>")
        if station["frequencies"]:
            parts.append("<ul style='margin:2px 0 8px;padding-left:20px'>")
            for operator, reference, band in sorted(set(station["frequencies"])):
                description = " — ".join(
                    part for part in (operator, reference, f"{band} MHz") if part
                )
                parts.append(f"<li>{escape(description)}</li>")
            parts.append("</ul>")
        else:
            parts.append("<div>Sin datos de frecuencias.</div>")

    parts.append(
        f"<small>Coordenadas: {coordinate[0]:.6f}, {coordinate[1]:.6f}</small>"
    )
    return "".join(parts)


def create_map(locations, count, seed, output_path):
    coordinates = sorted(locations)
    if count > len(coordinates):
        raise ValueError(
            f"Se solicitaron {count} ubicaciones, pero solo hay {len(coordinates)} "
            "coordenadas únicas."
        )
    selected = random.Random(seed).sample(coordinates, count)
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
        description="Crea un mapa interactivo de emplazamientos celulares y sus frecuencias."
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
    print(
        f"Mapa creado: {args.output.resolve()} "
        f"({selected_count} ubicaciones únicas de {len(locations)})."
    )


if __name__ == "__main__":
    main()
