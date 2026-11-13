
import pandas as pd
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

INPUT_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "scenario_sites_realistic_classified.csv"
)

OUTPUT_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "office_threshold_comparison.csv"
)


def classify(row, threshold):

    residential_group = row["residential_density_group"]

    # Missing AMB coverage: do not assume non-residential.
    if pd.isna(residential_group):
        return "Undetermined"

    residential = residential_group in ["Medium", "High"]
    office = row["office_count"] >= threshold

    if residential and office:
        return "Mixed"

    if residential and not office:
        return "Residential"

    if residential_group == "Low" and office:
        return "Office"

    return "Undetermined"


def main():

    print("======================================")
    print("OFFICE THRESHOLD COMPARISON")
    print("======================================")

    df = pd.read_csv(INPUT_FILE)

    results = []

    for threshold in [3, 5, 10]:

        category = df.apply(
            lambda row: classify(row, threshold),
            axis=1
        )

        print(f"\nOFFICE THRESHOLD: {threshold}")

        print(category.value_counts().to_string())

        print("\nBy RRH source:")

        print(
            pd.crosstab(
                category,
                df["source_type"]
            ).to_string()
        )

        for index, row in df.iterrows():

            results.append({
                "rrh_id": row["rrh_id"],
                "source_type": row["source_type"],
                "office_threshold": threshold,
                "office_count": row["office_count"],
                "traffic_category": category.iloc[index]
            })

    comparison = pd.DataFrame(results)

    comparison.to_csv(
        OUTPUT_FILE,
        index=False
    )

    print("\n======================================")
    print("COMPARISON COMPLETED")
    print("======================================")

    print(f"RRHs analysed: {len(df)}")
    print("Thresholds tested: 3, 5 and 10")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
