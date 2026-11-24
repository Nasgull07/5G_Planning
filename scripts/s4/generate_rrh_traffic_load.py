import argparse
import csv
import math
import os
from pathlib import Path
import tempfile


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_FILE = BASE_DIR / "data" / "processed" / "scenario_sites.csv"
DEFAULT_SCHEDULE_FILE = BASE_DIR / "data" / "input" / "traffic_schedule.csv"
DEFAULT_OUTPUT_FILE = BASE_DIR / "data" / "processed" / "rrh_traffic_load.csv"

DAYS = (
    ("mon", "Monday"),
    ("tue", "Tuesday"),
    ("wed", "Wednesday"),
    ("thu", "Thursday"),
    ("fri", "Friday"),
    ("sat", "Saturday"),
    ("sun", "Sunday"),
)
CATEGORY_PROFILES = {
    "Residential": "residential",
    "Office": "office",
    "Mixed": "mixed",
}
SAMPLES_PER_DAY = 144
MINUTES_PER_SAMPLE = 10
SCENARIO_FIELDS = (
    "rrh_id",
    "traffic_category",
    "capacity_technology",
    "phy_capacity_max_mbps",
    "phy_capacity_max_mbph",
)
OUTPUT_FIELDS = (
    "rrh_id",
    "source_type",
    "technology",
    "is_synthetic",
    "traffic_category",
    "capacity_technology",
    "day_of_week",
    "t_day",
    "time",
    "normalized_traffic",
    "phy_capacity_max_mbps",
    "phy_capacity_max_mbph",
    "traffic_load_mbph",
)


def read_scenario(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        missing = set(SCENARIO_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Scenario is missing required columns: {sorted(missing)}"
            )
        rows = list(reader)

    if not rows:
        raise ValueError("Scenario CSV contains no RRHs.")
    rrh_ids = [row["rrh_id"] for row in rows]
    if any(not rrh_id for rrh_id in rrh_ids) or len(set(rrh_ids)) != len(rrh_ids):
        raise ValueError("RRH IDs must be present and unique.")

    for row in rows:
        if row["traffic_category"] not in CATEGORY_PROFILES:
            raise ValueError(
                f"Unknown traffic category for {row['rrh_id']}: "
                f"{row['traffic_category']!r}"
            )
        if row["capacity_technology"] not in {"4G", "5G"}:
            raise ValueError(
                f"Unknown capacity technology for {row['rrh_id']}: "
                f"{row['capacity_technology']!r}"
            )
        for field in ("phy_capacity_max_mbps", "phy_capacity_max_mbph"):
            try:
                capacity = float(row[field])
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"Invalid {field} for {row['rrh_id']}: {row[field]!r}"
                ) from error
            if not math.isfinite(capacity) or capacity <= 0:
                raise ValueError(
                    f"Invalid {field} for {row['rrh_id']}: {row[field]!r}"
                )
    return rows


def read_schedule(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames or []
        required = {"t_day"}
        required.update(
            f"thp_{day}_xu17_{profile}"
            for day, _ in DAYS
            for profile in CATEGORY_PROFILES.values()
        )
        missing = required - set(fields)
        if missing:
            raise ValueError(
                f"Traffic schedule is missing required columns: "
                f"{sorted(missing)}"
            )
        rows = list(reader)

    if len(rows) != SAMPLES_PER_DAY:
        raise ValueError(
            f"Expected {SAMPLES_PER_DAY} samples per day, found {len(rows)}."
        )
    for index, row in enumerate(rows):
        try:
            t_day = float(row["t_day"])
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Invalid t_day value at sample {index}: {row['t_day']!r}"
            ) from error
        if not math.isclose(
            t_day, index / SAMPLES_PER_DAY, abs_tol=1e-9
        ):
            raise ValueError(
                f"Unexpected t_day value at sample {index}: {t_day}"
            )
        for day, _ in DAYS:
            for profile in CATEGORY_PROFILES.values():
                column = f"thp_{day}_xu17_{profile}"
                try:
                    value = float(row[column])
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        f"Invalid value in {column} at sample {index}."
                    ) from error
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError(
                        f"Normalized value in {column} must be between 0 "
                        f"and 1; found {value} at sample {index}."
                    )
    return rows


def generate_traffic_load(
    scenario_file=DEFAULT_SCENARIO_FILE,
    schedule_file=DEFAULT_SCHEDULE_FILE,
    output_file=DEFAULT_OUTPUT_FILE
):
    scenario_file = Path(scenario_file)
    schedule_file = Path(schedule_file)
    output_file = Path(output_file)
    if output_file.resolve() in {
        scenario_file.resolve(),
        schedule_file.resolve(),
    }:
        raise ValueError("Traffic-load output must not overwrite an input file.")

    scenario = read_scenario(scenario_file)
    schedule = read_schedule(schedule_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_file.parent,
        prefix=f".{output_file.name}.",
        suffix=".tmp"
    )
    temporary_file = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8-sig", newline=""
        ) as destination:
            writer = csv.DictWriter(destination, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            for rrh in scenario:
                category_profile = CATEGORY_PROFILES[rrh["traffic_category"]]
                capacity_mbps = float(rrh["phy_capacity_max_mbps"])
                capacity_mbph = float(rrh["phy_capacity_max_mbph"])
                for day, day_name in DAYS:
                    profile_column = (
                        f"thp_{day}_xu17_{category_profile}"
                    )
                    for index, sample in enumerate(schedule):
                        normalized_traffic = float(sample[profile_column])
                        minute_of_day = index * MINUTES_PER_SAMPLE
                        writer.writerow({
                            "rrh_id": rrh["rrh_id"],
                            "source_type": rrh.get("source_type", ""),
                            "technology": rrh.get("technology", ""),
                            "is_synthetic": rrh.get("is_synthetic", ""),
                            "traffic_category": rrh["traffic_category"],
                            "capacity_technology": rrh["capacity_technology"],
                            "day_of_week": day_name,
                            "t_day": sample["t_day"],
                            "time": (
                                f"{minute_of_day // 60:02d}:"
                                f"{minute_of_day % 60:02d}"
                            ),
                            "normalized_traffic": (
                                f"{normalized_traffic:.12g}"
                            ),
                            "phy_capacity_max_mbps": (
                                f"{capacity_mbps:.6f}"
                            ),
                            "phy_capacity_max_mbph": (
                                f"{capacity_mbph:.6f}"
                            ),
                            "traffic_load_mbph": (
                                f"{normalized_traffic * capacity_mbph:.6f}"
                            ),
                        })
        os.replace(temporary_file, output_file)
    finally:
        temporary_file.unlink(missing_ok=True)

    return len(scenario) * len(DAYS) * len(schedule)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Scale normalized Xu17 traffic profiles by each RRH's maximum "
            "PHY capacity and write a long-form load CSV."
        )
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        default=DEFAULT_SCENARIO_FILE,
        help="Scenario CSV with traffic category and capacity columns."
    )
    parser.add_argument(
        "--schedule",
        type=Path,
        default=DEFAULT_SCHEDULE_FILE,
        help="Reduced Xu17 traffic schedule CSV."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help="Long-form traffic-load output CSV."
    )
    args = parser.parse_args()

    row_count = generate_traffic_load(
        scenario_file=args.scenario,
        schedule_file=args.schedule,
        output_file=args.output
    )
    print(f"Generated {row_count} RRH/day/time samples.")
    print(f"Saved traffic loads: {args.output}")


if __name__ == "__main__":
    main()
