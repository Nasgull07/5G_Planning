
import pandas as pd
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

INPUT_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "rrh_residential_indicators.csv"
)

OUTPUT_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "residential_density_analysis.csv"
)


def main():

    df = pd.read_csv(INPUT_FILE)

    column = "densresi21"

    df[column] = pd.to_numeric(
        df[column], errors="coerce"
    )

    df = df.dropna(subset=[column]).copy()

    # Calculate statistical thresholds using terciles.
    low_threshold = df[column].quantile(1 / 3)
    high_threshold = df[column].quantile(2 / 3)

    def classify(value):

        if value <= low_threshold:
            return "Low"

        elif value <= high_threshold:
            return "Medium"

        return "High"

    df["residential_density_group"] = (
        df[column].apply(classify)
    )

    print("\nRESIDENTIAL DENSITY ANALYSIS")
    print("=" * 45)

    print("\nStatistical summary:")

    print(df[column].describe())

    print("\nDensity group counts:")

    print(
        df["residential_density_group"]
        .value_counts()
        .reindex(["Low", "Medium", "High"])
    )

    if "is_synthetic" in df.columns:

        print("\nDensity groups by RRH source:")

        print(
            pd.crosstab(
                df["residential_density_group"],
                df["is_synthetic"]
            )
        )

    df.to_csv(OUTPUT_FILE, index=False)

    print("\nOutput saved to:")
    print(OUTPUT_FILE)


if __name__ == "__main__":
    main()
