import argparse
import csv
import html
import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "processed"
SCENARIO_CSV = DATA_DIR / "scenario_sites.csv"
KML_FILE = PROJECT_DIR / "data" / "input" / "selected_area.kml"
OUTPUT_HTML = PROJECT_DIR / "output" / "cellular_sites_map.html"


def escape(value):
    return html.escape(str(value), quote=True)


def read_scenario_sites(path):
    if not path.exists():
        raise FileNotFoundError(
            f"No se encontró {path}. Ejecuta primero create_scenario_sites.py."
        )

    required = {
        "rrh_id", "latitude", "longitude", "source_type",
        "operator", "technology", "is_synthetic",
    }
    real_markers, synthetic_markers = [], []
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(
                f"{path.name} debe contener estas columnas: "
                f"{', '.join(sorted(required))}."
            )
        for index, row in enumerate(reader, start=2):
            try:
                latitude = float(row["latitude"])
                longitude = float(row["longitude"])
            except (TypeError, ValueError):
                raise ValueError(
                    f"Coordenadas inválidas en {path.name}, fila {index}."
                ) from None

            synthetic_flag = row["is_synthetic"].strip().casefold()
            if synthetic_flag not in {"true", "false"}:
                raise ValueError(
                    f"is_synthetic inválido en {path.name}, fila {index}."
                )
            site = {
                "latitude": latitude,
                "longitude": longitude,
                "rrh_id": row["rrh_id"].strip(),
                "source_type": row["source_type"].strip(),
                "operator": row["operator"].strip(),
                "technology": row["technology"].strip(),
            }
            if synthetic_flag == "true":
                synthetic_markers.append(site)
            else:
                coordinate = f"{latitude:.6f}, {longitude:.6f}"
                site["popup"] = (
                    "<div><b>Real 4G/5G site selected for scenario</b></div>"
                    f"<div><b>Site ID:</b> {escape(site['rrh_id'])}</div>"
                    f"<div><b>Operator:</b> {escape(site['operator'])}</div>"
                    f"<div><b>Technology:</b> {escape(site['technology'])}</div>"
                    f"<div><b>Coordinates:</b> {coordinate}</div>"
                )
                real_markers.append(site)

    return real_markers, synthetic_markers


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


def create_map(real_markers, synthetic_markers, output_path):
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
      marker.bindTooltip(site.rrh_id);
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
    overlays["Real 350-cell sites (" + realSites.length + ")"] = realLayer;
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
        '<span class="legend-dot" style="background:#2e86de"></span>Real 350-cell sites<br>' +
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
    return len(real_markers), len(synthetic_markers)


def main():
    parser = argparse.ArgumentParser(
        description="Crea un mapa interactivo a partir de scenario_sites.csv."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_HTML,
        help="Ruta del mapa HTML de salida.",
    )
    args = parser.parse_args()
    real_markers, synthetic_markers = read_scenario_sites(SCENARIO_CSV)
    real_count, synthetic_count = create_map(
        real_markers, synthetic_markers, args.output
    )
    print(f"Interactive map created: {args.output.resolve()}")
    print(f"Real 350-cell locations displayed: {real_count}")
    print(f"Synthetic RRHs displayed: {synthetic_count}")
    print(f"Scenario source: {SCENARIO_CSV.resolve()}")
    if KML_FILE.exists():
        print(f"KML study boundary loaded: {KML_FILE}")
    else:
        print(f"Warning: KML boundary not found: {KML_FILE}")


if __name__ == "__main__":
    main()
