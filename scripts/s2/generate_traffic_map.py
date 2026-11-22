import argparse
from html import escape
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import folium
import geopandas as gpd
import pandas as pd
from branca.element import Element


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data" / "processed"
SCENARIO_FILE = DATA_DIR / "scenario_sites.csv"
OSM_FILE = DATA_DIR / "osm_business_features_study_area.csv"
BOUNDARY_FILE = (
    BASE_DIR / "data" / "input" / "working_area_900km2.kml"
)
AMB_LAYER_URL = (
    "https://ide.amb.cat/geoserveis/rest/services/"
    "indicadors_habitatge/MapServer/24/query"
)
OFFICE_RADIUS_METRES = 500
OFFICE_THRESHOLD = 5
METRIC_CRS = "EPSG:25831"
TRAFFIC_CATEGORIES = ["Residential", "Office", "Mixed"]
TRAFFIC_SHAPES = {
    "Residential": "circle",
    "Mixed": "triangle",
    "Office": "square"
}
CLASSIFICATION_DERIVED_COLUMNS = [
    "densresi21",
    "residential_density_group",
    "office_count_500m",
    "residential_evidence",
    "office_evidence",
    "traffic_category",
    "classification_basis"
]


def classify_density(value, low_threshold, high_threshold):
    if pd.isna(value):
        return "No AMB data"
    if value <= low_threshold:
        return "Low"
    if value <= high_threshold:
        return "Medium"
    return "High"


def classify_traffic(row):
    density_group = row["residential_density_group"]
    residential = bool(row["residential_evidence"])
    office = bool(row["office_evidence"])

    if residential and office:
        return pd.Series({
            "traffic_category": "Mixed",
            "classification_basis": "Residential and office evidence"
        })
    if density_group == "Low" and office:
        return pd.Series({
            "traffic_category": "Office",
            "classification_basis": (
                "Low residential density and office evidence"
            )
        })
    if residential:
        return pd.Series({
            "traffic_category": "Residential",
            "classification_basis": "Residential density evidence"
        })
    if density_group == "No AMB data":
        basis = "Residential fallback: AMB residential data unavailable"
    else:
        basis = "Residential fallback: insufficient dominant activity evidence"
    return pd.Series({
        "traffic_category": "Residential",
        "classification_basis": basis
    })


def validate_classification(df):
    if df.empty:
        raise ValueError("The scenario contains no RRHs.")
    required = {"rrh_id", "latitude", "longitude", "traffic_category"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Classification is missing columns: {sorted(missing)}"
        )
    if df["rrh_id"].isna().any() or df["rrh_id"].duplicated().any():
        raise ValueError("RRH IDs must be present and unique.")
    if df["traffic_category"].isna().any():
        raise ValueError("Missing traffic categories found.")
    invalid = set(df["traffic_category"]) - set(TRAFFIC_CATEGORIES)
    if invalid:
        raise ValueError(f"Invalid traffic categories: {invalid}")


def load_scenario(scenario_file):
    scenario_file = Path(scenario_file)
    if not scenario_file.exists():
        raise FileNotFoundError(scenario_file)
    scenario = pd.read_csv(
        scenario_file,
        dtype={"rrh_id": "string"}
    )
    required = {"rrh_id", "latitude", "longitude"}
    missing = required - set(scenario.columns)
    if missing:
        raise ValueError(
            f"Scenario is missing columns: {sorted(missing)}"
        )
    if scenario.empty:
        raise ValueError("The scenario contains no RRHs.")
    for column in ("latitude", "longitude"):
        scenario[column] = pd.to_numeric(
            scenario[column], errors="coerce"
        )
    if scenario[["latitude", "longitude"]].isna().any().any():
        raise ValueError("Scenario contains invalid coordinates.")
    invalid_coordinates = (
        ~scenario["latitude"].between(-90, 90)
        | ~scenario["longitude"].between(-180, 180)
    )
    if invalid_coordinates.any():
        raise ValueError("Scenario contains coordinates outside valid ranges.")
    if scenario["rrh_id"].isna().any():
        raise ValueError("Scenario contains an empty RRH ID.")
    if scenario["rrh_id"].str.strip().eq("").any():
        raise ValueError("Scenario contains an empty RRH ID.")
    if scenario["rrh_id"].duplicated().any():
        raise ValueError("Duplicate RRH IDs detected.")
    return scenario


def load_residential_areas():
    params = {
        "where": "1=1",
        "outFields": "densresi21",
        "returnGeometry": "true",
        "outSR": "25831",
        "f": "geojson"
    }
    request = Request(
        AMB_LAYER_URL + "?" + urlencode(params),
        headers={"User-Agent": "Barcelona-CRAN-Research/1.0"}
    )
    for attempt in range(1, 4):
        try:
            with urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except HTTPError as error:
            body = error.read(500).decode("utf-8", errors="replace")
            if error.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise RuntimeError(
                    f"AMB request failed with HTTP {error.code}: {body}"
                ) from error
            time.sleep(2)
        except (URLError, TimeoutError) as error:
            if attempt == 3:
                raise RuntimeError(
                    f"Could not reach the AMB service: {error}"
                ) from error
            time.sleep(2)
    if "error" in data:
        raise RuntimeError(f"AMB service error: {data['error']}")
    features = data.get("features", [])
    if not features:
        raise ValueError("The AMB service returned no residential areas.")

    residential = gpd.GeoDataFrame.from_features(
        features,
        crs=METRIC_CRS
    )
    if "densresi21" not in residential.columns:
        raise ValueError("The AMB layer is missing densresi21.")
    residential["densresi21"] = pd.to_numeric(
        residential["densresi21"], errors="coerce"
    )
    residential = residential.dropna(
        subset=["densresi21", "geometry"]
    )
    if residential.empty:
        raise ValueError("The AMB layer contains no valid density data.")
    return residential[["densresi21", "geometry"]]


def classify_scenario(
    scenario_file=SCENARIO_FILE,
    osm_file=OSM_FILE,
    office_threshold=OFFICE_THRESHOLD
):
    scenario = load_scenario(scenario_file)
    scenario = scenario.drop(
        columns=CLASSIFICATION_DERIVED_COLUMNS,
        errors="ignore"
    )
    osm_file = Path(osm_file)
    if not osm_file.exists():
        raise FileNotFoundError(osm_file)
    if office_threshold < 0:
        raise ValueError("Office threshold cannot be negative.")

    residential = load_residential_areas()
    rrh_points = gpd.GeoDataFrame(
        scenario.copy(),
        geometry=gpd.points_from_xy(
            scenario["longitude"], scenario["latitude"]
        ),
        crs="EPSG:4326"
    ).to_crs(METRIC_CRS)
    residential_matches = gpd.sjoin(
        rrh_points[["rrh_id", "geometry"]],
        residential,
        how="left",
        predicate="within"
    )
    if residential_matches["rrh_id"].duplicated().any():
        raise ValueError("Some RRHs match multiple AMB residential areas.")
    result = scenario.merge(
        residential_matches[["rrh_id", "densresi21"]],
        on="rrh_id",
        how="left",
        validate="one_to_one"
    )

    reference_values = result["densresi21"].dropna()
    if reference_values.empty:
        raise ValueError("No RRHs matched AMB residential density data.")
    low_threshold = reference_values.quantile(1 / 3)
    high_threshold = reference_values.quantile(2 / 3)
    result["residential_density_group"] = result["densresi21"].apply(
        classify_density,
        args=(low_threshold, high_threshold)
    )

    osm = pd.read_csv(osm_file)
    required_osm = {"category", "x_25831", "y_25831"}
    missing = required_osm - set(osm.columns)
    if missing:
        raise ValueError(f"Missing OSM columns: {sorted(missing)}")
    osm_office = osm[
        osm["category"].astype(str).str.lower().eq("office")
    ].copy()
    if not osm_office.empty:
        for column in ("x_25831", "y_25831"):
            osm_office[column] = pd.to_numeric(
                osm_office[column], errors="coerce"
            )
        if osm_office[["x_25831", "y_25831"]].isna().any().any():
            raise ValueError("Office features contain invalid coordinates.")
        office_points = gpd.GeoDataFrame(
            osm_office,
            geometry=gpd.points_from_xy(
                osm_office["x_25831"], osm_office["y_25831"]
            ),
            crs=METRIC_CRS
        )
        rrh_buffers = rrh_points[["rrh_id", "geometry"]].copy()
        rrh_buffers["geometry"] = rrh_buffers.geometry.buffer(
            OFFICE_RADIUS_METRES
        )
        office_matches = gpd.sjoin(
            office_points[["geometry"]],
            rrh_buffers,
            how="inner",
            predicate="within"
        )
        office_counts = (
            office_matches.groupby("rrh_id")
            .size()
            .rename("office_count_500m")
            .reset_index()
        )
        result = result.merge(
            office_counts,
            on="rrh_id",
            how="left",
            validate="one_to_one"
        )
        result["office_count_500m"] = (
            result["office_count_500m"].fillna(0).astype(int)
        )
    else:
        result["office_count_500m"] = 0

    result["residential_evidence"] = (
        result["residential_density_group"].isin(["Medium", "High"])
    )
    result["office_evidence"] = (
        result["office_count_500m"] >= office_threshold
    )
    classification = result.apply(classify_traffic, axis=1)
    result = pd.concat([result, classification], axis=1)
    validate_classification(result)

    print("Residential density thresholds:")
    print(f"Low / Medium boundary: {low_threshold:.4f}")
    print(f"Medium / High boundary: {high_threshold:.4f}")
    print(
        f"Office evidence: at least {office_threshold} features "
        f"within {OFFICE_RADIUS_METRES} metres."
    )
    print(
        result["traffic_category"]
        .value_counts()
        .reindex(TRAFFIC_CATEGORIES, fill_value=0)
        .to_string()
    )
    return result


def save_classification(classification, output_file):
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    detail_columns = [
        column for column in CLASSIFICATION_DERIVED_COLUMNS
        if column != "traffic_category"
    ]
    scenario = classification.drop(columns=detail_columns, errors="ignore")

    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_file.parent,
        prefix=f".{output_file.name}.",
        suffix=".tmp"
    )
    temporary_file = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8-sig", newline=""
        ) as csvfile:
            scenario.to_csv(csvfile, index=False)
        os.replace(temporary_file, output_file)
    finally:
        temporary_file.unlink(missing_ok=True)


def safe_text(value, default="Not available"):
    if value is None or pd.isna(value):
        return default
    return escape(str(value))


def safe_number(value, decimals=2):
    if value is None or pd.isna(value):
        return "Not available"
    return f"{float(value):,.{decimals}f}"


def category_shape_markup(category):
    shapes = {
        "Residential": (
            "width:14px;height:14px;border:2px solid #222;"
            "border-radius:50%;background:#fff;"
        ),
        "Mixed": (
            "width:18px;height:16px;background:#222;"
            "clip-path:polygon(50% 0,0 100%,100% 100%);"
        ),
        "Office": (
            "width:14px;height:14px;border:2px solid #222;"
            "background:#fff;"
        )
    }
    try:
        style = shapes[category]
    except KeyError as error:
        raise ValueError(f"Unknown traffic category: {category}") from error
    return (
        f'<span style="display:inline-block;vertical-align:middle;'
        f'margin-right:5px;{style}"></span>'
    )


def add_markers(map_object, classification):
    counts = (
        classification["traffic_category"]
        .value_counts()
        .reindex(TRAFFIC_CATEGORIES, fill_value=0)
    )
    for category in TRAFFIC_CATEGORIES:
        subset = classification[
            classification["traffic_category"] == category
        ]
        layer = folium.FeatureGroup(
            name=category,
            overlay=True,
            show=True
        )
        for _, row in subset.iterrows():
            rrh_id = safe_text(row["rrh_id"])
            traffic = safe_text(row["traffic_category"])
            latitude = float(row["latitude"])
            longitude = float(row["longitude"])
            details = []
            optional_fields = [
                ("Classification basis", "classification_basis"),
                ("Residential density group", "residential_density_group"),
                ("Residential density", "densresi21"),
                ("Offices within 500 m", "office_count_500m")
            ]
            for label, field in optional_fields:
                if field not in classification.columns:
                    continue
                value = row[field]
                if field == "densresi21":
                    value = safe_number(value)
                else:
                    value = safe_text(value)
                details.append(
                    f"<b>{escape(label)}:</b> {value}<br>"
                )
            popup = f"""
            <div style="width:285px;font-family:Arial,sans-serif;font-size:13px;">
                <h3 style="margin-bottom:8px;">RRH {rrh_id}</h3>
                <b>Traffic category:</b> {traffic}<br>
                {''.join(details)}<hr>
                <b>Latitude:</b> {latitude:.6f}<br>
                <b>Longitude:</b> {longitude:.6f}
            </div>
            """
            shape = TRAFFIC_SHAPES[category]
            folium.Marker(
                location=[latitude, longitude],
                icon=folium.DivIcon(
                    html=category_shape_markup(category),
                    icon_size=(20, 20),
                    icon_anchor=(10, 10)
                ),
                popup=folium.Popup(popup, max_width=340),
                tooltip=f"RRH {rrh_id} | {category} ({shape})"
            ).add_to(layer)
        layer.add_to(map_object)
        print(f"{category}: {len(subset)} RRHs")

    legend_items = "".join(
        f"{category_shape_markup(category)}"
        f"{escape(category)}: {counts[category]}<br>"
        for category in TRAFFIC_CATEGORIES
    )
    legend = f"""
    <div style="
        position:fixed;bottom:25px;right:15px;z-index:9999;
        background:white;padding:15px;border:1px solid #777;
        border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.3);
        font-family:Arial,sans-serif;font-size:13px;">
        <h3 style="margin:0 0 12px 0;">Traffic classification</h3>
        {legend_items}<hr><b>Total RRHs:</b> {len(classification)}
    </div>
    """
    map_object.get_root().html.add_child(Element(legend))


def create_map(
    classification,
    boundary_file=BOUNDARY_FILE,
    output_file=None
):
    validate_classification(classification)
    boundary_file = Path(boundary_file)
    if not boundary_file.exists():
        raise FileNotFoundError(boundary_file)
    boundary = gpd.read_file(boundary_file, driver="KML")
    if boundary.crs is None:
        raise ValueError("Missing CRS in study boundary.")
    boundary_web = boundary.to_crs("EPSG:4326")
    center = boundary_web.geometry.union_all().centroid

    map_object = folium.Map(
        location=[center.y, center.x],
        zoom_start=10,
        tiles=None,
        control_scale=True,
        prefer_canvas=True,
        width="100%",
        height="100vh"
    )
    folium.TileLayer(
        tiles=(
            "https://server.arcgisonline.com/"
            "ArcGIS/rest/services/World_Street_Map/"
            "MapServer/tile/{z}/{y}/{x}"
        ),
        attr=(
            "Tiles © Esri. Sources: Esri, DeLorme, HERE, "
            "USGS, Intermap, iPC, NRCAN, Esri Japan, METI, "
            "Esri China (Hong Kong), Esri (Thailand), "
            "MapmyIndia, TomTom"
        ),
        name="Esri World Street Map",
        overlay=False,
        show=True,
        max_zoom=19
    ).add_to(map_object)

    add_markers(map_object, classification)
    boundary_layer = folium.FeatureGroup(
        name="Study boundary",
        show=True
    )
    folium.GeoJson(
        boundary_web.to_json(),
        name="Study boundary",
        style_function=lambda feature: {
            "fillColor": "transparent",
            "color": "#222222",
            "weight": 2.5,
            "fillOpacity": 0
        },
        tooltip="Study Area"
    ).add_to(boundary_layer)
    boundary_layer.add_to(map_object)

    map_css = """
    <style>
    html, body {
        width: 100%; height: 100%; margin: 0; padding: 0; overflow: hidden;
    }
    .folium-map {
        width: 100% !important; height: 100vh !important;
        min-height: 600px !important;
    }
    .leaflet-container { background: #e8edf0; }
    </style>
    """
    map_object.get_root().header.add_child(Element(map_css))
    folium.LayerControl(position="topright", collapsed=False).add_to(map_object)
    west, south, east, north = boundary_web.total_bounds
    map_object.fit_bounds([[south, west], [north, east]])

    if output_file is None:
        output_file = BASE_DIR / "output" / "traffic_interactive.html"
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    map_object.save(str(output_file))
    print("Saved map:", output_file)


def main():
    parser = argparse.ArgumentParser(
        description="Classify RRHs using geographic activity and create a map."
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        default=SCENARIO_FILE,
        help="Scenario CSV containing rrh_id, latitude, and longitude."
    )
    parser.add_argument(
        "--office-data",
        type=Path,
        default=OSM_FILE,
        help="Processed OpenStreetMap business features CSV."
    )
    parser.add_argument(
        "--boundary",
        type=Path,
        default=BOUNDARY_FILE,
        help="Study-area KML used as the map boundary."
    )
    parser.add_argument(
        "--office-threshold",
        type=int,
        default=OFFICE_THRESHOLD,
        help="Office count that qualifies as office activity."
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        help="Optional alternate CSV path; by default updates the scenario."
    )
    parser.add_argument("--output-map", type=Path)
    args = parser.parse_args()
    if args.office_threshold < 0:
        parser.error("--office-threshold cannot be negative.")

    classification = classify_scenario(
        scenario_file=args.scenario,
        osm_file=args.office_data,
        office_threshold=args.office_threshold
    )
    output_csv = args.output_csv or args.scenario
    output_map = args.output_map or (
        BASE_DIR / "output"
        / f"{args.scenario.stem}_traffic_classification.html"
    )
    save_classification(classification, output_csv)
    if output_csv.resolve() == args.scenario.resolve():
        print("Updated scenario with traffic category:", output_csv)
    else:
        print("Saved classification:", output_csv)
    create_map(
        classification,
        boundary_file=args.boundary,
        output_file=output_map
    )


if __name__ == "__main__":
    main()
