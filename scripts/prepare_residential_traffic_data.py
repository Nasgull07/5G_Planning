import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import geopandas as gpd
import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data" / "processed"

SCENARIO_FILE = DATA_DIR / "scenario_sites.csv"
SCENARIO_400_FILE = DATA_DIR / "rrh_scenario_400.csv"
AMB_SHAPEFILE = (
    BASE_DIR / "data" / "input" / "amb_residential"
    / "indicadors_hab_entorn_res.shp"
)

INDICATORS_FILE = DATA_DIR / "rrh_residential_indicators.csv"
UNMATCHED_FILE = DATA_DIR / "rrh_without_amb_coverage.csv"
SUMMARY_FILE = DATA_DIR / "residential_analysis_summary.csv"
DENSITY_REFERENCE_FILE = DATA_DIR / "residential_density_analysis.csv"
AMB_400_FILE = DATA_DIR / "amb_residential_coverage_400.csv"

AMB_LAYER_URL = (
    "https://ide.amb.cat/geoserveis/rest/services/"
    "indicadors_habitatge/MapServer/24/query"
)
METRIC_CRS = "EPSG:25831"


def prepare_residential_indicators():
    if not SCENARIO_FILE.exists():
        raise FileNotFoundError(SCENARIO_FILE)
    if not AMB_SHAPEFILE.exists():
        raise FileNotFoundError(AMB_SHAPEFILE)

    rrhs = pd.read_csv(SCENARIO_FILE)
    required = {"rrh_id", "latitude", "longitude"}
    missing = required - set(rrhs.columns)
    if missing:
        raise ValueError(
            f"Scenario is missing columns: {sorted(missing)}"
        )

    rrhs["latitude"] = pd.to_numeric(
        rrhs["latitude"], errors="coerce"
    )
    rrhs["longitude"] = pd.to_numeric(
        rrhs["longitude"], errors="coerce"
    )
    rrhs = rrhs.dropna(
        subset=["latitude", "longitude"]
    ).copy()
    rrhs = gpd.GeoDataFrame(
        rrhs,
        geometry=gpd.points_from_xy(
            rrhs["longitude"], rrhs["latitude"]
        ),
        crs="EPSG:4326"
    )

    amb = gpd.read_file(AMB_SHAPEFILE)
    if amb.crs is None:
        raise ValueError("AMB dataset has no coordinate reference system.")
    if "geometry" not in amb.columns:
        raise ValueError("AMB dataset has no geometry column.")

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

    joined = gpd.sjoin(
        rrhs.to_crs(amb.crs),
        amb_subset,
        how="left",
        predicate="within"
    )
    matched = joined[joined["index_right"].notna()].copy()
    unmatched = joined[joined["index_right"].isna()].copy()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        matched.drop(columns="geometry")
    ).to_csv(INDICATORS_FILE, index=False)
    pd.DataFrame(
        unmatched.drop(
            columns=["geometry", "index_right"],
            errors="ignore"
        )
    ).to_csv(UNMATCHED_FILE, index=False)

    total = len(matched) + len(unmatched)
    summary = pd.DataFrame([
        {"metric": "Total RRHs", "value": total},
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
            "value": round(len(matched) / total * 100, 2) if total else 0
        }
    ])
    summary.to_csv(SUMMARY_FILE, index=False)
    print(summary.to_string(index=False))
    print("Saved:", INDICATORS_FILE)
    print("Saved:", UNMATCHED_FILE)
    print("Saved:", SUMMARY_FILE)


def prepare_density_reference():
    if not INDICATORS_FILE.exists():
        raise FileNotFoundError(INDICATORS_FILE)

    df = pd.read_csv(INDICATORS_FILE)
    if "densresi21" not in df.columns:
        raise ValueError(
            f"{INDICATORS_FILE} is missing the densresi21 column."
        )

    df["densresi21"] = pd.to_numeric(
        df["densresi21"], errors="coerce"
    )
    df = df.dropna(subset=["densresi21"]).copy()
    if df.empty:
        raise ValueError("No valid residential density values.")

    low_threshold = df["densresi21"].quantile(1 / 3)
    high_threshold = df["densresi21"].quantile(2 / 3)

    def classify(value):
        if value <= low_threshold:
            return "Low"
        if value <= high_threshold:
            return "Medium"
        return "High"

    df["residential_density_group"] = (
        df["densresi21"].apply(classify)
    )
    df.to_csv(DENSITY_REFERENCE_FILE, index=False)

    print("Residential density thresholds:")
    print(f"Low / Medium boundary: {low_threshold:.4f}")
    print(f"Medium / High boundary: {high_threshold:.4f}")
    print("Saved:", DENSITY_REFERENCE_FILE)


def prepare_amb_coverage_400():
    if not SCENARIO_400_FILE.exists():
        raise FileNotFoundError(SCENARIO_400_FILE)

    params = {
        "where": "1=1",
        "outFields": "cod_barri,nom,codi_ine,nommuni,densresi21",
        "returnGeometry": "true",
        "outSR": "25831",
        "f": "geojson"
    }
    request = Request(
        AMB_LAYER_URL + "?" + urlencode(params),
        headers={"User-Agent": "Barcelona-CRAN-Research/1.0"}
    )

    print("Downloading official AMB residential layer...")
    with urlopen(request, timeout=90) as response:
        data = json.loads(response.read().decode("utf-8"))

    if "error" in data:
        raise RuntimeError(f"AMB service error: {data['error']}")
    features = data.get("features", [])
    if not features:
        raise ValueError(
            "The AMB service returned no residential polygons."
        )

    residential = gpd.GeoDataFrame.from_features(
        features,
        crs=METRIC_CRS
    )
    required_fields = [
        "cod_barri",
        "nom",
        "codi_ine",
        "nommuni",
        "densresi21"
    ]
    missing = [
        field for field in required_fields
        if field not in residential.columns
    ]
    if missing:
        raise ValueError(f"Missing AMB fields: {missing}")

    residential = residential[
        required_fields + ["geometry"]
    ].copy()
    residential = residential[
        residential.geometry.notna()
        & ~residential.geometry.is_empty
    ].copy()
    if residential.empty:
        raise ValueError("Residential polygon layer is empty.")

    rrhs = pd.read_csv(
        SCENARIO_400_FILE,
        dtype={"rrh_id": "string"}
    )
    if len(rrhs) != 400:
        raise ValueError(
            f"Expected 400 RRHs, found {len(rrhs)}."
        )
    if rrhs["rrh_id"].duplicated().any():
        raise ValueError("Duplicate RRH IDs found.")

    points = gpd.GeoDataFrame(
        rrhs.copy(),
        geometry=gpd.points_from_xy(
            rrhs["longitude"], rrhs["latitude"]
        ),
        crs="EPSG:4326"
    ).to_crs(METRIC_CRS)

    joined = gpd.sjoin(
        points,
        residential,
        how="left",
        predicate="within"
    )
    duplicates = joined[
        joined["rrh_id"].duplicated(keep=False)
    ]
    if not duplicates.empty:
        raise ValueError(
            "Some RRHs match multiple residential polygons."
        )

    joined = joined.drop(columns=["index_right"], errors="ignore")
    joined["amb_residential_match"] = (
        joined["densresi21"].notna()
    )

    summary = (
        joined.groupby("source_type", dropna=False)
        .agg(
            total_rrhs=("rrh_id", "count"),
            matched=("amb_residential_match", "sum")
        )
    )
    summary["not_matched"] = (
        summary["total_rrhs"] - summary["matched"]
    )
    summary["match_percentage"] = (
        summary["matched"] / summary["total_rrhs"] * 100
    ).round(2)
    print(summary.to_string())

    output = joined.drop(columns=["geometry"], errors="ignore")
    output.to_csv(AMB_400_FILE, index=False)
    print("Saved:", AMB_400_FILE)
    print("Total RRHs:", len(output))
    print("Matched:", int(output["amb_residential_match"].sum()))


def main():
    parser = argparse.ArgumentParser(
        description="Prepare residential inputs for traffic classification."
    )
    parser.add_argument(
        "--stage",
        choices=["all", "indicators", "density", "coverage-400"],
        default="all",
        help=(
            "Run all preparation stages or one stage. "
            "'all' requires both AMB sources."
        )
    )
    stage = parser.parse_args().stage

    if stage in ("all", "indicators"):
        prepare_residential_indicators()
    if stage in ("all", "density"):
        prepare_density_reference()
    if stage in ("all", "coverage-400"):
        prepare_amb_coverage_400()


if __name__ == "__main__":
    main()
