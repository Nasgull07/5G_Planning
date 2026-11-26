#!/usr/bin/env python3
"""
Plot RRH counts for every BBU pool across the test2 allocation scenarios,
separated by territory.

The script searches test2/output for bbu_pool_*_locations.csv files,
uses num_assigned_rrhs, and writes one ordered line chart per territory.
"""

import argparse
import re
from pathlib import Path
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def scenario_name(folder: Path) -> str:
    match = re.search(r"_k(\d+)", folder.name, flags=re.IGNORECASE)
    if match:
        return f"K={match.group(1)}"
    return folder.name


def scenario_sort_key(label: str) -> int:
    match = re.search(r"K=(\d+)", label)
    return int(match.group(1)) if match else -1


def territory_label(name: str) -> str:
    if "Zona 1" in name:
        return "Zone 1 - Rest of area"
    if "Zona 2" in name:
        return "Zone 2 - Barcelona"
    return name


def load_scenarios(output_dir: Path) -> dict[str, dict[str, pd.Series]]:
    scenarios: dict[str, dict[str, pd.Series]] = {}

    for folder in sorted(output_dir.iterdir(), key=lambda path: path.name):
        if not folder.is_dir():
            continue

        location_files = list(folder.glob("bbu_pool_*_locations.csv"))
        if not location_files:
            continue
        if len(location_files) > 1:
            raise RuntimeError(
                f"Expected one BBU location CSV in '{folder}', "
                f"found {len(location_files)}."
            )

        location_file = location_files[0]
        locations = pd.read_csv(
            location_file,
            dtype={"bbu_pool": str, "territory_name": str},
        )
        required = {"bbu_pool", "territory_name", "num_assigned_rrhs"}
        if not required.issubset(locations.columns):
            raise ValueError(
                f"{location_file} must contain {sorted(required)} columns."
            )

        locations = locations.dropna(
            subset=["bbu_pool", "territory_name", "num_assigned_rrhs"]
        )
        locations["territory_name"] = locations["territory_name"].map(
            territory_label
        )

        territory_counts: dict[str, pd.Series] = {}
        for territory, territory_locations in locations.groupby(
            "territory_name",
            sort=True,
        ):
            counts = territory_locations["num_assigned_rrhs"].sort_values(
                ascending=False,
                kind="stable",
            )
            territory_counts[str(territory)] = counts.reset_index(
                drop=True
            ).rename("rrh_count")

        scenarios[scenario_name(folder)] = territory_counts

    if not scenarios:
        raise FileNotFoundError(
            f"No bbu_pool_*_locations.csv files found under '{output_dir}'."
        )
    return scenarios


def build_long_summary(
    scenarios: dict[str, dict[str, pd.Series]],
) -> pd.DataFrame:
    rows: list[dict] = []
    for scenario, territories in scenarios.items():
        for territory, counts in territories.items():
            for rank, count in enumerate(counts, start=1):
                rows.append({
                    "scenario": scenario,
                    "zone": territory,
                    "position": rank,
                    "num_rrhs": int(count),
                })
    return pd.DataFrame(rows)


def plot_summary(
    summary: pd.DataFrame,
    output_png: Path,
    output_csv: Path,
) -> None:
    territories = sorted(summary["zone"].unique())
    scenarios = sorted(summary["scenario"].unique(), key=scenario_sort_key)

    fig, axes = plt.subplots(
        1,
        len(territories),
        figsize=(8 * len(territories), 7),
        sharey=True,
    )
    if len(territories) == 1:
        axes = [axes]

    for axis, territory in zip(axes, territories):
        territory_rows = summary[summary["zone"] == territory]
        max_rank = int(territory_rows["position"].max())

        for scenario in scenarios:
            rows = territory_rows[
                territory_rows["scenario"] == scenario
            ].sort_values("position")
            if rows.empty:
                continue
            axis.plot(
                rows["position"],
                rows["num_rrhs"],
                marker="o",
                linewidth=2,
                label=scenario,
            )

        axis.set_title(territory)
        axis.set_xlabel("BBU pools ranked by load (highest to lowest)")
        axis.set_ylabel("Number of RRHs")
        axis.grid(True, linestyle="--", alpha=0.35)
        axis.set_xticks(range(1, max_rank + 1))
        axis.legend(title="Scenario")

    fig.suptitle("Number of RRHs per BBU pool and zone", fontsize=14)
    fig.tight_layout()

    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=180, bbox_inches="tight")
    plt.close(fig)

    summary.to_csv(output_csv, index=False)


def parse_args() -> argparse.Namespace:
    test2_dir = Path('C:/Users/monop/OneDrive/Escritorio/project_5gplan/scripts/s2')
    parser = argparse.ArgumentParser(
        description="Plot RRH counts by BBU pool and territory."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=test2_dir / "output",
        help="Directory containing the scenario folders.",
    )
    parser.add_argument(
        "--png",
        type=Path,
        default=test2_dir / "rrh_count_by_bbu_pool.png",
        help="Destination PNG file.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=test2_dir / "rrh_count_by_bbu_pool.csv",
        help="Destination ordered data CSV.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scenarios = load_scenarios(args.output_dir)
    summary = build_long_summary(scenarios)
    plot_summary(summary, args.png, args.csv)
    print(f"Line chart: {args.png}")
    print(f"Ordered data: {args.csv}")


if __name__ == "__main__":
    main()
