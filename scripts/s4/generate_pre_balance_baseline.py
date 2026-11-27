#!/usr/bin/env python3
"""Generate weekday traffic profiles and pre-load-balancing BBU reports."""

import argparse
import os
from pathlib import Path
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LOAD_FILE = (
    BASE_DIR
    / "scripts"
    / "s4"
    / "rrh_traffic_load_k40_s100_zoned_balanced.csv"
)
DEFAULT_ASSIGNMENT_FILE = (
    BASE_DIR
    / "scripts"
    / "s4"
    / "rrh_bbu_k40_s100_zoned_balanced_kmedian_assignments_with_traffic_and_capacity.csv"
)
DEFAULT_SCHEDULE_FILE = BASE_DIR / "data" / "input" / "traffic_schedule.csv"
DEFAULT_OUTPUT_DIR = BASE_DIR / "data" / "processed" / "s4_pre_balance"

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
SCHEDULE_DAYS = ("mon", "tue", "wed", "thu", "fri")
TRAFFIC_CATEGORIES = ("Residential", "Office", "Mixed")
PROFILE_NAMES = {
    "Residential": "residential",
    "Office": "office",
    "Mixed": "mixed",
}
SAMPLES_PER_DAY = 144
MINUTES_PER_SAMPLE = 10
REPORT_TIMES = ("08:00", "15:00", "20:00", "21:00", "21:20")


def _time_label(sample_index):
    minute_of_day = sample_index * MINUTES_PER_SAMPLE
    return f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"


def build_weekday_normalized_profiles(schedule):
    """Return each normalized category profile averaged Monday-Friday."""
    if len(schedule) != SAMPLES_PER_DAY:
        raise ValueError(
            f"Expected {SAMPLES_PER_DAY} schedule samples, found {len(schedule)}."
        )
    profiles = pd.DataFrame({
        "time": [_time_label(index) for index in range(SAMPLES_PER_DAY)]
    })
    for category, profile in PROFILE_NAMES.items():
        columns = [f"thp_{day}_xu17_{profile}" for day in SCHEDULE_DAYS]
        missing = set(columns) - set(schedule.columns)
        if missing:
            raise ValueError(
                f"Traffic schedule is missing columns: {sorted(missing)}"
            )
        values = schedule[columns].apply(pd.to_numeric, errors="raise")
        if not np.isfinite(values.to_numpy()).all():
            raise ValueError(f"Non-finite values in the {category} profile.")
        if ((values < 0) | (values > 1)).to_numpy().any():
            raise ValueError(
                f"Normalized {category} traffic values must be in [0, 1]."
            )
        profiles[category] = values.mean(axis=1)
    return profiles


def _validate_weekday_loads(loads, assignments):
    required_load_columns = {
        "rrh_id",
        "day_of_week",
        "time",
        "traffic_category",
        "traffic_load_mbph",
    }
    missing = required_load_columns - set(loads.columns)
    if missing:
        raise ValueError(
            f"Traffic-load data is missing columns: {sorted(missing)}"
        )
    required_assignment_columns = {
        "rrh_id",
        "bbu_pool",
        "traffic_category",
        "phy_capacity_max_mbph",
    }
    missing = required_assignment_columns - set(assignments.columns)
    if missing:
        raise ValueError(
            f"Assignment data is missing columns: {sorted(missing)}"
        )
    if assignments["rrh_id"].isna().any() or assignments["rrh_id"].duplicated().any():
        raise ValueError("Assignment RRH IDs must be present and unique.")
    if assignments["bbu_pool"].isna().any():
        raise ValueError("Assignment BBU Pool IDs must be present.")
    if not assignments["traffic_category"].isin(TRAFFIC_CATEGORIES).all():
        raise ValueError("Assignment data contains an unknown traffic category.")

    weekdays = loads.loc[
        loads["day_of_week"].isin(WEEKDAYS),
        [
            "rrh_id",
            "day_of_week",
            "time",
            "traffic_category",
            "traffic_load_mbph",
        ],
    ].copy()
    if weekdays.empty:
        raise ValueError("Traffic-load data has no weekday samples.")
    if weekdays[["rrh_id", "day_of_week", "time"]].isna().any().any():
        raise ValueError("Weekday traffic data contains empty sample keys.")
    if weekdays.duplicated(["rrh_id", "day_of_week", "time"]).any():
        raise ValueError("Weekday traffic data contains duplicate samples.")

    expected_times = {
        _time_label(index) for index in range(SAMPLES_PER_DAY)
    }
    if set(weekdays["time"]) != expected_times:
        raise ValueError(
            "Weekday traffic data must contain all 144 ten-minute samples."
        )
    expected_rrhs = set(assignments["rrh_id"].astype(str))
    observed_rrhs = set(weekdays["rrh_id"].astype(str))
    if expected_rrhs != observed_rrhs:
        raise ValueError(
            "Traffic-load RRHs do not match assignments; "
            f"missing={len(expected_rrhs - observed_rrhs)}, "
            f"unexpected={len(observed_rrhs - expected_rrhs)}."
        )
    sample_counts = weekdays.groupby(
        ["rrh_id", "day_of_week"]
    )["time"].nunique()
    if (
        len(sample_counts) != len(expected_rrhs) * len(WEEKDAYS)
        or (sample_counts != SAMPLES_PER_DAY).any()
    ):
        raise ValueError(
            "Every RRH must have all 144 samples on each weekday."
        )

    weekdays["traffic_load_mbph"] = pd.to_numeric(
        weekdays["traffic_load_mbph"], errors="raise"
    )
    if (
        not np.isfinite(weekdays["traffic_load_mbph"]).all()
        or (weekdays["traffic_load_mbph"] < 0).any()
    ):
        raise ValueError("Traffic loads must be finite and non-negative.")
    assignment_columns = assignments[
        ["rrh_id", "bbu_pool", "traffic_category", "phy_capacity_max_mbph"]
    ].copy()
    assignment_columns["rrh_id"] = assignment_columns["rrh_id"].astype(str)
    assignment_columns["phy_capacity_max_mbph"] = pd.to_numeric(
        assignment_columns["phy_capacity_max_mbph"], errors="raise"
    )
    if (
        not np.isfinite(assignment_columns["phy_capacity_max_mbph"]).all()
        or (assignment_columns["phy_capacity_max_mbph"] <= 0).any()
    ):
        raise ValueError("RRH PHY capacities must be finite and positive.")
    assignment_columns = assignment_columns.rename(
        columns={"traffic_category": "assigned_traffic_category"}
    )
    weekdays["rrh_id"] = weekdays["rrh_id"].astype(str)
    merged = weekdays.merge(
        assignment_columns,
        on="rrh_id",
        how="left",
        validate="many_to_one",
        indicator=True,
        sort=False,
    )
    if (merged["_merge"] != "both").any():
        raise ValueError("Some traffic-load RRHs have no BBU assignment.")
    merged = merged.drop(columns="_merge")
    if (
        merged["traffic_category"] != merged["assigned_traffic_category"]
    ).any():
        raise ValueError("Traffic categories differ between load and assignment data.")
    return merged


def build_prebalance_reports(loads, assignments, schedule):
    """Build 10-minute network rates, pool baselines, and peak sensitivity."""
    merged = _validate_weekday_loads(loads, assignments)
    normalized_profiles = build_weekday_normalized_profiles(schedule)
    times = normalized_profiles["time"].tolist()
    pool_ids = sorted(assignments["bbu_pool"].astype(str).unique())

    daily_category_loads = merged.groupby(
        ["day_of_week", "time", "traffic_category"]
    )["traffic_load_mbph"].sum()
    category_means = daily_category_loads.groupby(
        ["time", "traffic_category"]
    ).mean().unstack("traffic_category", fill_value=0)
    category_means = category_means.reindex(
        index=times, columns=TRAFFIC_CATEGORIES, fill_value=0
    )
    daily_network_loads = merged.groupby(
        ["day_of_week", "time"]
    )["traffic_load_mbph"].sum()
    network_means = daily_network_loads.groupby("time").mean().reindex(times)
    network_profile = category_means.copy()
    network_profile["Total network"] = network_means
    network_profile.index.name = "time"
    network_profile = network_profile.reset_index()
    peak_index = int(network_profile["Total network"].to_numpy().argmax())
    peak_time = str(network_profile.loc[peak_index, "time"])

    daily_pool_loads = merged.groupby(
        ["day_of_week", "time", "bbu_pool"]
    )["traffic_load_mbph"].sum()
    mean_pool_loads = daily_pool_loads.groupby(
        ["time", "bbu_pool"]
    ).mean()
    pool_summary = pd.DataFrame({"bbu_pool_id": pool_ids})
    for time in REPORT_TIMES:
        if time not in times:
            raise ValueError(f"Requested baseline time is missing: {time}")
        values = mean_pool_loads.xs(time, level="time")
        pool_summary[f"load_{time.replace(':', '')}_mbph"] = (
            pool_summary["bbu_pool_id"].map(values).fillna(0.0)
        )

    peak_profile = normalized_profiles.set_index("time").loc[peak_time]
    capacities = assignments[
        ["rrh_id", "bbu_pool", "phy_capacity_max_mbph"]
    ].copy()
    capacities["rrh_id"] = capacities["rrh_id"].astype(str)
    capacities["bbu_pool"] = capacities["bbu_pool"].astype(str)
    capacities["phy_capacity_max_mbph"] = pd.to_numeric(
        capacities["phy_capacity_max_mbph"], errors="raise"
    )
    sensitivity = pd.DataFrame({"bbu_pool_id": pool_ids})
    for category in ("Residential", "Office"):
        loads_for_category = capacities.assign(
            hypothetical_load_mbph=(
                capacities["phy_capacity_max_mbph"]
                * float(peak_profile[category])
            )
        )
        totals = loads_for_category.groupby("bbu_pool")[
            "hypothetical_load_mbph"
        ].sum()
        sensitivity[f"all_{category.lower()}_mbph"] = (
            sensitivity["bbu_pool_id"].map(totals).fillna(0.0)
        )

    peak_daily_pool_loads = merged.loc[
        merged["time"] == peak_time
    ].groupby(["day_of_week", "bbu_pool"])["traffic_load_mbph"].sum()
    peak_real_pool_loads = peak_daily_pool_loads.groupby("bbu_pool").mean()
    sensitivity["real_case_mbph"] = (
        sensitivity["bbu_pool_id"].map(peak_real_pool_loads).fillna(0.0)
    )
    sensitivity["peak_time"] = peak_time

    for time in REPORT_TIMES:
        expected = float(network_profile.loc[
            network_profile["time"] == time, "Total network"
        ].iloc[0])
        observed = float(pool_summary[
            f"load_{time.replace(':', '')}_mbph"
        ].sum())
        if not np.isclose(expected, observed, rtol=1e-10, atol=1e-6):
            raise AssertionError(
                f"Pool loads do not conserve network load at {time}."
            )
    return network_profile, pool_summary, sensitivity, peak_time


def _write_csv(dataframe, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8-sig", newline=""
        ) as output:
            dataframe.to_csv(output, index=False)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _plot_network_rate(profile, peak_time, output_dir):
    output_dir = Path(output_dir)
    time_values = profile["time"].tolist()
    hours = np.arange(len(profile)) / 6
    fig, axis = plt.subplots(figsize=(13, 6))
    colors = {
        "Residential": "#d55e00",
        "Office": "#0072b2",
        "Mixed": "#009e73",
        "Total network": "#222222",
    }
    for category in (*TRAFFIC_CATEGORIES, "Total network"):
        axis.plot(
            hours,
            profile[category] / 1_000_000,
            label=category,
            color=colors[category],
            linewidth=2.8 if category == "Total network" else 1.8,
            linestyle="-" if category == "Total network" else "--",
        )
    axis.margins(y=0.14)
    peak_index = time_values.index(peak_time)
    peak_tbph = float(profile.loc[peak_index, "Total network"]) / 1_000_000
    axis.scatter(
        [hours[peak_index]],
        [peak_tbph],
        color="#d62728",
        edgecolor="white",
        marker="*",
        s=180,
        zorder=5,
        label=f"Network peak: {peak_time}",
    )
    axis.annotate(
        f"Peak {peak_time}: {peak_tbph:.1f} TB/h",
        xy=(hours[peak_index], peak_tbph),
        xytext=(-12, 12),
        textcoords="offset points",
        ha="right",
        color="#9b1c1c",
        fontweight="bold",
    )
    axis.set_xticks(np.arange(0, 24, 2))
    axis.set_xticklabels([f"{hour:02d}:00" for hour in range(0, 24, 2)])
    axis.set_xlabel("Time of day")
    axis.set_ylabel("Mean weekday traffic rate (TB/h)")
    axis.set_title("Mean weekday traffic rate (10-minute intervals)")
    axis.grid(True, alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "network_traffic_rate_weekday_mean_10min.png", dpi=300)
    plt.close(fig)


def _plot_pool_loads(pool_summary, output_dir):
    output_dir = Path(output_dir)
    pool_ids = pool_summary["bbu_pool_id"]
    for time in REPORT_TIMES:
        column = f"load_{time.replace(':', '')}_mbph"
        fig, axis = plt.subplots(figsize=(13, 6))
        axis.bar(pool_ids, pool_summary[column], color="#4c78a8")
        axis.set(
            xlabel="BBU Pool",
            ylabel="Mean assigned load (MB/h)",
            title=f"Initial BBU Pool load at {time} (weekday mean)",
        )
        axis.tick_params(axis="x", labelrotation=60)
        axis.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(
            output_dir / f"bbu_pool_load_weekday_mean_{time.replace(':', '')}.png",
            dpi=300,
        )
        plt.close(fig)


def _plot_peak_sensitivity(sensitivity, peak_time, output_dir):
    output_dir = Path(output_dir)
    positions = np.arange(len(sensitivity))
    width = 0.26
    fig, axis = plt.subplots(figsize=(14, 6))
    series = (
        ("all_residential_mbph", "All Residential", "#d55e00"),
        ("all_office_mbph", "All Office", "#0072b2"),
        ("real_case_mbph", "Real Case", "#009e73"),
    )
    for offset, (column, label, color) in enumerate(series):
        axis.bar(
            positions + (offset - 1) * width,
            sensitivity[column],
            width,
            label=label,
            color=color,
        )
    axis.set(
        xlabel="BBU Pool",
        ylabel="Mean assigned load (MB/h)",
        title=f"Initial BBU Pool load by traffic profile at {peak_time}",
    )
    axis.set_xticks(positions, sensitivity["bbu_pool_id"], rotation=60)
    axis.grid(axis="y", alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(
        output_dir / "bbu_pool_traffic_profile_sensitivity_peak.png",
        dpi=300,
    )
    plt.close(fig)


def run_prebalance_reports(
    load_file=DEFAULT_LOAD_FILE,
    assignment_file=DEFAULT_ASSIGNMENT_FILE,
    schedule_file=DEFAULT_SCHEDULE_FILE,
    output_dir=DEFAULT_OUTPUT_DIR,
):
    load_file = Path(load_file)
    assignment_file = Path(assignment_file)
    schedule_file = Path(schedule_file)
    output_dir = Path(output_dir)
    loads = pd.read_csv(
        load_file,
        dtype={
            "rrh_id": str,
            "day_of_week": str,
            "time": str,
            "traffic_category": str,
        },
    )
    assignments = pd.read_csv(
        assignment_file,
        dtype={
            "rrh_id": str,
            "site_id": str,
            "bbu_pool": str,
            "traffic_category": str,
        },
    )
    schedule = pd.read_csv(schedule_file)
    network_profile, pool_summary, sensitivity, peak_time = (
        build_prebalance_reports(loads, assignments, schedule)
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        network_profile.rename(columns={
            "Residential": "residential_mbph",
            "Office": "office_mbph",
            "Mixed": "mixed_mbph",
            "Total network": "total_network_mbph",
        }),
        output_dir / "network_traffic_rate_weekday_mean_10min.csv",
    )
    _write_csv(
        pool_summary,
        output_dir / "bbu_pool_load_weekday_mean.csv",
    )
    _write_csv(
        sensitivity,
        output_dir / "bbu_pool_traffic_profile_sensitivity_peak.csv",
    )
    _plot_network_rate(network_profile, peak_time, output_dir)
    _plot_pool_loads(pool_summary, output_dir)
    _plot_peak_sensitivity(sensitivity, peak_time, output_dir)
    return network_profile, pool_summary, sensitivity, peak_time


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate mean weekday traffic-rate and pre-load-balancing "
            "BBU Pool reports."
        )
    )
    parser.add_argument("--loads", type=Path, default=DEFAULT_LOAD_FILE)
    parser.add_argument(
        "--assignments", type=Path, default=DEFAULT_ASSIGNMENT_FILE
    )
    parser.add_argument("--schedule", type=Path, default=DEFAULT_SCHEDULE_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    profile, pools, _, peak_time = run_prebalance_reports(
        load_file=args.loads,
        assignment_file=args.assignments,
        schedule_file=args.schedule,
        output_dir=args.output_dir,
    )
    peak_rate = float(
        profile.loc[profile["time"] == peak_time, "Total network"].iloc[0]
    )
    print(f"Generated baseline reports for {len(pools)} BBU Pools.")
    print(f"Actual mixed-scenario weekday peak: {peak_time} ({peak_rate:.3f} MB/h).")
    print(f"Pool-load charts: {', '.join(REPORT_TIMES)}.")
    print(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
