from html import escape
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
from branca.element import Element


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data" / "processed"
SCENARIO_FILE = DATA_DIR / "rrh_scenario_400.csv"
AMB_FILE = DATA_DIR / "amb_residential_coverage_400.csv"
DENSITY_REFERENCE_FILE = DATA_DIR / "residential_density_analysis.csv"
OSM_FILE = DATA_DIR / "osm_business_features_study_area.csv"
BOUNDARY_FILE = (
    BASE_DIR / "data" / "input" / "working_area_900km2.kml"
)
CLASSIFICATION_FILE = (
    DATA_DIR / "rrh_scenario_400_traffic_realistic.csv"
)
OUTPUT_FILE = (
    BASE_DIR / "output" / "rrh_scenario_400_traffic_interactive.html"
)

EXPECTED_RRHS = 400
OFFICE_RADIUS_METRES = 500
OFFICE_THRESHOLD = 5
METRIC_CRS = "EPSG:25831"
TRAFFIC_CATEGORIES = ["Residential", "Office", "Mixed"]
TRAFFIC_COLORS = {
    "Residential": "#299D67",
    "Office": "#3984D6",
    "Mixed": "#D58B36"
}


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

    if density_group == "No AMB data":
        return pd.Series({
            "traffic_category": "Mixed",
            "classification_basis": (
                "Mixed fallback: AMB residential data unavailable"
            )
        })
    if residential and office:
        return pd.Series({
            "traffic_category": "Mixed",
            "classification_basis": "Residential and office evidence"
        })
    if residential:
        return pd.Series({
            "traffic_category": "Residential",
            "classification_basis": "Residential density evidence"
        })
    if density_group == "Low" and office:
        return pd.Series({
            "traffic_category": "Office",
            "classification_basis": (
                "Low residential density and office evidence"
            )
        })
    return pd.Series({
        "traffic_category": "Mixed",
        "classification_basis": (
            "Mixed fallback: insufficient dominant activity evidence"
        )
    })


def validate_classification(df):
    if len(df) != EXPECTED_RRHS:
        raise ValueError(
            f"Expected {EXPECTED_RRHS} RRHs, found {len(df)}."
        )
    if df["rrh_id"].duplicated().any():
        raise ValueError("Duplicate RRH IDs detected.")
    if df["traffic_category"].isna().any():
        raise ValueError("Missing traffic categories found.")

    invalid = set(df["traffic_category"]) - set(TRAFFIC_CATEGORIES)
    if invalid:
        raise ValueError(f"Invalid traffic categories: {invalid}")


def classify_scenario():
    for file in [
        SCENARIO_FILE,
        AMB_FILE,
        DENSITY_REFERENCE_FILE,
        OSM_FILE
    ]:
        if not file.exists():
            raise FileNotFoundError(file)

    scenario = pd.read_csv(
        SCENARIO_FILE,
        dtype={"rrh_id": "string"}
    )
    required_scenario = {"rrh_id", "latitude", "longitude"}
    missing = required_scenario - set(scenario.columns)
    if missing:
        raise ValueError(
            f"Scenario is missing columns: {sorted(missing)}"
        )
    if len(scenario) != EXPECTED_RRHS:
        raise ValueError(
            f"Expected {EXPECTED_RRHS} RRHs, found {len(scenario)}."
        )
    if scenario["rrh_id"].duplicated().any():
        raise ValueError("Duplicate RRH IDs detected.")

    amb = pd.read_csv(
        AMB_FILE,
        dtype={"rrh_id": "string"}
    )
    required_amb = {"rrh_id", "densresi21"}
    missing = required_amb - set(amb.columns)
    if missing:
        raise ValueError(
            f"AMB residential data is missing columns: {sorted(missing)}"
        )
    if amb["rrh_id"].duplicated().any():
        raise ValueError("Duplicate RRH IDs in AMB residential dataset.")
    amb = amb[["rrh_id", "densresi21"]].copy()
    amb["densresi21"] = pd.to_numeric(
        amb["densresi21"], errors="coerce"
    )

    result = scenario.merge(
        amb,
        on="rrh_id",
        how="left",
        validate="one_to_one"
    )

    density_reference = pd.read_csv(DENSITY_REFERENCE_FILE)
    if "densresi21" not in density_reference.columns:
        raise ValueError(
            f"{DENSITY_REFERENCE_FILE} is missing densresi21."
        )
    reference_values = pd.to_numeric(
        density_reference["densresi21"],
        errors="coerce"
    ).dropna()
    if reference_values.empty:
        raise ValueError(
            "No valid residential density reference values."
        )

    low_threshold = reference_values.quantile(1 / 3)
    high_threshold = reference_values.quantile(2 / 3)
    result["residential_density_group"] = result["densresi21"].apply(
        classify_density,
        args=(low_threshold, high_threshold)
    )

    osm = pd.read_csv(OSM_FILE)
    required_osm = {"category", "x_25831", "y_25831"}
    missing = required_osm - set(osm.columns)
    if missing:
        raise ValueError(f"Missing OSM columns: {sorted(missing)}")
    osm_office = osm[
        osm["category"].astype(str).str.lower().eq("office")
    ].dropna(subset=["x_25831", "y_25831"]).copy()
    if osm_office.empty:
        raise ValueError("No office features are available for classification.")

    rrh_points = gpd.GeoDataFrame(
        result[["rrh_id", "latitude", "longitude"]].copy(),
        geometry=gpd.points_from_xy(
            result["longitude"], result["latitude"]
        ),
        crs="EPSG:4326"
    ).to_crs(METRIC_CRS)
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
    result["residential_evidence"] = (
        result["residential_density_group"].isin(["Medium", "High"])
    )
    result["office_evidence"] = (
        result["office_count_500m"] >= OFFICE_THRESHOLD
    )

    classification = result.apply(classify_traffic, axis=1)
    result = pd.concat([result, classification], axis=1)
    validate_classification(result)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    result.to_csv(CLASSIFICATION_FILE, index=False)

    print("Residential density thresholds:")
    print(f"Low / Medium boundary: {low_threshold:.4f}")
    print(f"Medium / High boundary: {high_threshold:.4f}")
    print(
        f"Office evidence: at least {OFFICE_THRESHOLD} features "
        f"within {OFFICE_RADIUS_METRES} metres."
    )
    print(
        result["traffic_category"]
        .value_counts()
        .reindex(TRAFFIC_CATEGORIES, fill_value=0)
        .to_string()
    )
    print("Saved classification:", CLASSIFICATION_FILE)
    return result


def safe_text(value, default="Not available"):
    if value is None or pd.isna(value):
        return default
    return escape(str(value))


def safe_number(value, decimals=2):
    if value is None or pd.isna(value):
        return "Not available"
    return f"{float(value):,.{decimals}f}"


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
        color = TRAFFIC_COLORS[category]
        layer = folium.FeatureGroup(
            name=category,
            overlay=True,
            show=True
        )

        for _, row in subset.iterrows():
            rrh_id = safe_text(row["rrh_id"])
            traffic = safe_text(row["traffic_category"])
            basis = safe_text(row["classification_basis"])
            density_group = safe_text(
                row["residential_density_group"]
            )
            density = safe_number(row["densresi21"])
            office_count = safe_text(row["office_count_500m"])
            latitude = float(row["latitude"])
            longitude = float(row["longitude"])

            popup = f"""
            <div style="width:285px;font-family:Arial,sans-serif;font-size:13px;">
                <h3 style="color:{color};margin-bottom:8px;">RRH {rrh_id}</h3>
                <b>Traffic category:</b> {traffic}<br>
                <b>Classification basis:</b> {basis}<hr>
                <b>Residential density group:</b> {density_group}<br>
                <b>Residential density:</b> {density} dwellings/ha<br>
                <b>Nearby offices (500 m):</b> {office_count}<hr>
                <b>Latitude:</b> {latitude:.6f}<br>
                <b>Longitude:</b> {longitude:.6f}
            </div>
            """
            folium.CircleMarker(
                location=[latitude, longitude],
                radius=5.5,
                color="#222222",
                weight=0.8,
                fill=True,
                fill_color=color,
                fill_opacity=0.95,
                popup=folium.Popup(popup, max_width=340),
                tooltip=f"RRH {rrh_id} | {category}"
            ).add_to(layer)

        layer.add_to(map_object)
        print(f"{category}: {len(subset)} RRHs")

    legend_items = "".join(
        f'<span style="color:{TRAFFIC_COLORS[category]};">●</span> '
        f'{category}: {counts[category]}<br>'
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


def create_map(classification):
    if not BOUNDARY_FILE.exists():
        raise FileNotFoundError(BOUNDARY_FILE)

    boundary = gpd.read_file(BOUNDARY_FILE, driver="KML")
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
        name="900 km² Barcelona Study Boundary",
        show=True
    )
    folium.GeoJson(
        boundary_web.to_json(),
        name="Barcelona Study Boundary",
        style_function=lambda feature: {
            "fillColor": "transparent",
            "color": "#222222",
            "weight": 2.5,
            "fillOpacity": 0
        },
        tooltip="Barcelona C-RAN Study Area"
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
    folium.LayerControl(
        position="topright",
        collapsed=False
    ).add_to(map_object)

    west, south, east, north = boundary_web.total_bounds
    map_object.fit_bounds([[south, west], [north, east]])
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    map_object.save(str(OUTPUT_FILE))
    print("Saved map:", OUTPUT_FILE)


def main():
    classification = classify_scenario()
    create_map(classification)


if __name__ == "__main__":
    main()
