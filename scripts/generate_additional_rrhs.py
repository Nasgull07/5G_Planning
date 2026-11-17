
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


# =====================================================
# 1. CONFIGURATION
# =====================================================

BASE_DIR = Path(__file__).resolve().parents[1]

REAL_RRH_FILE = (
    BASE_DIR / "data" / "processed"
    / "rrh_min_delay_200.csv"
)

BOUNDARY_FILE = (
    BASE_DIR / "data" / "input"
    / "working_area_900km2.kml"
)

DEGURBA_DIR = BASE_DIR / "data" / "input" / "degurba"

OUTPUT_DIR = BASE_DIR / "data" / "processed"

SYNTHETIC_OUTPUT = (
    OUTPUT_DIR / "rrh_synthetic_200.csv"
)

SCENARIO_OUTPUT = (
    OUTPUT_DIR / "rrh_scenario_400.csv"
)

# Adjustable simulation parameters
BASE_RRH_COUNT = 200
ADDITIONAL_RRH_COUNT = 200

TOTAL_RRH_COUNT = (
    BASE_RRH_COUNT + ADDITIONAL_RRH_COUNT
)

MIN_SEPARATION_METERS = 200
RANDOM_SEED = 42

# Metric coordinate system suitable for Barcelona
METRIC_CRS = "EPSG:25831"


# =====================================================
# 2. DEGURBA DEFINITIONS
# =====================================================

DEGURBA_NAMES = {
    1: "Urban centre",
    2: "Intermediate density",
    3: "Rural / thinly populated"
}


# =====================================================
# 3. LOAD THE REAL RRH BASELINE
# =====================================================

print("=" * 65)
print("BARCELONA C-RAN - 400 RRH SCENARIO GENERATION")
print("=" * 65)

if not REAL_RRH_FILE.exists():
    raise FileNotFoundError(REAL_RRH_FILE)

real = pd.read_csv(REAL_RRH_FILE)

if len(real) != BASE_RRH_COUNT:
    raise ValueError(
        f"Expected {BASE_RRH_COUNT} real RRHs, "
        f"found {len(real)}."
    )

if real["rrh_id"].duplicated().any():
    raise ValueError("Duplicate real RRH IDs found.")

if real["is_synthetic"].astype(str).str.lower().eq("true").any():
    raise ValueError("The baseline must contain only real RRHs.")

print("\nReal RRHs loaded:", len(real))


# =====================================================
# 4. LOAD THE STUDY BOUNDARY
# =====================================================

boundary = gpd.read_file(
    BOUNDARY_FILE,
    driver="KML"
)

boundary = boundary.to_crs(METRIC_CRS)

study_area = boundary.geometry.union_all()

if study_area.is_empty:
    raise ValueError("Study boundary is empty.")

min_x, min_y, max_x, max_y = study_area.bounds

print("\nStudy boundary loaded.")
print("Metric CRS:", METRIC_CRS)


# =====================================================
# 5. PROJECT EXISTING RRHs TO METRIC COORDINATES
# =====================================================

real_points = gpd.GeoDataFrame(
    real.copy(),
    geometry=gpd.points_from_xy(
        real["longitude"],
        real["latitude"]
    ),
    crs="EPSG:4326"
).to_crs(METRIC_CRS)

# Verify all existing RRHs are within the boundary
real_inside = real_points.geometry.apply(
    study_area.covers
)

if not real_inside.all():
    raise ValueError(
        "Some real RRHs are outside the study boundary."
    )

# Existing coordinates become the initial occupied locations
existing_coordinates = np.array([
    (point.x, point.y)
    for point in real_points.geometry
])

# Check the original baseline separation
real_min_distance = float("inf")

for i in range(len(existing_coordinates)):
    distances = np.linalg.norm(
        existing_coordinates[i + 1:]
        - existing_coordinates[i],
        axis=1
    )

    if len(distances):
        real_min_distance = min(
            real_min_distance,
            distances.min()
        )

print(
    "\nMinimum distance among real RRHs:",
    f"{real_min_distance:.2f} metres"
)


# =====================================================
# 6. GENERATE RANDOM SYNTHETIC RRHs
# =====================================================

rng = np.random.default_rng(RANDOM_SEED)

accepted_coordinates = (
    existing_coordinates.tolist()
)

synthetic_coordinates = []

attempts = 0
max_attempts = 500_000

print("\nGenerating synthetic RRHs...")

while len(synthetic_coordinates) < ADDITIONAL_RRH_COUNT:

    attempts += 1

    if attempts > max_attempts:
        raise RuntimeError(
            "Maximum attempts reached. "
            "Try reducing minimum separation."
        )

    # Generate random candidate within bounding box
    x = rng.uniform(min_x, max_x)
    y = rng.uniform(min_y, max_y)

    candidate = gpd.points_from_xy(
        [x], [y],
        crs=METRIC_CRS
    )[0]

    # Candidate must be strictly inside the study area
    if not study_area.contains(candidate):
        continue

    # Calculate distance to all existing and new RRHs
    coordinates_array = np.asarray(
        accepted_coordinates
    )

    distances = np.linalg.norm(
        coordinates_array - np.array([x, y]),
        axis=1
    )

    nearest_distance = distances.min()

    # Enforce minimum separation
    if nearest_distance < MIN_SEPARATION_METERS:
        continue

    accepted_coordinates.append([x, y])
    synthetic_coordinates.append([x, y])

    current = len(synthetic_coordinates)

    if current % 25 == 0:
        print(
            f"Generated {current} / "
            f"{ADDITIONAL_RRH_COUNT}"
        )


# =====================================================
# 7. CONVERT SYNTHETIC LOCATIONS TO LATITUDE/LONGITUDE
# =====================================================

synthetic_points = gpd.GeoDataFrame(
    geometry=gpd.points_from_xy(
        [p[0] for p in synthetic_coordinates],
        [p[1] for p in synthetic_coordinates]
    ),
    crs=METRIC_CRS
)

synthetic_geo = synthetic_points.to_crs("EPSG:4326")

synthetic_data = pd.DataFrame({
    "rrh_id": [
        f"SYN_RRH_{i:04d}"
        for i in range(1, ADDITIONAL_RRH_COUNT + 1)
    ],
    "latitude": synthetic_geo.geometry.y.values,
    "longitude": synthetic_geo.geometry.x.values,
    "source_type": "SYNTHETIC",
    "operator": "SYNTHETIC",
    "technology": "ASSUMED_FOR_SIMULATION",
    "is_synthetic": True
})


# =====================================================
# 8. ASSIGN OFFICIAL DEGURBA GEOGRAPHICAL CLASS
# =====================================================

grid_files = list(
    DEGURBA_DIR.rglob("mapa2025_degurba_celes.shp")
)

if not grid_files:
    raise FileNotFoundError("DEGURBA grid not found.")

grid = gpd.read_file(grid_files[0])

grid = grid[
    ["id", "degurba", "geometry"]
].to_crs(METRIC_CRS)

synthetic_join = gpd.sjoin(
    synthetic_points,
    grid,
    how="left",
    predicate="within"
)

if synthetic_join["degurba"].isna().any():
    unmatched = synthetic_join["degurba"].isna().sum()

    raise ValueError(
        f"{unmatched} synthetic RRHs could not be "
        "matched to DEGURBA cells."
    )

if len(synthetic_join) != ADDITIONAL_RRH_COUNT:
    raise ValueError(
        "Unexpected number of synthetic RRH matches."
    )

synthetic_data["degurba"] = (
    synthetic_join["degurba"].astype(int).values
)

synthetic_data["degurba_class"] = (
    synthetic_data["degurba"].map(DEGURBA_NAMES)
)


# =====================================================
# 9. VERIFY AND EXPORT THE 200 SYNTHETIC RRHs
# =====================================================

if synthetic_data["rrh_id"].duplicated().any():
    raise ValueError("Duplicate synthetic RRH IDs found.")

synthetic_data.to_csv(
    SYNTHETIC_OUTPUT,
    index=False
)

# =====================================================
# 10. COMBINE 200 REAL + 200 SYNTHETIC
# =====================================================

scenario = pd.concat(
    [real, synthetic_data],
    ignore_index=True,
    sort=False
)

if len(scenario) != TOTAL_RRH_COUNT:
    raise ValueError(
        f"Expected {TOTAL_RRH_COUNT} total RRHs, "
        f"found {len(scenario)}."
    )

if scenario["rrh_id"].duplicated().any():
    raise ValueError("Duplicate RRH IDs in final scenario.")

scenario.to_csv(
    SCENARIO_OUTPUT,
    index=False
)


# =====================================================
# 11. FINAL REPORT
# =====================================================

print("\n" + "=" * 65)
print("400 RRH SCENARIO COMPLETED")
print("=" * 65)

print("\n--- RRH SUMMARY ---")
print("Real RRHs:", len(real))
print("Synthetic RRHs:", len(synthetic_data))
print("Total RRHs:", len(scenario))

print("\n--- SYNTHETIC RRHs BY DEGURBA ---")
print(
    synthetic_data["degurba_class"]
    .value_counts()
    .to_string()
)

print("\n--- TOTAL RRHs BY SOURCE ---")
print(
    scenario["source_type"]
    .value_counts()
    .to_string()
)

print("\n--- TOTAL RRHs BY TECHNOLOGY ---")
print(
    scenario["technology"]
    .value_counts()
    .to_string()
)

print("\n--- MINIMUM SEPARATION ---")
print(
    "Required separation:",
    MIN_SEPARATION_METERS,
    "metres"
)

print(
    "New synthetic locations checked against "
    "all previous locations."
)

print("\n--- OUTPUT FILES ---")
print("Synthetic only:", SYNTHETIC_OUTPUT)
print("Combined scenario:", SCENARIO_OUTPUT)

print("\nRandom seed:", RANDOM_SEED)
print("Candidate attempts:", attempts)

print("\n400 RRH scenario generated successfully.")
