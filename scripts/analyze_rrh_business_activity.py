
import geopandas as gpd
import pandas as pd
from pathlib import Path

# ==========================================
# 1. PROJECT PATHS
# ==========================================

PROJECT_DIR = Path(__file__).resolve().parent.parent

RRH_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "scenario_sites.csv"
)

OSM_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "osm_business_features_study_area.csv"
)

OUTPUT_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "rrh_business_activity.csv"
)

RADIUS_METRES = 500

# ==========================================
# 2. LOAD DATA
# ==========================================

def main():

    print("======================================")
    print("RRH BUSINESS ACTIVITY ANALYSIS")
    print("======================================")

    print("Loading RRH locations...")

    rrhs = pd.read_csv(RRH_FILE)

    print("Loading OSM business features...")

    osm = pd.read_csv(OSM_FILE)

    print(f"RRHs loaded: {len(rrhs)}")
    print(f"OSM features loaded: {len(osm)}")

    # ======================================
    # 3. CONVERT RRHS TO PROJECTED CRS
    # ======================================

    rrh_gdf = gpd.GeoDataFrame(
        rrhs,
        geometry=gpd.points_from_xy(
            rrhs["longitude"],
            rrhs["latitude"]
        ),
        crs="EPSG:4326"
    ).to_crs("EPSG:25831")

    # ======================================
    # 4. LOAD OSM PROJECTED COORDINATES
    # ======================================

    osm_gdf = gpd.GeoDataFrame(
        osm,
        geometry=gpd.points_from_xy(
            osm["x_25831"],
            osm["y_25831"]
        ),
        crs="EPSG:25831"
    )

    # ======================================
    # 5. COUNT FEATURES WITHIN 500 M
    # ======================================

    print("Calculating business activity...")
    print(f"Search radius: {RADIUS_METRES} metres")

    results = []

    for index, rrh in rrh_gdf.iterrows():

        buffer = rrh.geometry.buffer(RADIUS_METRES)

        nearby = osm_gdf[
            osm_gdf.geometry.within(buffer)
        ]

        counts = (
            nearby["category"]
            .value_counts()
            .to_dict()
        )

        results.append({

            "rrh_id": rrh["rrh_id"],

            "shop_count": counts.get("shop", 0),

            "office_count": counts.get("office", 0),

            "retail_count": counts.get("retail", 0),

            "commercial_count": counts.get("commercial", 0),

            "total_business_features": len(nearby)

        })

        if (index + 1) % 50 == 0:

            print(
                f"Processed {index + 1}/{len(rrh_gdf)} RRHs"
            )

    # ======================================
    # 6. SAVE RESULTS
    # ======================================

    results_df = pd.DataFrame(results)

    results_df.to_csv(
        OUTPUT_FILE,
        index=False
    )

    print("--------------------------------------")
    print("ANALYSIS COMPLETED")
    print("--------------------------------------")

    print(f"RRHs analysed: {len(results_df)}")

    print("\nAverage nearby features per RRH:")

    print(
        results_df[
            [
                "shop_count",
                "office_count",
                "retail_count",
                "commercial_count",
                "total_business_features"
            ]
        ].mean().round(2).to_string()
    )

    print("\nOutput saved to:")
    print(OUTPUT_FILE)


if __name__ == "__main__":
    main()
