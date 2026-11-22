import argparse
import csv
from pathlib import Path

import geopandas as gpd
import osmium
import pandas as pd
from shapely.geometry import LineString, Point, Polygon


BASE_DIR = Path(__file__).resolve().parents[2]
OSM_PBF = (
    BASE_DIR / "data" / "input" / "osm"
    / "cataluna-latest.osm.pbf"
)
BOUNDARY_FILE = (
    BASE_DIR / "data" / "input"
    / "working_area_900km2.kml"
)
DATA_DIR = BASE_DIR / "data" / "processed"
RAW_FEATURES_FILE = DATA_DIR / "osm_business_features.csv"
STUDY_AREA_FEATURES_FILE = (
    DATA_DIR / "osm_business_features_study_area.csv"
)
FEATURE_FIELDS = [
    "osm_type",
    "osm_id",
    "category",
    "latitude",
    "longitude",
    "geometry_type",
    "office",
    "building",
    "landuse",
    "shop"
]


class BusinessHandler(osmium.SimpleHandler):

    def __init__(self):
        super().__init__()
        self.features = []
        self.seen = set()

    def save_feature(self, obj, point, geometry_type):
        key = (obj.type_str(), obj.id)
        if key in self.seen:
            return
        self.seen.add(key)

        tags = dict(obj.tags)
        office = tags.get("office", "")
        building = tags.get("building", "")
        landuse = tags.get("landuse", "")
        shop = tags.get("shop", "")

        if office or building == "office":
            category = "office"
        elif landuse == "commercial":
            category = "commercial"
        elif landuse == "retail":
            category = "retail"
        elif shop:
            category = "shop"
        else:
            return

        self.features.append({
            "osm_type": obj.type_str(),
            "osm_id": obj.id,
            "category": category,
            "latitude": point.y,
            "longitude": point.x,
            "geometry_type": geometry_type,
            "office": office,
            "building": building,
            "landuse": landuse,
            "shop": shop
        })

    def node(self, node):
        if not node.location.valid():
            return
        if "office" not in node.tags and "shop" not in node.tags:
            return

        point = Point(node.location.lon, node.location.lat)
        self.save_feature(node, point, "node")

    def way(self, way):
        tags = way.tags
        if not (
            "office" in tags
            or tags.get("building") == "office"
            or tags.get("landuse") in ["commercial", "retail"]
            or "shop" in tags
        ):
            return

        coordinates = []
        for node in way.nodes:
            if not node.location.valid():
                return
            coordinates.append((node.location.lon, node.location.lat))

        if len(coordinates) < 2:
            return

        try:
            if len(coordinates) >= 4 and coordinates[0] == coordinates[-1]:
                polygon = Polygon(coordinates)
                if not polygon.is_valid:
                    polygon = polygon.buffer(0)
                if polygon.is_empty:
                    return
                point = polygon.representative_point()
                geometry_type = "polygon"
            else:
                line = LineString(coordinates)
                point = line.interpolate(0.5, normalized=True)
                geometry_type = "line"
        except Exception:
            return

        self.save_feature(way, point, geometry_type)


def extract_business_features():
    if not OSM_PBF.exists():
        raise FileNotFoundError(OSM_PBF)

    handler = BusinessHandler()
    handler.apply_file(str(OSM_PBF), locations=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with RAW_FEATURES_FILE.open(
        "w", newline="", encoding="utf-8"
    ) as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=FEATURE_FIELDS)
        writer.writeheader()
        writer.writerows(handler.features)

    print("Extracted OSM features:", len(handler.features))
    print("Saved:", RAW_FEATURES_FILE)


def filter_features_to_study_area():
    if not RAW_FEATURES_FILE.exists():
        raise FileNotFoundError(RAW_FEATURES_FILE)
    if not BOUNDARY_FILE.exists():
        raise FileNotFoundError(BOUNDARY_FILE)

    study_area = gpd.read_file(BOUNDARY_FILE)
    if study_area.crs is None:
        raise ValueError("Study-area KML has no CRS defined.")
    boundary = study_area.to_crs("EPSG:25831").geometry.union_all()

    features = pd.read_csv(RAW_FEATURES_FILE)
    points = gpd.GeoDataFrame(
        features,
        geometry=gpd.points_from_xy(
            features["longitude"],
            features["latitude"]
        ),
        crs="EPSG:4326"
    ).to_crs("EPSG:25831")

    filtered = points[
        points.geometry.intersects(boundary)
    ].copy()
    filtered["x_25831"] = filtered.geometry.x
    filtered["y_25831"] = filtered.geometry.y
    filtered = pd.DataFrame(
        filtered.drop(columns="geometry")
    )
    filtered.to_csv(STUDY_AREA_FEATURES_FILE, index=False)

    print("Features before filtering:", len(features))
    print("Features inside study area:", len(filtered))
    print(
        filtered["category"].value_counts().to_string()
    )
    print("Saved:", STUDY_AREA_FEATURES_FILE)


def main():
    parser = argparse.ArgumentParser(
        description="Extract OSM businesses and filter to the study area."
    )
    parser.add_argument(
        "--filter-existing",
        action="store_true",
        help="Skip PBF extraction and filter the existing raw CSV."
    )
    args = parser.parse_args()

    if not args.filter_existing:
        extract_business_features()
    filter_features_to_study_area()


if __name__ == "__main__":
    main()
