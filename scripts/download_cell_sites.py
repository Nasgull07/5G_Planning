import csv
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


BASE_URL = "https://geoportal.minetur.gob.es/VCTEL/infoantenasGeoJSON.do"
ZOOM = 7
PROJECT_DIR = Path(__file__).resolve().parent.parent
KML_FILE = PROJECT_DIR / "data" / "input" / "working_area_900km2.kml"
CSV_FILE = PROJECT_DIR / "data" / "processed" / "cellular_sites.csv"

# Approximate width and height of the example bbox at zoom 7.
TILE_WIDTH = 1.9830280064774 - 1.9068961857986
TILE_HEIGHT = 41.47127122711 - 41.414107988584
REQUEST_DELAY_SECONDS = 0.2
MAX_ATTEMPTS = 3


def parse_coordinate_ring(text):
    points = []
    for coordinate in text.split():
        parts = coordinate.split(",")
        if len(parts) >= 2:
            points.append((float(parts[0]), float(parts[1])))
    if len(points) < 3:
        raise ValueError("El KML contiene un anillo con menos de tres coordenadas.")
    return points


def load_polygons(path):
    root = ET.parse(path).getroot()
    polygons = []
    for polygon in root.findall(".//{*}Polygon"):
        outer = polygon.find("./{*}outerBoundaryIs/{*}LinearRing/{*}coordinates")
        if outer is None or not outer.text:
            continue
        outer_ring = parse_coordinate_ring(outer.text)
        holes = []
        for inner in polygon.findall("./{*}innerBoundaryIs/{*}LinearRing/{*}coordinates"):
            if inner.text:
                holes.append(parse_coordinate_ring(inner.text))
        polygons.append((outer_ring, holes))
    if not polygons:
        raise ValueError(f"No se encontraron polígonos en {path}.")
    return polygons


def point_in_ring(point, ring):
    x, y = point
    inside = False
    for index, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(index + 1) % len(ring)]
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if abs(cross) < 1e-10 and min(x1, x2) - 1e-10 <= x <= max(x1, x2) + 1e-10 and min(y1, y2) - 1e-10 <= y <= max(y1, y2) + 1e-10:
            return True
        if (y1 > y) != (y2 > y):
            crossing_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing_x:
                inside = not inside
    return inside


def point_in_polygons(point, polygons):
    for outer, holes in polygons:
        if point_in_ring(point, outer) and not any(point_in_ring(point, hole) for hole in holes):
            return True
    return False


def make_tiles(polygons):
    all_points = [point for outer, holes in polygons for ring in [outer, *holes] for point in ring]
    min_lon = min(point[0] for point in all_points)
    max_lon = max(point[0] for point in all_points)
    min_lat = min(point[1] for point in all_points)
    max_lat = max(point[1] for point in all_points)
    columns = max(1, math.ceil((max_lon - min_lon) / TILE_WIDTH))
    rows = max(1, math.ceil((max_lat - min_lat) / TILE_HEIGHT))
    tiles = []
    for row in range(rows):
        south = min_lat + (max_lat - min_lat) * row / rows
        north = min_lat + (max_lat - min_lat) * (row + 1) / rows
        for column in range(columns):
            west = min_lon + (max_lon - min_lon) * column / columns
            east = min_lon + (max_lon - min_lon) * (column + 1) / columns
            tiles.append((west, south, east, north))
    return tiles


def fetch_geojson(bbox):
    params = urllib.parse.urlencode({
        "idCapa": "null",
        "bbox": ",".join(format(value, ".12f").rstrip("0").rstrip(".") for value in bbox),
        "zoom": ZOOM,
    })
    request = urllib.request.Request(
        f"{BASE_URL}?{params}",
        headers={"User-Agent": "cellular-site-export/1.0", "Accept": "application/json"},
    )
    for attempt in range(MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                payload = response.read().decode(charset)
            data = json.loads(payload)
            if data == {}:
                return []
            if not isinstance(data, dict) or not isinstance(data.get("features"), list):
                raise ValueError(
                    f"Respuesta no válida para bbox={bbox}: {payload[:200]!r}"
                )
            return data["features"]
        except (urllib.error.URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as error:
            if attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError(f"Falló la consulta bbox={bbox}: {error}") from error
            time.sleep(2 ** attempt)


def feature_key(feature, properties):
    return (
        feature.get("id")
        or properties.get("Gis_ID")
        or properties.get("Gis_Codigo")
        or json.dumps(feature, sort_keys=True, ensure_ascii=False)
    )


def main():
    polygons = load_polygons(KML_FILE)
    tiles = make_tiles(polygons)
    unique_features = {}

    for index, bbox in enumerate(tiles, start=1):
        print(f"Consulta {index}/{len(tiles)} (zoom {ZOOM})...")
        for feature in fetch_geojson(bbox):
            geometry = feature.get("geometry") or {}
            coordinates = geometry.get("coordinates")
            if geometry.get("type") != "Point" or not isinstance(coordinates, list) or len(coordinates) < 2:
                continue
            longitude, latitude = coordinates[:2]
            if not point_in_polygons((longitude, latitude), polygons):
                continue
            properties = feature.get("properties") or {}
            unique_features.setdefault(feature_key(feature, properties), (longitude, latitude, properties))
        if index < len(tiles):
            time.sleep(REQUEST_DELAY_SECONDS)

    property_names = sorted({
        name
        for _, _, properties in unique_features.values()
        for name in properties
    })
    columns = ["feature_id", "latitude", "longitude", *property_names]
    CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CSV_FILE.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for identifier, (longitude, latitude, properties) in unique_features.items():
            row = {
                "feature_id": identifier,
                "latitude": latitude,
                "longitude": longitude,
            }
            row.update({
                name: value if isinstance(value, (str, int, float, bool)) or value is None
                else json.dumps(value, ensure_ascii=False)
                for name, value in properties.items()
            })
            writer.writerow(row)

    print(f"CSV creado: {CSV_FILE} ({len(unique_features)} estaciones).")


if __name__ == "__main__":
    main()
