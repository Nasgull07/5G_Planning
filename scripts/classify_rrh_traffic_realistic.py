
import pandas as pd
from pathlib import Path

# ==========================================
# 1. PROJECT PATHS
# ==========================================

PROJECT_DIR = Path(__file__).resolve().parent.parent

RESIDENTIAL_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "residential_density_analysis.csv"
)

BUSINESS_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "rrh_business_activity.csv"
)

SCENARIO_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "scenario_sites.csv"
)

OUTPUT_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "scenario_sites_realistic_classified.csv"
)

# ==========================================
# 2. CLASSIFICATION SETTINGS
# ==========================================

OFFICE_THRESHOLD = 5
OFFICE_RADIUS_METRES = 500

# ==========================================
# 3. CLASSIFICATION FUNCTION
# ==========================================

def classify_traffic(row):

    residential_group = row["residential_density_group"]

    # Missing AMB data must remain undetermined.
    if pd.isna(residential_group):
        return "Undetermined"

    residential = residential_group in ["Medium", "High"]

    office = (
        row["office_count"] >= OFFICE_THRESHOLD
    )

    if residential and office:
        return "Mixed"

    if residential and not office:
        return "Residential"

    if residential_group == "Low" and office:
        return "Office"

    return "Undetermined"


# ==========================================
# 4. MAIN PROCESS
# ==========================================

def main():

    print("======================================")
    print("UPDATED REALISTIC RRH CLASSIFICATION")
    print("======================================")

    print("Loading input datasets...")

    scenario = pd.read_csv(SCENARIO_FILE)
    residential = pd.read_csv(RESIDENTIAL_FILE)
    business = pd.read_csv(BUSINESS_FILE)

    print(f"Scenario RRHs: {len(scenario)}")
    print(f"Residential matches: {len(residential)}")
    print(f"Business records: {len(business)}")

    # ======================================
    # 5. MERGE RESIDENTIAL DATA
    # ======================================

    result = scenario.merge(
        residential[
            [
                "rrh_id",
                "densresi21",
                "perhaban21",
                "residential_density_group",
                "nom",
                "nommuni"
            ]
        ],
        on="rrh_id",
        how="left",
        validate="one_to_one"
    )

    # ======================================
    # 6. MERGE BUSINESS DATA
    # ======================================

    result = result.merge(
        business,
        on="rrh_id",
        how="left",
        validate="one_to_one"
    )

    business_columns = [
        "shop_count",
        "office_count",
        "retail_count",
        "commercial_count",
        "total_business_features"
    ]

    result[business_columns] = (
        result[business_columns].fillna(0)
    )

    # ======================================
    # 7. CREATE EVIDENCE FLAGS
    # ======================================

    result["residential_evidence"] = (
        result["residential_density_group"].isin(
            ["Medium", "High"]
        )
    )

    result["office_evidence"] = (
        result["office_count"] >= OFFICE_THRESHOLD
    )

    # ======================================
    # 8. CLASSIFY TRAFFIC CATEGORY
    # ======================================

    result["traffic_category"] = result.apply(
        classify_traffic,
        axis=1
    )

    # ======================================
    # 9. SAVE RESULTS
    # ======================================

    if len(result) != 400:
        raise ValueError(
            f"Expected 400 RRHs, found {len(result)}"
        )

    if result["rrh_id"].duplicated().any():
        raise ValueError("Duplicate RRH IDs detected.")

    result.to_csv(
        OUTPUT_FILE,
        index=False
    )

    # ======================================
    # 10. SUMMARY
    # ======================================

    print("--------------------------------------")
    print("CLASSIFICATION COMPLETED")
    print("--------------------------------------")

    print(f"Total RRHs: {len(result)}")

    print(
        f"Office threshold: {OFFICE_THRESHOLD} features"
    )

    print(
        f"Office radius: {OFFICE_RADIUS_METRES} metres"
    )

    print("\nTraffic category counts:")

    print(
        result["traffic_category"]
        .value_counts()
        .to_string()
    )

    print("\nClassification by RRH source:")

    print(
        pd.crosstab(
            result["traffic_category"],
            result["source_type"]
        ).to_string()
    )

    print("\nOutput saved to:")
    print(OUTPUT_FILE)


if __name__ == "__main__":
    main()
