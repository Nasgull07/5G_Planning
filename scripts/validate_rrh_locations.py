
from pathlib import Path
import pandas as pd
import geopandas as gpd

BASE_DIR = Path(__file__).resolve().parents[1]

RRH_FILE = BASE_DIR / "data/processed/scenario_sites_realistic_classified.csv"
KML_FILE = BASE_DIR / "data/input/working_area_900km2.kml"

df = pd.read_csv(RRH_FILE)

rrhs = gpd.GeoDataFrame(
    df.copy(),
    geometry=gpd.points_from_xy(df.longitude, df.latitude),
    crs="EPSG:4326"
)

boundary = gpd.read_file(KML_FILE, driver="KML").to_crs("EPSG:4326")

# Combine boundary geometries
study_area = boundary.geometry.union_all()

# Check whether each RRH is inside or on the boundary
rrhs["inside_study_area"] = rrhs.geometry.apply(
    lambda point: study_area.covers(point)
)

inside = rrhs[rrhs["inside_study_area"]]
outside = rrhs[~rrhs["inside_study_area"]]

print("======================================")
print("RRH STUDY-AREA VALIDATION")
print("======================================")
print(f"Total RRHs: {len(rrhs)}")
print(f"Inside study area: {len(inside)}")
print(f"Outside study area: {len(outside)}")

print("\nOutside RRHs by source:")
print(outside["source_type"].value_counts())

if len(outside) > 0:
    output_file = (
        BASE_DIR / "data/processed/rrhs_outside_study_area.csv"
    )
    outside.drop(columns="geometry").to_csv(output_file, index=False)
    print(f"\nOutside RRHs saved to: {output_file}")

print("\nValidation completed.")
