import argparse
import math
from pathlib import Path
import random

import pandas as pd

if __package__:
    from .generate_traffic_map import (
        BASE_DIR,
        BOUNDARY_FILE,
        DATA_DIR,
        SCENARIO_FILE,
        TRAFFIC_CATEGORIES,
        create_map,
        load_scenario,
    )
else:
    from scripts.s2.generate_traffic_map import (
        BASE_DIR,
        BOUNDARY_FILE,
        DATA_DIR,
        SCENARIO_FILE,
        TRAFFIC_CATEGORIES,
        create_map,
        load_scenario,
    )


def calculate_category_counts(total, percentages):
    if total < 0:
        raise ValueError("The number of RRHs cannot be negative.")
    if set(percentages) != set(TRAFFIC_CATEGORIES):
        raise ValueError(
            f"Percentages must be provided for {TRAFFIC_CATEGORIES}."
        )
    for category, percentage in percentages.items():
        if not math.isfinite(percentage) or not 0 <= percentage <= 100:
            raise ValueError(
                f"{category} percentage must be between 0 and 100."
            )
    if not math.isclose(sum(percentages.values()), 100, abs_tol=1e-8):
        raise ValueError("Traffic percentages must add up to 100.")

    exact_counts = {
        category: total * percentages[category] / 100
        for category in TRAFFIC_CATEGORIES
    }
    counts = {
        category: math.floor(exact_counts[category])
        for category in TRAFFIC_CATEGORIES
    }
    remaining = total - sum(counts.values())
    remainder_order = sorted(
        TRAFFIC_CATEGORIES,
        key=lambda category: exact_counts[category] - counts[category],
        reverse=True
    )
    for category in remainder_order[:remaining]:
        counts[category] += 1
    return counts


def assign_random_categories(
    scenario,
    residential_percent,
    office_percent,
    mixed_percent,
    seed=None
):
    percentages = {
        "Residential": residential_percent,
        "Office": office_percent,
        "Mixed": mixed_percent
    }
    counts = calculate_category_counts(len(scenario), percentages)
    categories = [
        category
        for category in TRAFFIC_CATEGORIES
        for _ in range(counts[category])
    ]
    random.Random(seed).shuffle(categories)

    result = scenario.copy()
    result["traffic_category"] = categories
    result["assignment_mode"] = "random"
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Randomly assign traffic types and create a shape-based map."
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        default=SCENARIO_FILE,
        help="Scenario CSV containing rrh_id, latitude, and longitude."
    )
    parser.add_argument(
        "--residential-percent",
        type=float,
        required=True,
        help="Percentage of RRHs assigned to Residential."
    )
    parser.add_argument(
        "--office-percent",
        type=float,
        required=True,
        help="Percentage of RRHs assigned to Office."
    )
    parser.add_argument(
        "--mixed-percent",
        type=float,
        required=True,
        help="Percentage of RRHs assigned to Mixed."
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Optional seed to reproduce the same random assignment."
    )
    parser.add_argument("--boundary", type=Path)
    parser.add_argument("--output-csv", type=Path)
    parser.add_argument("--output-map", type=Path)
    args = parser.parse_args()

    scenario = load_scenario(args.scenario)
    classification = assign_random_categories(
        scenario,
        residential_percent=args.residential_percent,
        office_percent=args.office_percent,
        mixed_percent=args.mixed_percent,
        seed=args.seed
    )
    output_csv = args.output_csv or (
        DATA_DIR / f"{args.scenario.stem}_traffic_random.csv"
    )
    output_map = args.output_map or (
        BASE_DIR / "output"
        / f"{args.scenario.stem}_traffic_random_interactive.html"
    )
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    classification.to_csv(output_csv, index=False)
    print("Random traffic assignment:")
    print(
        classification["traffic_category"]
        .value_counts()
        .reindex(TRAFFIC_CATEGORIES, fill_value=0)
        .to_string()
    )
    print("Saved classification:", output_csv)
    print("Random seed:", args.seed if args.seed is not None else "not fixed")
    create_map(
        classification,
        boundary_file=args.boundary or BOUNDARY_FILE,
        output_file=output_map
    )


if __name__ == "__main__":
    main()
