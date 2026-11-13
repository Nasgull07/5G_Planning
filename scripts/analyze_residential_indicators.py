
import pandas as pd
import geopandas as gpd
from pathlib import Path

# =====================================================
# 1. PROJECT PATHS
# =====================================================

PROJECT_DIR = Path(__file__).resolve().parent.parent

RRH_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "scenario_sites.csv"
)

AMB_FILE = (
    PROJECT_DIR / "data" / "input"
    / "amb_residential"
    / "indicadors_hab_entorn_res.shp"
)

OUTPUT_DIR = PROJECT_DIR / "data" / "processed"

MATCHED_FILE = OUTPUT_DIR / "rrh_residential_indicators.csv"
UNMATCHED_FILE = OUTPUT_DIR / "rrh_without_amb_coverage.csv"
SUMMARY_FILE = OUTPUT_DIR / "residential_analysis_summary.csv"


# =====================================================
# 2. LOAD RRH DATA
# =====================================================

def load_rrhs():

    print("Loading RRH dataset...")

    if not RRH_FILE.exists():
        raise FileNotFoundError(
            f"RRH dataset not found: {RRH_FILE}"
        )

    df = pd.read_csv(RRH_FILE)

    required = {"rrh_id", "latitude", "longitude"}

    if not required.issubset(df.columns):
        raise ValueError(
            "RRH dataset is missing required columns."
        )

    df["latitude"] = pd.to_numeric(
        df["latitude"], errors="coerce"
    )

    df["longitude"] = pd.to_numeric(
        df["longitude"], errors="coerce"
    )

    df = df.dropna(
        subset=["latitude", "longitude"]
    ).copy()

    # Convert geographic coordinates into points.
    # CSV coordinates are assumed to be WGS84.
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
# 3. LOAD AMB RESIDENTIAL DATA
# =====================================================

def load_amb():

    print("Loading AMB neighbourhood polygons...")

    if not AMB_FILE.exists():
        raise FileNotFoundError(
            f"AMB Shapefile not found: {AMB_FILE}"
        )

    amb = gpd.read_file(AMB_FILE)

    if amb.crs is None:
        raise ValueError(
            "AMB dataset has no coordinate reference system."
        )

    if "geometry" not in amb.columns:
        raise ValueError(
            "AMB dataset has no geometry column."
        )

    return amb


# =====================================================
# 4. MATCH RRHs WITH AMB NEIGHBOURHOODS
# =====================================================

def match_rrhs_with_amb(rrhs, amb):

    print("Matching RRHs with AMB neighbourhoods...")

    # Convert RRHs to the same CRS as the AMB polygons.
    rrhs_projected = rrhs.to_crs(amb.crs)

    # Keep only useful residential indicator fields.
    indicator_fields = [
        "cod_barri",
        "nom",
        "codi_ine",
        "nommuni",
        "densresi21",
        "perhaban21",
        "sup_hb_21",
        "conllog_21",
        "llogmes_21",
        "llog_m2_21"
    ]

    available_fields = [
        field for field in indicator_fields
        if field in amb.columns
    ]

    amb_subset = amb[available_fields + ["geometry"]].copy()

    # Spatial join: find the neighbourhood containing each RRH.
    matched = gpd.sjoin(
        rrhs_projected,
        amb_subset,
        how="left",
        predicate="within"
    )

    # Separate matched and unmatched locations.
    matched_rows = matched[
        matched["index_right"].notna()
    ].copy()

    unmatched_rows = matched[
        matched["index_right"].isna()
    ].copy()

    return matched_rows, unmatched_rows


# =====================================================
# 5. SAVE RESULTS
# =====================================================

def save_results(matched, unmatched):

    print("Saving analysis results...")

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # Convert matched data to an ordinary CSV.
    matched_csv = pd.DataFrame(
        matched.drop(columns="geometry")
    )

    matched_csv.to_csv(
        MATCHED_FILE,
        index=False
    )

    unmatched_csv = pd.DataFrame(
        unmatched.drop(
            columns=["geometry", "index_right"],
            errors="ignore"
        )
    )

    unmatched_csv.to_csv(
        UNMATCHED_FILE,
        index=False
    )

    # Create summary.
    total = len(matched) + len(unmatched)

    summary = pd.DataFrame([
        {
            "metric": "Total RRHs",
            "value": total
        },
        {
            "metric": "RRHs matched to AMB neighbourhoods",
            "value": len(matched)
        },
        {
            "metric": "RRHs outside AMB coverage",
            "value": len(unmatched)
        },
        {
            "metric": "Coverage percentage",
            "value": round(
                len(matched) / total * 100, 2
            ) if total else 0
        }
    ])

    summary.to_csv(
        SUMMARY_FILE,
        index=False
    )

    return summary


# =====================================================
# 6. MAIN PROGRAM
# =====================================================

def main():

    print("\n======================================")
    print("AMB RESIDENTIAL INDICATOR ANALYSIS")
    print("======================================\n")

    rrhs = load_rrhs()

    amb = load_amb()

    matched, unmatched = match_rrhs_with_amb(
        rrhs,
        amb
    )

    summary = save_results(
        matched,
        unmatched
    )

    print("\nANALYSIS RESULTS")
    print("--------------------------------------")

    print(summary.to_string(index=False))

    print("\nMatched RRH sample:")

    display_fields = [
        field for field in [
            "rrh_id",
            "nommuni",
            "nom",
            "densresi21",
            "perhaban21"
        ]
        if field in matched.columns
    ]

    print(
        matched[display_fields].head(10).to_string(
            index=False
        )
    )

    print("\nOUTPUT FILES")
    print("--------------------------------------")
    print(MATCHED_FILE)
    print(UNMATCHED_FILE)
    print(SUMMARY_FILE)

    print("\nResidential analysis completed successfully.")


if __name__ == "__main__":
    main()
