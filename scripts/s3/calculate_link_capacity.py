import argparse
import csv
import os
from pathlib import Path
import tempfile


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_FILE = BASE_DIR / "data" / "processed" / "scenario_sites.csv"

LTE_N_RB = 100
LTE_CQI_EFFICIENCY = 5.5547
LTE_MIMO_LAYERS = 2
LTE_ANTENNA_LAYERS = 2

NR_N_PRB = 273
NR_NUMEROLOGY = 1
NR_Q_M = 8
NR_R_MAX = 948 / 1024
NR_TDD_DL_FRACTION = 0.75
NR_OVERHEAD = 0.14
NR_MIMO_LAYERS = 4

CAPACITY_FIELDS = (
    "capacity_technology",
    "phy_capacity_max_mbps",
    "phy_capacity_max_mbph",
)


def calculate_lte_overhead(
    n_rb=LTE_N_RB,
    antenna_layers=LTE_ANTENNA_LAYERS
):
    x = 2 ** antenna_layers
    crs_rb = 4 * antenna_layers
    overhead_resources = (
        crs_rb * 20 * n_rb
        + (24 - x) * 10 * n_rb
        + 6 * (24 + 24 + 48 - x)
    )
    total_resources = 12 * n_rb * 7 * 20
    return overhead_resources / total_resources


def calculate_lte_capacity_mbps():
    overhead = calculate_lte_overhead()
    capacity_per_layer = (
        LTE_N_RB
        * 12
        * 14
        * 10**3
        * LTE_CQI_EFFICIENCY
        * (1 - overhead)
        * 10**-6
    )
    return capacity_per_layer * LTE_MIMO_LAYERS


def calculate_nr_capacity_mbps():
    symbol_duration = 10**-3 / (14 * 2**NR_NUMEROLOGY)
    capacity_per_layer = (
        NR_Q_M
        * NR_TDD_DL_FRACTION
        * NR_R_MAX
        * (NR_N_PRB * 12 / symbol_duration)
        * (1 - NR_OVERHEAD)
        * 10**-6
    )
    return capacity_per_layer * NR_MIMO_LAYERS


def capacity_technology(technology, is_synthetic):
    if str(is_synthetic).strip().lower() in {"true", "1", "yes"}:
        return "4G"
    if technology in {"4G", "5G"}:
        return technology
    raise ValueError(
        f"Unsupported technology for a real RRH: {technology!r}"
    )


def update_scenario_capacities(scenario_file):
    scenario_file = Path(scenario_file)
    with scenario_file.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fieldnames = reader.fieldnames
        required = {"rrh_id", "technology", "is_synthetic"}
        missing = required - set(fieldnames or [])
        if missing:
            raise ValueError(
                f"Scenario is missing required columns: {sorted(missing)}"
            )
        rows = list(reader)
    if not rows:
        raise ValueError("Scenario CSV contains no RRHs.")

    lte_capacity = calculate_lte_capacity_mbps()
    nr_capacity = calculate_nr_capacity_mbps()
    capacities = {
        "4G": lte_capacity,
        "5G": nr_capacity,
    }

    output_fields = list(fieldnames or [])
    output_fields.extend(
        field for field in CAPACITY_FIELDS if field not in output_fields
    )
    for row in rows:
        tech = capacity_technology(row["technology"], row["is_synthetic"])
        capacity_mbps = capacities[tech]
        row["capacity_technology"] = tech
        row["phy_capacity_max_mbps"] = f"{capacity_mbps:.6f}"
        row["phy_capacity_max_mbph"] = (
            f"{capacity_mbps * 3600 / 8:.6f}"
        )

    descriptor, temporary_name = tempfile.mkstemp(
        dir=scenario_file.parent,
        prefix=f".{scenario_file.name}.",
        suffix=".tmp"
    )
    temporary_file = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8-sig", newline=""
        ) as destination:
            writer = csv.DictWriter(destination, fieldnames=output_fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_file, scenario_file)
    finally:
        temporary_file.unlink(missing_ok=True)

    return lte_capacity, nr_capacity


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Calculate theoretical LTE/NR PHY capacity for each RRH "
            "and update the scenario CSV in place."
        )
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        default=DEFAULT_SCENARIO_FILE,
        help=(
            "Scenario CSV to update "
            "(default: data/processed/scenario_sites.csv)."
        )
    )
    args = parser.parse_args()

    lte_capacity, nr_capacity = update_scenario_capacities(args.scenario)
    print(
        f"LTE maximum: {lte_capacity:.3f} Mbps "
        f"({lte_capacity * 3600 / 8:.3f} MB/h)"
    )
    print(
        f"NR maximum: {nr_capacity:.3f} Mbps "
        f"({nr_capacity * 3600 / 8:.3f} MB/h)"
    )
    print("Synthetic RRHs use LTE capacity.")
    print(f"Updated scenario: {args.scenario}")


if __name__ == "__main__":
    main()
