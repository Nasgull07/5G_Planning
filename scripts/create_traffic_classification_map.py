
from pathlib import Path

import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

# --------------------------------------------------
# 1. PROJECT PATHS
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parents[1]

RRH_FILE = (
    BASE_DIR / "data" / "processed"
    / "scenario_sites_realistic_classified.csv"
)

KML_FILE = (
    BASE_DIR / "data" / "input"
    / "working_area_900km2.kml"
)

OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = OUTPUT_DIR / "rrh_traffic_classification_map.png"

# --------------------------------------------------
# 2. LOAD DATA
# --------------------------------------------------

print("Loading RRH data...")
df = pd.read_csv(RRH_FILE)

print("Loading study-area boundary...")
boundary = gpd.read_file(KML_FILE, driver="KML")

# Check required columns
required = [
    "latitude",
    "longitude",
    "source_type",
    "traffic_category"
]

missing = [col for col in required if col not in df.columns]

if missing:
    raise ValueError(f"Missing columns: {missing}")

# --------------------------------------------------
# 3. CONVERT TO EPSG:25831
# --------------------------------------------------

# Create geographical points from RRH coordinates
rrh_gdf = gpd.GeoDataFrame(
    df.copy(),
    geometry=gpd.points_from_xy(
        df["longitude"],
        df["latitude"]
    ),
    crs="EPSG:4326"
)

# Convert both datasets to the same projected CRS
rrh_gdf = rrh_gdf.to_crs("EPSG:25831")
boundary = boundary.to_crs("EPSG:25831")

# --------------------------------------------------
# 4. MAP STYLING
# --------------------------------------------------

category_colors = {
    "Residential": "green",
    "Office": "royalblue",
    "Mixed": "orange",
    "Undetermined": "gray"
}

source_markers = {
    "REAL_4G_5G": "o",
    "SYNTHETIC": "^"
}

fig, ax = plt.subplots(figsize=(13, 12))

# --------------------------------------------------
# 5. DRAW STUDY-AREA BOUNDARY
# --------------------------------------------------

boundary.plot(
    ax=ax,
    facecolor="none",
    edgecolor="black",
    linewidth=2,
    zorder=1
)

# --------------------------------------------------
# 6. DRAW RRH LOCATIONS
# --------------------------------------------------

for category, color in category_colors.items():

    for source, marker in source_markers.items():

        subset = rrh_gdf[
            (rrh_gdf["traffic_category"] == category)
            & (rrh_gdf["source_type"] == source)
        ]

        if not subset.empty:
            ax.scatter(
                subset.geometry.x,
                subset.geometry.y,
                color=color,
                marker=marker,
                s=45 if source == "REAL_4G_5G" else 65,
                edgecolors="black",
                linewidths=0.5,
                alpha=0.85,
                zorder=2
            )

# --------------------------------------------------
# 7. CREATE LEGENDS
# --------------------------------------------------

category_handles = [
    Line2D(
        [0], [0],
        marker="o",
        color="w",
        label=category,
        markerfacecolor=color,
        markeredgecolor="black",
        markersize=9
    )
    for category, color in category_colors.items()
]

source_handles = [
    Line2D(
        [0], [0],
        marker=marker,
        color="black",
        linestyle="None",
        label=source.replace("_", " ").title(),
        markerfacecolor="white",
        markeredgecolor="black",
        markersize=9
    )
    for source, marker in source_markers.items()
]

legend1 = ax.legend(
    handles=category_handles,
    title="Traffic Category",
    loc="upper left"
)

ax.add_artist(legend1)

ax.legend(
    handles=source_handles,
    title="RRH Source",
    loc="upper right"
)

# --------------------------------------------------
# 8. ADD MAP TITLE AND AXIS LABELS
# --------------------------------------------------

ax.set_title(
    "Barcelona 5G Planning - RRH Traffic Classification",
    fontsize=15,
    fontweight="bold",
    pad=15
)

ax.set_xlabel("Easting (metres)")
ax.set_ylabel("Northing (metres)")

ax.set_aspect("equal")
ax.grid(True, linestyle="--", alpha=0.3)

# Fit map around study boundary
minx, miny, maxx, maxy = boundary.total_bounds

margin_x = (maxx - minx) * 0.04
margin_y = (maxy - miny) * 0.04

ax.set_xlim(minx - margin_x, maxx + margin_x)
ax.set_ylim(miny - margin_y, maxy + margin_y)

# --------------------------------------------------
# 9. ADD SUMMARY
# --------------------------------------------------

total = len(rrh_gdf)
real_count = (rrh_gdf["source_type"] == "REAL_4G_5G").sum()
synthetic_count = (rrh_gdf["source_type"] == "SYNTHETIC").sum()

category_counts = (
    rrh_gdf["traffic_category"]
    .value_counts()
)

summary = (
    f"Total RRHs: {total} | "
    f"Real: {real_count} | "
    f"Synthetic: {synthetic_count}\n"
    f"Residential: {category_counts.get('Residential', 0)} | "
    f"Office: {category_counts.get('Office', 0)} | "
    f"Mixed: {category_counts.get('Mixed', 0)} | "
    f"Undetermined: {category_counts.get('Undetermined', 0)}"
)

fig.text(
    0.5,
    0.025,
    summary,
    ha="center",
    fontsize=10
)

# --------------------------------------------------
# 10. SAVE MAP
# --------------------------------------------------

plt.tight_layout(rect=[0, 0.07, 1, 1])

plt.savefig(
    OUTPUT_FILE,
    dpi=300,
    bbox_inches="tight"
)

plt.close()

print("======================================")
print("UPDATED TRAFFIC CLASSIFICATION MAP")
print("======================================")
print(f"Total RRHs: {total}")
print(f"Real RRHs: {real_count}")
print(f"Synthetic RRHs: {synthetic_count}")
print("\nCategory counts:")
print(category_counts)
print(f"\nMap saved to: {OUTPUT_FILE}")
