
import geopandas as gpd
import pandas as pd
from pathlib import Path

# ==========================================
# 1. PROJECT PATHS
# ==========================================

PROJECT_DIR = Path(__file__).resolve().parent.parent

KML_FILE = (
    PROJECT_DIR / "data" / "input"
    / "working_area_900km2.kml"
)

OSM_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "osm_business_features.csv"
)

OUTPUT_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "osm_business_features_study_area.csv"
)

# ==========================================
# 2. LOAD STUDY AREA
# ==========================================

def main():

    print("======================================")
    print("FILTER OSM TO STUDY AREA")
    print("======================================")

    print("Reading study-area KML...")

    study_area = gpd.read_file(KML_FILE)

    if study_area.crs is None:
        raise ValueError("Study-area KML has no CRS defined.")

    study_area = study_area.to_crs("EPSG:25831")

    # Combine all polygons into one geometry.
    boundary = study_area.geometry.union_all()

    print("Study-area boundary loaded.")

    # ======================================
    # 3. LOAD OSM FEATURES
    # ======================================

    print("Loading OSM business features...")

    df = pd.read_csv(OSM_FILE)

    print(f"Features before filtering: {len(df)}")

    # ======================================
    # 4. CREATE GEOGRAPHIC POINTS
    # ======================================

    points = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(
            df["longitude"],
            df["latitude"]
        ),
        crs="EPSG:4326"
    )

    points = points.to_crs("EPSG:25831")

    # ======================================
    # 5. FILTER INSIDE STUDY AREA
    # ======================================

    print("Filtering features inside boundary...")

    filtered = points[
        points.geometry.intersects(boundary)
    ].copy()

    # Store projected coordinates for later analysis.
    filtered["x_25831"] = filtered.geometry.x
    filtered["y_25831"] = filtered.geometry.y

    # Remove geometry before saving CSV.
    filtered = pd.DataFrame(
        filtered.drop(columns="geometry")
    )

    # ======================================
    # 6. SAVE RESULTS
    # ======================================

    filtered.to_csv(
        OUTPUT_FILE,
        index=False
    )

    # ======================================
    # 7. SUMMARY
    # ======================================

    print("--------------------------------------")
    print("FILTERING COMPLETED")
    print("--------------------------------------")

    print(f"Features before: {len(df)}")
    print(f"Features inside: {len(filtered)}")
    print(
        f"Features outside: {len(df) - len(filtered)}"
    )

    print("\nCategory counts:")

    print(
        filtered["category"]
        .value_counts()
        .to_string()
    )

    print("\nSaved to:")
    print(OUTPUT_FILE)


if __name__ == "__main__":
    main()
