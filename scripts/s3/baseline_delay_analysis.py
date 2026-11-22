#!/usr/bin/env python3
"""Generate weekday traffic and BBU Pool baseline reports for Phase 2."""

import argparse
import json
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_FILE = BASE_DIR / "data" / "processed" / "scenario_sites.csv"
DEFAULT_SCHEDULE_FILE = BASE_DIR / "data" / "input" / "traffic_schedule.csv"
DEFAULT_LOAD_FILE = BASE_DIR / "data" / "processed" / "rrh_traffic_load.csv"
DEFAULT_ASSIGNMENT_FILE = (
    BASE_DIR
    / "results"
    / "phase2"
    / "baseline"
    / "rrh_bbu_k30_s100_kmedian_assignments.csv"
)
DEFAULT_OUTPUT_DIR = BASE_DIR / "results" / "phase2" / "baseline"

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri")
WEEKDAY_NAMES = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}
DAY_NAMES = {
    "Monday": "mon",
    "Tuesday": "tue",
    "Wednesday": "wed",
    "Thursday": "thu",
    "Friday": "fri",
    "Saturday": "sat",
    "Sunday": "sun",
}
PROFILE_COLUMNS = {
    "Residential": "residential",
    "Office": "office",
    "Mixed": "mixed",
}
SAMPLES_PER_DAY = 144
MINUTES_PER_SAMPLE = 10
REPORT_HOURS = ("08:00", "15:00", "21:00")
PROFILE_COLORS = {
    "Residential": "#d55e00",
    "Office": "#0072b2",
    "Mixed": "#009e73",
}


def _time_label(sample_index):
    minute_of_day = sample_index * MINUTES_PER_SAMPLE
    return f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"


def _site_id_from_rrh_id(rrh_id):
    value = str(rrh_id).strip()
    if value.startswith("[") and value.endswith("]"):
        parsed = json.loads(value)
        if not isinstance(parsed, list) or not parsed:
            raise ValueError(f"Invalid source RRH ID: {rrh_id!r}")
        return str(parsed[0]).strip()
    return value


def build_assignment_map(scenario, assignment):
    """Map original scenario RRH IDs to the BBU Pool IDs from Phase 1."""
    required_scenario = {"rrh_id"}
    required_assignment = {"site_id"}
    missing_scenario = required_scenario - set(scenario.columns)
    if missing_scenario:
        raise ValueError(
            f"Scenario is missing columns: {sorted(missing_scenario)}"
        )
    if "bbu_pool_id" in assignment.columns:
        pool_column = "bbu_pool_id"
    elif "bbu_pool" in assignment.columns:
        pool_column = "bbu_pool"
    else:
        required_assignment.add("bbu_pool")
        raise ValueError(
            f"Assignment is missing columns: {sorted(required_assignment)}"
        )
    missing_assignment = required_assignment - set(assignment.columns)
    if missing_assignment:
        raise ValueError(
            f"Assignment is missing columns: {sorted(missing_assignment)}"
        )

    site_map = scenario[["rrh_id"]].copy()
    site_map["rrh_id"] = site_map["rrh_id"].astype(str)
    site_map["site_id"] = site_map["rrh_id"].map(_site_id_from_rrh_id)
    if site_map["rrh_id"].duplicated().any():
        raise ValueError("Scenario RRH IDs must be unique.")

    pool_map = assignment[["site_id", pool_column]].copy()
    pool_map["site_id"] = pool_map["site_id"].astype(str)
    pool_map[pool_column] = pool_map[pool_column].astype(str)
    pool_map = pool_map.rename(columns={pool_column: "bbu_pool_id"})

    mapped = site_map.merge(
        pool_map,
        on="site_id",
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    unmapped = mapped["_merge"] != "both"
    if unmapped.any():
        missing_ids = mapped.loc[unmapped, "site_id"].head(5).tolist()
        raise ValueError(
            "Some scenario RRHs have no initial BBU Pool assignment; "
            f"example site IDs: {missing_ids}"
        )
    return mapped[["rrh_id", "bbu_pool_id"]]


def merge_loads_with_assignment(loads, assignment_map):
    """Merge assignments in memory without dropping or multiplying load rows."""
    if loads["rrh_id"].isna().any():
        raise ValueError("Traffic-load data contains empty RRH IDs.")
    if assignment_map["rrh_id"].duplicated().any():
        raise ValueError("Assignment map contains duplicate RRH IDs.")

    merged = loads.merge(
        assignment_map,
        on="rrh_id",
        how="left",
        validate="many_to_one",
        indicator=True,
        sort=False,
    )
    if len(merged) != len(loads):
        raise ValueError(
            "Assignment merge changed the traffic-load row count: "
            f"{len(loads)} -> {len(merged)}."
        )
    if (merged["_merge"] != "both").any():
        missing_ids = (
            merged.loc[merged["_merge"] != "both", "rrh_id"]
            .drop_duplicates()
            .head(5)
            .tolist()
        )
        raise ValueError(
            f"Traffic-load RRHs have no assignment; example IDs: {missing_ids}"
        )
    return merged.drop(columns="_merge")


def validate_load_coverage(loads, assignment_map):
    expected_rrhs = set(assignment_map["rrh_id"])
    observed_rrhs = set(loads["rrh_id"].dropna())
    if observed_rrhs != expected_rrhs:
        raise ValueError(
            "Traffic-load RRHs do not match the scenario RRHs; "
            f"missing={len(expected_rrhs - observed_rrhs)}, "
            f"unexpected={len(observed_rrhs - expected_rrhs)}."
        )

    sample_keys = ["rrh_id", "day_of_week", "time"]
    if loads.duplicated(sample_keys).any():
        raise ValueError(
            "Traffic-load data contains duplicate RRH/day/time samples."
        )
    expected_days = set(DAY_NAMES)
    if set(loads["day_of_week"]) != expected_days:
        raise ValueError(
            "Traffic-load data must contain exactly Monday through Sunday."
        )
    expected_times = {
        _time_label(index) for index in range(SAMPLES_PER_DAY)
    }
    if set(loads["time"]) != expected_times:
        raise ValueError(
            "Traffic-load data must contain all 144 ten-minute samples."
        )
    expected_rows = len(expected_rrhs) * len(expected_days) * len(expected_times)
    if len(loads) != expected_rows:
        raise ValueError(
            f"Expected {expected_rows} RRH/day/time rows, found {len(loads)}."
        )


def assert_pool_load_conservation(merged):
    """Ensure pool totals preserve the network total for every day and time."""
    dimensions = ["day_of_week", "time"]
    scaled_loads = np.rint(
        merged["traffic_load_mbph"].to_numpy() * 1_000_000
    )
    if not np.allclose(
        merged["traffic_load_mbph"].to_numpy(),
        scaled_loads / 1_000_000,
        rtol=0,
        atol=1e-9,
    ):
        raise ValueError(
            "Traffic-load values have more than six decimal places; "
            "exact conservation cannot be checked at the CSV precision."
        )
    conservation_data = merged[
        dimensions + ["bbu_pool_id"]
    ].copy()
    conservation_data["load_micro_mbph"] = scaled_loads.astype(np.int64)
    total_by_sample = (
        conservation_data.groupby(dimensions, sort=True)[
            "load_micro_mbph"
        ].sum()
    )
    pool_by_sample = (
        conservation_data.groupby(dimensions + ["bbu_pool_id"], sort=True)[
            "load_micro_mbph"
        ]
        .sum()
        .groupby(level=dimensions)
        .sum()
    )
    if not total_by_sample.equals(pool_by_sample):
        raise AssertionError(
            "The sum of per-pool loads does not match the total RRH load "
            "for every day/time sample."
        )


def build_weekday_profiles(schedule):
    """Average each normalized profile over Monday-Friday by sample index."""
    if len(schedule) != SAMPLES_PER_DAY:
        raise ValueError(
            f"Expected {SAMPLES_PER_DAY} schedule samples, found {len(schedule)}."
        )

    profiles = pd.DataFrame({
        "sample_index": range(SAMPLES_PER_DAY),
        "t_day": [index / SAMPLES_PER_DAY for index in range(SAMPLES_PER_DAY)],
        "time": [_time_label(index) for index in range(SAMPLES_PER_DAY)],
    })
    for label, profile in PROFILE_COLUMNS.items():
        columns = [f"thp_{day}_xu17_{profile}" for day in WEEKDAYS]
        missing = set(columns) - set(schedule.columns)
        if missing:
            raise ValueError(
                f"Traffic schedule is missing columns: {sorted(missing)}"
            )
        values = schedule[columns].apply(pd.to_numeric, errors="raise")
        if not np.isfinite(values.to_numpy()).all():
            raise ValueError(f"Non-finite values in the {label} profile.")
        if ((values < 0) | (values > 1)).to_numpy().any():
            raise ValueError(
                f"Normalized values in the {label} profile must be in [0, 1]."
            )
        profiles[label] = values.mean(axis=1)
    profiles["combined_normalized_sum"] = profiles[
        ["Residential", "Office", "Mixed"]
    ].sum(axis=1)
    return profiles


def detect_weekday_peak(profiles):
    """Return the earliest sample index at which the three weekday profiles peak."""
    return int(profiles["combined_normalized_sum"].idxmax())


def _weekday_pool_means(merged):
    weekdays = merged.loc[merged["day_of_week"].isin(WEEKDAY_NAMES)].copy()
    if weekdays.empty:
        raise ValueError("Traffic-load data has no weekday samples.")
    daily_pool = (
        weekdays.groupby(
            ["day_of_week", "time", "bbu_pool_id"], as_index=False
        )["traffic_load_mbph"]
        .sum()
    )
    mean_by_pool = (
        daily_pool.groupby(["time", "bbu_pool_id"], as_index=False)[
            "traffic_load_mbph"
        ]
        .mean()
    )
    daily_network = (
        weekdays.groupby(["day_of_week", "time"], as_index=False)[
            "traffic_load_mbph"
        ]
        .sum()
    )
    mean_network = (
        daily_network.groupby("time", as_index=False)["traffic_load_mbph"]
        .mean()
    )
    return mean_by_pool, mean_network


def _build_pool_summary(
    pool_ids,
    pool_means,
    scenario,
    assignment_map,
    profiles,
    peak_index,
):
    peak_time = profiles.loc[peak_index, "time"]
    summary = pd.DataFrame({"bbu_pool_id": pool_ids})

    def values_at(time):
        rows = pool_means.loc[pool_means["time"] == time]
        return rows.set_index("bbu_pool_id")["traffic_load_mbph"]

    for time in REPORT_HOURS:
        series = values_at(time)
        summary[f"baseline_{time.replace(':', '')}_mbph"] = (
            summary["bbu_pool_id"].map(series)
        )
    real_peak = values_at(peak_time)
    summary["real_case_peak_mbph"] = summary["bbu_pool_id"].map(real_peak)

    capacities = scenario[["rrh_id", "phy_capacity_max_mbph"]].copy()
    capacities["rrh_id"] = capacities["rrh_id"].astype(str)
    capacities["phy_capacity_max_mbph"] = pd.to_numeric(
        capacities["phy_capacity_max_mbph"], errors="raise"
    )
    capacities = capacities.merge(
        assignment_map, on="rrh_id", how="left", validate="one_to_one"
    )
    for label in ("Residential", "Office"):
        profile_value = float(profiles.loc[peak_index, label])
        homogeneous = capacities.assign(
            traffic_load_mbph=(
                capacities["phy_capacity_max_mbph"] * profile_value
            )
        )
        by_pool = homogeneous.groupby("bbu_pool_id")[
            "traffic_load_mbph"
        ].sum()
        summary[f"all_{label.lower()}_peak_mbph"] = summary[
            "bbu_pool_id"
        ].map(by_pool)
    summary["weekday_traffic_peak_time"] = peak_time
    return summary, peak_time


def _save_profile_plot(profiles, output_dir):
    fig, axis = plt.subplots(figsize=(11, 6))
    hours = profiles["sample_index"] * MINUTES_PER_SAMPLE / 60
    for label in ("Residential", "Office", "Mixed"):
        axis.plot(
            hours,
            profiles[label],
            color=PROFILE_COLORS[label],
            linestyle="--" if label == "Mixed" else "-",
            linewidth=2.2,
            label=label,
        )
    axis.set(
        xlim=(0, 24),
        ylim=(0, 1),
        xlabel="Time of day",
        ylabel="Normalized traffic [0, 1]",
        title="Weekday mean normalized traffic profiles",
    )
    axis.set_xticks(range(0, 25, 4), [f"{hour:02d}:00" for hour in range(0, 25, 4)])
    axis.grid(True, alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "traffic_profiles_24h.png", dpi=300)
    plt.close(fig)


def _save_pool_load_plots(pool_ids, pool_means, output_dir):
    for time in REPORT_HOURS:
        rows = pool_means.loc[pool_means["time"] == time].set_index(
            "bbu_pool_id"
        )
        values = rows["traffic_load_mbph"].reindex(pool_ids)
        fig, axis = plt.subplots(figsize=(12, 6))
        axis.bar(pool_ids, values, color="#4c78a8")
        axis.set(
            xlabel="BBU Pool",
            ylabel="Assigned load [MB/h]",
            title=f"Initial BBU Pool load at {time} (weekday mean)",
        )
        axis.tick_params(axis="x", labelrotation=60)
        axis.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(output_dir / f"pool_load_{time.replace(':', '')}.png", dpi=300)
        plt.close(fig)


def _save_sensitivity_plot(summary, peak_time, output_dir):
    positions = np.arange(len(summary))
    width = 0.26
    fig, axis = plt.subplots(figsize=(13, 6))
    series = (
        ("all_residential_peak_mbph", "All Residential", "#d55e00"),
        ("all_office_peak_mbph", "All Office", "#0072b2"),
        ("real_case_peak_mbph", "Real Case", "#009e73"),
    )
    for offset, (column, label, color) in enumerate(series):
        axis.bar(
            positions + (offset - 1) * width,
            summary[column],
            width,
            label=label,
            color=color,
        )
    axis.set(
        xlabel="BBU Pool",
        ylabel="Assigned load [MB/h]",
        title=f"Cell-type sensitivity at weekday traffic peak ({peak_time})",
    )
    axis.set_xticks(positions, summary["bbu_pool_id"], rotation=60)
    axis.grid(axis="y", alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "pool_sensitivity_peak.png", dpi=300)
    plt.close(fig)


def _save_network_plot(mean_network, output_dir):
    mean_network = mean_network.copy()
    mean_network["sample_index"] = mean_network["time"].map(
        { _time_label(index): index for index in range(SAMPLES_PER_DAY) }
    )
    mean_network = mean_network.sort_values("sample_index")
    hours = mean_network["sample_index"] * MINUTES_PER_SAMPLE / 60
    fig, axis = plt.subplots(figsize=(11, 6))
    axis.plot(hours, mean_network["traffic_load_mbph"], color="#4c78a8", linewidth=2)
    axis.set(
        xlim=(0, 24),
        xlabel="Time of day",
        ylabel="Network load [MB/h]",
        title="Total network load over the mean weekday",
    )
    axis.set_xticks(range(0, 25, 4), [f"{hour:02d}:00" for hour in range(0, 25, 4)])
    axis.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_dir / "network_load_24h.png", dpi=300)
    plt.close(fig)
    return mean_network.drop(columns="sample_index")


def run_analysis(
    scenario_file=DEFAULT_SCENARIO_FILE,
    schedule_file=DEFAULT_SCHEDULE_FILE,
    load_file=DEFAULT_LOAD_FILE,
    assignment_file=DEFAULT_ASSIGNMENT_FILE,
    output_dir=DEFAULT_OUTPUT_DIR,
):
    scenario_file = Path(scenario_file)
    schedule_file = Path(schedule_file)
    load_file = Path(load_file)
    assignment_file = Path(assignment_file)
    output_dir = Path(output_dir)

    scenario = pd.read_csv(scenario_file, dtype={"rrh_id": str})
    assignment = pd.read_csv(
        assignment_file, dtype={"site_id": str, "bbu_pool": str}
    )
    schedule = pd.read_csv(schedule_file)
    if "t_day" not in schedule.columns:
        raise ValueError("Traffic schedule is missing the t_day column.")
    t_day = pd.to_numeric(schedule["t_day"], errors="coerce")
    expected_t_day = np.arange(SAMPLES_PER_DAY) / SAMPLES_PER_DAY
    if len(schedule) == SAMPLES_PER_DAY and not np.allclose(
        t_day.to_numpy(), expected_t_day, rtol=0, atol=1e-9, equal_nan=False
    ):
        warnings.warn(
            "traffic_schedule.csv t_day values are invalid or out of order; "
            "sample times will be derived from the ordered 10-minute rows.",
            RuntimeWarning,
        )
    profiles = build_weekday_profiles(schedule)
    peak_index = detect_weekday_peak(profiles)

    required_load_columns = {
        "rrh_id",
        "day_of_week",
        "time",
        "traffic_load_mbph",
    }
    loads = pd.read_csv(
        load_file,
        usecols=lambda column: column in required_load_columns,
        dtype={"rrh_id": str, "day_of_week": str, "time": str},
    )
    missing_load = required_load_columns - set(loads.columns)
    if missing_load:
        raise ValueError(
            f"Traffic-load CSV is missing columns: {sorted(missing_load)}"
        )
    loads["traffic_load_mbph"] = pd.to_numeric(
        loads["traffic_load_mbph"], errors="raise"
    )
    assignment_map = build_assignment_map(scenario, assignment)
    validate_load_coverage(loads, assignment_map)
    merged = merge_loads_with_assignment(loads, assignment_map)
    assert_pool_load_conservation(merged)

    pool_ids = sorted(assignment_map["bbu_pool_id"].unique())
    pool_means, mean_network = _weekday_pool_means(merged)
    pool_summary, peak_time = _build_pool_summary(
        pool_ids,
        pool_means,
        scenario,
        assignment_map,
        profiles,
        peak_index,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    profiles.to_csv(output_dir / "weekday_traffic_profiles.csv", index=False)
    pool_summary.to_csv(output_dir / "bbu_pool_load_summary.csv", index=False)
    mean_network = _save_network_plot(mean_network, output_dir)
    mean_network.to_csv(output_dir / "network_load_24h.csv", index=False)
    _save_profile_plot(profiles, output_dir)
    _save_pool_load_plots(pool_ids, pool_means, output_dir)
    _save_sensitivity_plot(pool_summary, peak_time, output_dir)

    return {
        "merged_rows": len(merged),
        "rrh_count": scenario["rrh_id"].nunique(),
        "pool_count": len(pool_ids),
        "peak_time": peak_time,
        "peak_profile_sum": float(
            profiles.loc[peak_index, "combined_normalized_sum"]
        ),
        "pool_summary": pool_summary,
        "network_load": mean_network,
        "output_dir": output_dir,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Create weekday traffic and initial BBU Pool baseline reports."
        )
    )
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO_FILE)
    parser.add_argument("--schedule", type=Path, default=DEFAULT_SCHEDULE_FILE)
    parser.add_argument("--load", type=Path, default=DEFAULT_LOAD_FILE)
    parser.add_argument(
        "--assignment", type=Path, default=DEFAULT_ASSIGNMENT_FILE
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    result = run_analysis(
        scenario_file=args.scenario,
        schedule_file=args.schedule,
        load_file=args.load,
        assignment_file=args.assignment,
        output_dir=args.output_dir,
    )
    print(
        f"Baseline: {result['rrh_count']} RRHs, {result['pool_count']} BBU Pools, "
        f"{result['merged_rows']} load rows."
    )
    print(
        f"Weekday traffic peak: {result['peak_time']} "
        f"(normalized sum {result['peak_profile_sum']:.12f})."
    )
    print(f"Saved plots and CSV summaries to: {result['output_dir']}")
    print(result["pool_summary"].to_string(index=False))


if __name__ == "__main__":
    main()
