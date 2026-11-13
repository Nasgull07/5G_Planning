
import pandas as pd
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = PROJECT_ROOT / "data" / "processed" / "scenario_sites.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "processed" / "scenario_sites_classified.csv"

# Traffic category percentages (simulation assumptions)
TRAFFIC_PERCENTAGES = {
    "Residential": 0.30,
    "Office": 0.30,
    "Mixed": 0.40
}

RANDOM_SEED = 42


def main():
    df = pd.read_csv(INPUT_FILE)

    # Check that the percentages add up to 100%
    if abs(sum(TRAFFIC_PERCENTAGES.values()) - 1.0) > 0.0001:
        raise ValueError("Traffic percentages must add up to 100%.")

    # Assign traffic categories consistently using a fixed seed
    rng = __import__("numpy").random.default_rng(RANDOM_SEED)

    df["traffic_type"] = rng.choice(
        list(TRAFFIC_PERCENTAGES.keys()),
        size=len(df),
        p=list(TRAFFIC_PERCENTAGES.values())
    )

    df.to_csv(OUTPUT_FILE, index=False)

    print("Traffic classification completed successfully.")
    print("\nTraffic category counts:")
    print(df["traffic_type"].value_counts())

    print("\nOutput file:")
    print(OUTPUT_FILE)


if __name__ == "__main__":
    main()
