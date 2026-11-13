
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union


# =====================================================
# 1. PROJECT PATHS AND SETTINGS
# =====================================================

PROJECT_DIR = Path(__file__).resolve().parent.parent

RRH_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "scenario_sites.csv"
)

KML_FILE = (
    PROJECT_DIR / "data" / "input"
    / "working_area_900km2.kml"
)

OUTPUT_DIR = PROJECT_DIR / "data" / "processed"

FEATURES_FILE = (
    OUTPUT_DIR / "osm_business_features.csv"
)

RRH_ACTIVITY_FILE = (
    OUTPUT_DIR / "rrh_business_activity.csv"
)

RADIUS_METRES = 500

OVERPASS_URL = (
    "https://overpass.kumi.systems/api/interpreter"
)

# Use a descriptive user agent for the public service.
USER_AGENT = "BarcelonaCRANResearch/1.0"


# =====================================================
# 2. READ STUDY AREA FROM KML
# =====================================================

def read_study_area():

    print("Reading study-area KML...")

    if not KML_FILE.exists():
        raise FileNotFoundError(
            f"KML file not found: {KML_FILE}"
        )

    root = ET.parse(KML_FILE).getroot()

    polygons = []

    for polygon_node in root.findall(".//{*}Polygon"):

        outer_node = polygon_node.find(
            "./{*}outerBoundaryIs/{*}LinearRing/{*}coordinates"
        )

        if outer_node is None or not outer_node.text:
            continue

        def parse_ring(node):

            coordinates = []

            for item in node.text.split():

                values = item.split(",")

                if len(values) >= 2:

                    lon = float(values[0])
                    lat = float(values[1])

                    coordinates.append((lon, lat))

            return coordinates

        outer = parse_ring(outer_node)

        if len(outer) < 4:
            continue

        holes = []

        for inner_node in polygon_node.findall(
            "./{*}innerBoundaryIs/{*}LinearRing/{*}coordinates"
        ):

            if inner_node.text:

                inner = parse_ring(inner_node)

                if len(inner) >= 4:
                    holes.append(inner)

        polygon = Polygon(outer, holes)

        if not polygon.is_valid:
            polygon = polygon.buffer(0)

        if not polygon.is_empty:
            polygons.append(polygon)

    if not polygons:
        raise ValueError(
            "No valid polygons were found in the KML."
        )

    study_area = unary_union(polygons)

    if study_area.is_empty:
        raise ValueError("Study-area geometry is empty.")

    return study_area


# =====================================================
# 3. LOAD RRH LOCATIONS
# =====================================================

def load_rrhs():

    print("Loading RRH locations...")

    if not RRH_FILE.exists():
        raise FileNotFoundError(
            f"RRH file not found: {RRH_FILE}"
        )

    df = pd.read_csv(RRH_FILE)

    df["latitude"] = pd.to_numeric(
        df["latitude"], errors="coerce"
    )

    df["longitude"] = pd.to_numeric(
        df["longitude"], errors="coerce"
    )

    df = df.dropna(
        subset=["latitude", "longitude"]
    ).copy()

    rrhs = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(
            df["longitude"],
            df["latitude"]
        ),
        crs="EPSG:4326"
    )

    return rrhs


# =====================================================
# 4. DOWNLOAD OPENSTREETMAP BUSINESS FEATURES
# =====================================================


def download_osm_features(study_area):

    print("Preparing OpenStreetMap query...")

    min_lon, min_lat, max_lon, max_lat = study_area.bounds

    # Divide the study area into a 3 x 3 grid.
    grid_size = 3

    lon_step = (max_lon - min_lon) / grid_size
    lat_step = (max_lat - min_lat) / grid_size

    all_elements = {}

    print("Study area divided into 9 smaller sections.")
    print("Contacting OpenStreetMap Overpass API...")

    for row in range(grid_size):

        for col in range(grid_size):

            south = min_lat + row * lat_step
            north = min_lat + (row + 1) * lat_step
            west = min_lon + col * lon_step
            east = min_lon + (col + 1) * lon_step

            bbox = f"{south},{west},{north},{east}"

            query = f"""
            [out:json][timeout:120];

            (
              nwr["office"]({bbox});
              nwr["building"="office"]({bbox});
              nwr["landuse"~"^(commercial|retail)$"]({bbox});
              nwr["shop"]({bbox});
            );

            out center tags;
            """

            data = urllib.parse.urlencode({
                "data": query
            }).encode("utf-8")

            request = urllib.request.Request(
                OVERPASS_URL,
                data=data,
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded"
                }
            )

            section_number = row * grid_size + col + 1

            print(
                f"Downloading section {section_number}/9..."
            )

            success = False

            for attempt in range(1, 4):

                try:

                    with urllib.request.urlopen(
                        request,
                        timeout=180
                    ) as response:

                        result = json.loads(
                            response.read().decode("utf-8")
                        )

                    elements = result.get("elements", [])

                    # Remove duplicate OSM features.
                    for element in elements:

                        key = (
                            element.get("type"),
                            element.get("id")
                        )

                        all_elements[key] = element

                    print(
                        f"Section {section_number}: "
                        f"{len(elements)} features returned."
                    )

                    success = True
                    break

                except Exception as error:

                    print(
                        f"Attempt {attempt}/3 failed: {error}"
                    )

                    if attempt < 3:
                        print("Waiting 15 seconds before retry...")
                        time.sleep(15)

            if not success:

                raise RuntimeError(
                    f"Could not download section {section_number}/9. "
                    "The Overpass server may be busy. "
                    "Please try again later."
                )

            # Give the public Overpass server time between requests.
            time.sleep(5)

    final_elements = list(all_elements.values())

    print("--------------------------------------")
    print(
        f"Total unique OSM features: {len(final_elements)}"
    )
    print("--------------------------------------")

    return final_elements



# =====================================================
# 5. CONVERT OSM RESULTS INTO GEOGRAPHIC FEATURES
# =====================================================

def process_osm_features(elements, study_area):

    print("Processing OSM features...")

    records = []
    seen_ids = set()

    for element in elements:

        osm_type = element.get("type")
        osm_id = element.get("id")

        unique_id = (osm_type, osm_id)

        if unique_id in seen_ids:
            continue

        seen_ids.add(unique_id)

        tags = element.get("tags", {})

        # Nodes have their own coordinates.
        # Ways and relations use their returned centre.
        if "lat" in element and "lon" in element:

            lat = element["lat"]
            lon = element["lon"]

        elif "center" in element:

            lat = element["center"]["lat"]
            lon = element["center"]["lon"]

        else:
            continue

        point = Point(lon, lat)

        # Exclude results outside the actual KML polygon.
        if not study_area.covers(point):
            continue

        office = (
            "office" in tags
            or tags.get("building") == "office"
        )

        commercial = (
            tags.get("landuse") == "commercial"
        )

        retail = (
            tags.get("landuse") == "retail"
        )

        shop = "shop" in tags

        if office:
            category = "Office"

        elif commercial:
            category = "Commercial"

        elif retail:
            category = "Retail"

        elif shop:
            category = "Shop"

        else:
            continue

        records.append({

            "osm_id": osm_id,
            "osm_type": osm_type,

            "latitude": lat,
            "longitude": lon,

            "category": category,

            "office": office,
            "commercial": commercial,
            "retail": retail,
            "shop": shop,

            "office_type": tags.get("office", ""),
            "building_type": tags.get("building", ""),
            "landuse": tags.get("landuse", ""),
            "shop_type": tags.get("shop", ""),

            "name": tags.get("name", "")

        })

    return records


# =====================================================
# 6. COUNT BUSINESS FEATURES AROUND EACH RRH
# =====================================================

def calculate_rrh_activity(rrhs, features):

    print("Calculating business activity around RRHs...")

    rrhs_projected = rrhs.to_crs("EPSG:25831")

    if features.empty:

        raise ValueError(
            "No business features were found inside the KML area."
        )

    features_projected = features.to_crs(
        "EPSG:25831"
    )

    spatial_index = features_projected.sindex

    results = []

    for _, rrh in rrhs_projected.iterrows():

        search_area = rrh.geometry.buffer(
            RADIUS_METRES
        )

        candidate_indices = spatial_index.query(
            search_area,
            predicate="intersects"
        )

        nearby = features_projected.iloc[
            candidate_indices
        ].copy()

        # Verify actual distance from RRH to feature point.
        nearby["distance_m"] = nearby.geometry.distance(
            rrh.geometry
        )

        nearby = nearby[
            nearby["distance_m"] <= RADIUS_METRES
        ]

        office_count = int(
            nearby["office"].sum()
        )

        commercial_count = int(
            nearby["commercial"].sum()
        )

        retail_count = int(
            nearby["retail"].sum()
        )

        shop_count = int(
            nearby["shop"].sum()
        )

        results.append({

            "rrh_id": rrh["rrh_id"],

            "is_synthetic": rrh.get(
                "is_synthetic", False
            ),

            "business_radius_m": RADIUS_METRES,

            "office_count": office_count,

            "commercial_area_feature_count": commercial_count,

            "retail_area_feature_count": retail_count,

            "shop_count": shop_count,

            "total_business_features": len(nearby)

        })

    return pd.DataFrame(results)


# =====================================================
# 7. MAIN PROGRAM
# =====================================================

def main():

    print("\n======================================")
    print("OPENSTREETMAP BUSINESS ACTIVITY ANALYSIS")
    print("======================================\n")

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    study_area = read_study_area()

    rrhs = load_rrhs()

    elements = download_osm_features(
        study_area
    )

    records = process_osm_features(
        elements,
        study_area
    )

    if not records:

        raise ValueError(
            "No qualifying OSM features were found "
            "inside the study area."
        )

    features = pd.DataFrame(records)

    # Save the collected feature dataset.
    features.to_csv(
        FEATURES_FILE,
        index=False
    )

    # Convert to GeoDataFrame.
    features_gdf = gpd.GeoDataFrame(
        features,
        geometry=gpd.points_from_xy(
            features["longitude"],
            features["latitude"]
        ),
        crs="EPSG:4326"
    )

    activity = calculate_rrh_activity(
        rrhs,
        features_gdf
    )

    activity.to_csv(
        RRH_ACTIVITY_FILE,
        index=False
    )

    # =================================================
    # SUMMARY
    # =================================================

    print("\nBUSINESS FEATURE SUMMARY")
    print("--------------------------------------")

    print(
        features["category"].value_counts()
    )

    print("\nRRH BUSINESS ACTIVITY SUMMARY")
    print("--------------------------------------")

    print(
        activity[
            [
                "office_count",
                "commercial_area_feature_count",
                "retail_area_feature_count",
                "shop_count",
                "total_business_features"
            ]
        ].describe().round(2)
    )

    print("\nOUTPUT FILES")
    print("--------------------------------------")

    print(FEATURES_FILE)

    print(RRH_ACTIVITY_FILE)

    print("\nBusiness activity analysis completed.")


if __name__ == "__main__":
    main()
