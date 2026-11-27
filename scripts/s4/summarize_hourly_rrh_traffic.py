#!/usr/bin/env python3
"""Summarize mean weekday traffic volume per RRH and plot network totals."""

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
DEFAULT_OUTPUT_FILE = (
    BASE_DIR
    / "data"
    / "processed"
    / "rrh_hourly_traffic_weekday_mean.csv"
)
DEFAULT_PLOT_FILE = (
    BASE_DIR
    / "data"
    / "processed"
    / "network_hourly_traffic_weekday_mean.png"
)

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
TRAFFIC_CATEGORIES = ("Residential", "Office", "Mixed")
SAMPLES_PER_HOUR = 6
SAMPLES_PER_DAY = 144
METADATA_COLUMNS = (
    "rrh_id",
    "site_id",
    "operator",
    "technology",
    "is_synthetic",
    "territory_id",
    "territory_name",
    "bbu_pool",
    "traffic_category",
    "capacity_technology",
    "phy_capacity_max_mbps",
    "phy_capacity_max_mbph",
)


def build_hourly_traffic(loads, assignments):
    """Return one mean-weekday hourly-volume row per RRH and network totals."""
    required_loads = {
        "rrh_id",
        "day_of_week",
        "time",
        "traffic_load_mbph",
    }
    missing_loads = required_loads - set(loads.columns)
    if missing_loads:
        raise ValueError(
            f"Traffic-load data is missing columns: {sorted(missing_loads)}"
        )

    required_assignments = {
        "rrh_id",
        "site_id",
        "traffic_category",
        "capacity_technology",
    }
    missing_assignments = required_assignments - set(assignments.columns)
    if missing_assignments:
        raise ValueError(
            f"Assignment data is missing columns: {sorted(missing_assignments)}"
        )
    if assignments["rrh_id"].isna().any() or assignments["rrh_id"].duplicated().any():
        raise ValueError("Assignment RRH IDs must be present and unique.")
    if assignments["site_id"].isna().any():
        raise ValueError("Assignment site IDs must be present.")
    if not assignments["traffic_category"].isin(TRAFFIC_CATEGORIES).all():
        raise ValueError("Assignments contain an unsupported traffic category.")
    if not assignments["capacity_technology"].isin({"4G", "5G"}).all():
        raise ValueError("Assignments contain an unsupported capacity technology.")

    weekday_loads = loads.loc[
        loads["day_of_week"].isin(WEEKDAYS),
        ["rrh_id", "day_of_week", "time", "traffic_load_mbph"],
    ].copy()
    if weekday_loads.empty:
        raise ValueError("Traffic-load data has no weekday samples.")
    if weekday_loads[["rrh_id", "day_of_week", "time"]].isna().any().any():
        raise ValueError("Weekday traffic data contains empty sample keys.")
    if weekday_loads.duplicated(["rrh_id", "day_of_week", "time"]).any():
        raise ValueError("Weekday traffic data contains duplicate samples.")

    expected_times = {
        f"{index // 6:02d}:{(index % 6) * 10:02d}"
        for index in range(SAMPLES_PER_DAY)
    }
    if set(weekday_loads["time"]) != expected_times:
        raise ValueError(
            "Weekday traffic data must contain all 144 ten-minute samples."
        )
    expected_rrhs = set(assignments["rrh_id"].astype(str))
    observed_rrhs = set(weekday_loads["rrh_id"].astype(str))
    if observed_rrhs != expected_rrhs:
        raise ValueError(
            "Traffic-load RRHs do not match assignments; "
            f"missing={len(expected_rrhs - observed_rrhs)}, "
            f"unexpected={len(observed_rrhs - expected_rrhs)}."
        )

    sample_counts = weekday_loads.groupby(
        ["rrh_id", "day_of_week"]
    )["time"].nunique()
    if len(sample_counts) != len(expected_rrhs) * len(WEEKDAYS):
        raise ValueError(
            "Every RRH must have traffic samples for all five weekdays."
        )
    if (sample_counts != SAMPLES_PER_DAY).any():
        raise ValueError(
            "Every RRH must have all 144 samples on each weekday."
        )

    weekday_loads["traffic_load_mbph"] = pd.to_numeric(
        weekday_loads["traffic_load_mbph"], errors="raise"
    )
    if (
        not np.isfinite(weekday_loads["traffic_load_mbph"]).all()
        or (weekday_loads["traffic_load_mbph"] < 0).any()
    ):
        raise ValueError("Traffic loads must be finite and non-negative.")
    weekday_loads["hour"] = (
        weekday_loads["time"].str.slice(0, 2).astype(int)
    )
    weekday_loads["hourly_volume_mb"] = (
        weekday_loads["traffic_load_mbph"] / SAMPLES_PER_HOUR
    )
    daily_volumes = weekday_loads.groupby(
        ["rrh_id", "day_of_week", "hour"], as_index=False
    )["hourly_volume_mb"].sum()
    mean_volumes = daily_volumes.groupby(
        ["rrh_id", "hour"], as_index=False
    )["hourly_volume_mb"].mean()

    volume_columns = [
        f"traffic_{hour:02d}_mb" for hour in range(24)
    ]
    hourly_wide = mean_volumes.pivot(
        index="rrh_id", columns="hour", values="hourly_volume_mb"
    )
    hourly_wide = hourly_wide.reindex(
        index=assignments["rrh_id"].astype(str),
        columns=range(24),
    )
    if hourly_wide.isna().any().any():
        raise ValueError("Some RRHs are missing one or more hourly volumes.")
    hourly_wide.columns = volume_columns
    hourly_wide.index.name = "rrh_id"

    metadata_columns = [
        column for column in METADATA_COLUMNS if column in assignments.columns
    ]
    metadata = assignments[metadata_columns].copy()
    metadata["rrh_id"] = metadata["rrh_id"].astype(str)
    if "phy_capacity_max_mbps" in metadata:
        metadata["phy_capacity_max_mbps"] = pd.to_numeric(
            metadata["phy_capacity_max_mbps"], errors="raise"
        )
    if "phy_capacity_max_mbph" in metadata:
        metadata["phy_capacity_max_mbph"] = pd.to_numeric(
            metadata["phy_capacity_max_mbph"], errors="raise"
        )
    per_rrh = metadata.merge(
        hourly_wide.reset_index(),
        on="rrh_id",
        how="left",
        validate="one_to_one",
        sort=False,
    )

    totals = per_rrh.groupby("traffic_category")[volume_columns].sum()
    totals = totals.reindex(TRAFFIC_CATEGORIES, fill_value=0)
    network = totals.sum(axis=0).rename("Total network")
    chart_data = totals.copy()
    chart_data.loc["Total network"] = network
    chart_data.index.name = "traffic_category"
    return per_rrh, chart_data.reset_index()


def _write_csv_atomic(dataframe, output_file):
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_file.parent,
        prefix=f".{output_file.name}.",
        suffix=".tmp",
    )
    temporary_file = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as output:
            dataframe.to_csv(output, index=False)
        os.replace(temporary_file, output_file)
    finally:
        temporary_file.unlink(missing_ok=True)


def _network_daily_metrics(chart_data):
    volume_columns = [f"traffic_{hour:02d}_mb" for hour in range(24)]
    network_rows = chart_data.loc[
        chart_data["traffic_category"] == "Total network",
        volume_columns,
    ]
    if len(network_rows) != 1:
        raise ValueError("Chart data must contain exactly one total network row.")
    values = pd.to_numeric(network_rows.iloc[0], errors="raise").to_numpy(
        dtype=float
    )
    if not np.isfinite(values).all():
        raise ValueError("Network hourly volumes must be finite.")
    peak_hour = int(np.argmax(values))
    return float(values.mean()), peak_hour, float(values[peak_hour])


def _save_plot(chart_data, output_file):
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    hours = np.arange(24)
    volume_columns = [f"traffic_{hour:02d}_mb" for hour in range(24)]
    colors = {
        "Residential": "#d55e00",
        "Office": "#0072b2",
        "Mixed": "#009e73",
        "Total network": "#222222",
    }
    fig, axis = plt.subplots(figsize=(12, 6))
    for _, row in chart_data.iterrows():
        category = row["traffic_category"]
        axis.plot(
            hours,
            row[volume_columns].astype(float) / 1_000_000,
            label=category,
            color=colors[category],
            linewidth=2.8 if category == "Total network" else 1.8,
            linestyle="-" if category == "Total network" else "--",
        )
    axis.margins(y=0.14)
    daily_mean, peak_hour, peak_volume = _network_daily_metrics(chart_data)
    daily_mean_tb = daily_mean / 1_000_000
    peak_volume_tb = peak_volume / 1_000_000
    axis.axhline(
        daily_mean_tb,
        color="#9467bd",
        linestyle=":",
        linewidth=1.8,
        label=f"Daily mean (network): {daily_mean_tb:.1f} TB",
    )
    axis.scatter(
        [peak_hour],
        [peak_volume_tb],
        color="#d62728",
        edgecolor="white",
        linewidth=0.8,
        marker="*",
        s=180,
        zorder=5,
        label="Daily maximum (network)",
    )
    axis.annotate(
        f"Max {peak_hour:02d}:00\n{peak_volume_tb:.1f} TB",
        xy=(peak_hour, peak_volume_tb),
        xytext=(-14, 14),
        textcoords="offset points",
        ha="right",
        va="bottom",
        color="#9b1c1c",
        fontweight="bold",
    )
    axis.set_xticks(hours)
    axis.set_xticklabels([f"{hour:02d}:00" for hour in hours], rotation=45)
    axis.set_xlabel("Hour of day")
    axis.set_ylabel("Mean traffic volume per one-hour interval (TB)")
    axis.set_title("Network traffic by hour (mean Monday-Friday)")
    axis.grid(True, alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_file, dpi=300)
    plt.close(fig)


def generate_hourly_traffic_report(
    load_file=DEFAULT_LOAD_FILE,
    assignment_file=DEFAULT_ASSIGNMENT_FILE,
    output_file=DEFAULT_OUTPUT_FILE,
    plot_file=DEFAULT_PLOT_FILE,
):
    load_file = Path(load_file)
    assignment_file = Path(assignment_file)
    output_file = Path(output_file)
    plot_file = Path(plot_file)
    if output_file.resolve() in {load_file.resolve(), assignment_file.resolve()}:
        raise ValueError("Hourly traffic output must not overwrite an input file.")
    if plot_file.resolve() in {load_file.resolve(), assignment_file.resolve()}:
        raise ValueError("Traffic plot must not overwrite an input file.")

    loads = pd.read_csv(
        load_file,
        dtype={"rrh_id": str, "day_of_week": str, "time": str},
    )
    assignments = pd.read_csv(
        assignment_file,
        dtype={"rrh_id": str, "site_id": str},
    )
    per_rrh, chart_data = build_hourly_traffic(loads, assignments)
    _write_csv_atomic(per_rrh, output_file)
    _save_plot(chart_data, plot_file)
    return per_rrh, chart_data


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate mean weekday hourly traffic volumes per RRH and a "
            "network/category plot."
        )
    )
    parser.add_argument("--loads", type=Path, default=DEFAULT_LOAD_FILE)
    parser.add_argument(
        "--assignments", type=Path, default=DEFAULT_ASSIGNMENT_FILE
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_FILE)
    parser.add_argument("--plot", type=Path, default=DEFAULT_PLOT_FILE)
    args = parser.parse_args()

    per_rrh, chart_data = generate_hourly_traffic_report(
        load_file=args.loads,
        assignment_file=args.assignments,
        output_file=args.output,
        plot_file=args.plot,
    )
    total_column = "traffic_13_mb"
    total_13_tb = float(
        chart_data.loc[
            chart_data["traffic_category"] == "Total network",
            total_column,
        ].iloc[0]
    ) / 1_000_000
    print(f"Generated hourly volumes for {len(per_rrh)} RRHs.")
    print(f"CSV: {args.output}")
    print(f"Plot: {args.plot}")
    print(f"Mean weekday network volume at 13:00: {total_13_tb:.3f} TB.")


if __name__ == "__main__":
    main()
