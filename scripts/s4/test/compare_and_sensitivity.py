#!/usr/bin/env python3
"""Compare Phase 1 and Module 2 assignments across BBU Pool deployments."""

import argparse
import json
from decimal import Decimal
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

if __package__:
    from .load_balancing_algorithms import detect_peak_time, weekday_mean_loads
else:
    from scripts.s4.test.load_balancing_algorithms import (
        detect_peak_time,
        weekday_mean_loads,
    )


BASE_DIR = Path(__file__).resolve().parents[3]
DEFAULT_SCENARIO_FILE = BASE_DIR / "data" / "processed" / "scenario_sites.csv"
DEFAULT_LOAD_FILE = BASE_DIR / "data" / "processed" / "rrh_traffic_load.csv"
DEFAULT_OUTPUT_DIR = BASE_DIR / "results" / "phase2" / "comparison"
DEFAULT_K_VALUES = (22, 30, 40)
SENSITIVITY_HOURS = ("08:00", "15:00", "21:00")
MAX_DISTANCE_KM = 4.0
METHOD_LABELS = {
    "distance": "Distance-only",
    "traffic": "Traffic-balanced",
    "rrh_count": "RRH-count-balanced",
}
CELL_CASES = ("All Residential", "All Office", "Real Case")
DISTANCE_BINS = ("0-1 km", "1-2 km", "2-3 km", "3-4 km")


def _extract_site_id(rrh_id):
    value = str(rrh_id).strip()
    if value.startswith("[") and value.endswith("]"):
        parsed = json.loads(value)
        if not isinstance(parsed, list) or not parsed:
            raise ValueError(f"Invalid source RRH ID: {rrh_id!r}")
        return str(parsed[0]).strip()
    return value


def validate_assignment(assignments, expected_rrh_ids):
    required = {"rrh_id", "bbu_pool_id", "distance_km"}
    missing = required - set(assignments.columns)
    if missing:
        raise ValueError(f"Assignment is missing columns: {sorted(missing)}")
    if assignments["rrh_id"].isna().any():
        raise ValueError("Assignment contains empty RRH IDs.")
    if assignments["bbu_pool_id"].isna().any():
        raise ValueError("Assignment contains RRHs without a BBU Pool.")
    if assignments["rrh_id"].duplicated().any():
        raise ValueError("Assignment contains duplicate RRH IDs.")
    expected = set(expected_rrh_ids)
    actual = set(assignments["rrh_id"].astype(str))
    if actual != expected:
        raise ValueError(
            "Assignment must contain every scenario RRH exactly once; "
            f"missing={len(expected - actual)}, unexpected={len(actual - expected)}."
        )
    distances = pd.to_numeric(assignments["distance_km"], errors="raise")
    if (
        not np.isfinite(distances).all()
        or (distances < 0).any()
        or (distances > MAX_DISTANCE_KM + 1e-9).any()
    ):
        raise ValueError(
            f"Assignment distances must be finite and within {MAX_DISTANCE_KM:g} km."
        )


def build_distance_assignment(scenario, phase1_assignment):
    """Translate Phase 1's normalized RRH/site IDs to scenario RRH IDs."""
    required_scenario = {"rrh_id"}
    required_phase1 = {"site_id", "bbu_pool", "distance_to_bbu_km"}
    missing_scenario = required_scenario - set(scenario.columns)
    missing_phase1 = required_phase1 - set(phase1_assignment.columns)
    if missing_scenario:
        raise ValueError(f"Scenario is missing columns: {sorted(missing_scenario)}")
    if missing_phase1:
        raise ValueError(
            f"Phase 1 assignment is missing columns: {sorted(missing_phase1)}"
        )

    site_map = scenario[["rrh_id"]].copy()
    if site_map["rrh_id"].isna().any() or site_map["rrh_id"].duplicated().any():
        raise ValueError("Scenario RRH IDs must be present and unique.")
    site_map["rrh_id"] = site_map["rrh_id"].astype(str)
    site_map["site_id"] = site_map["rrh_id"].map(_extract_site_id)

    pool_map = phase1_assignment[
        ["site_id", "bbu_pool", "distance_to_bbu_km"]
    ].copy()
    pool_map["site_id"] = pool_map["site_id"].astype(str)
    pool_map = pool_map.rename(columns={
        "bbu_pool": "bbu_pool_id",
        "distance_to_bbu_km": "distance_km",
    })
    mapped = site_map.merge(
        pool_map,
        on="site_id",
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    if (mapped["_merge"] != "both").any():
        example_ids = (
            mapped.loc[mapped["_merge"] != "both", "site_id"]
            .head(5)
            .tolist()
        )
        raise ValueError(
            "Some scenario RRHs have no Phase 1 assignment; "
            f"example site IDs: {example_ids}"
        )
    result = mapped[["rrh_id", "bbu_pool_id", "distance_km"]]
    return result


def assert_load_conservation(assignments, rrh_loads, expected_rrh_ids=None):
    """Check exact row and decimal-load conservation after assignment."""
    if expected_rrh_ids is None:
        expected_rrh_ids = rrh_loads["rrh_id"].astype(str)
    validate_assignment(assignments, set(expected_rrh_ids))
    if rrh_loads["rrh_id"].isna().any() or rrh_loads["rrh_id"].duplicated().any():
        raise ValueError("Loads must contain exactly one row per RRH.")
    if set(rrh_loads["rrh_id"].astype(str)) != set(expected_rrh_ids):
        raise ValueError("Load RRH IDs do not match the scenario RRH IDs.")

    loads = rrh_loads[["rrh_id", "traffic_load_mbph"]].copy()
    loads["rrh_id"] = loads["rrh_id"].astype(str)
    loads["traffic_load_mbph"] = pd.to_numeric(
        loads["traffic_load_mbph"], errors="raise"
    )
    if not np.isfinite(loads["traffic_load_mbph"]).all():
        raise ValueError("Loads must be finite.")
    merged = assignments[["rrh_id", "bbu_pool_id"]].merge(
        loads, on="rrh_id", how="left", validate="one_to_one", indicator=True
    )
    if len(merged) != len(rrh_loads) or (merged["_merge"] != "both").any():
        raise AssertionError("Assignment merge lost or duplicated RRH load rows.")

    expected_total = sum(
        (Decimal(str(value)) for value in loads["traffic_load_mbph"]),
        Decimal(0),
    )
    pool_totals = {}
    for pool, load in merged[
        ["bbu_pool_id", "traffic_load_mbph"]
    ].itertuples(index=False, name=None):
        pool_totals[pool] = pool_totals.get(pool, Decimal(0)) + Decimal(str(load))
    if sum(pool_totals.values(), Decimal(0)) != expected_total:
        raise AssertionError(
            "The sum of BBU Pool loads differs from the original RRH load."
        )
    return float(expected_total)


def aggregate_pool_metrics(assignments, rrh_loads, capacities, pool_ids):
    """Aggregate RRH count, peak load, and available PHY capacity by pool."""
    expected_rrh_ids = set(rrh_loads["rrh_id"].astype(str))
    assert_load_conservation(assignments, rrh_loads, expected_rrh_ids)
    if capacities["rrh_id"].isna().any() or capacities["rrh_id"].duplicated().any():
        raise ValueError("Capacity data must contain exactly one row per RRH.")
    if set(capacities["rrh_id"].astype(str)) != expected_rrh_ids:
        raise ValueError("Capacity RRH IDs do not match the scenario RRH IDs.")

    merged = assignments[
        ["rrh_id", "bbu_pool_id", "distance_km"]
    ].copy()
    merged["rrh_id"] = merged["rrh_id"].astype(str)
    loads = rrh_loads[["rrh_id", "traffic_load_mbph"]].copy()
    capacities = capacities[["rrh_id", "phy_capacity_max_mbph"]].copy()
    loads["rrh_id"] = loads["rrh_id"].astype(str)
    capacities["rrh_id"] = capacities["rrh_id"].astype(str)
    merged = merged.merge(
        loads, on="rrh_id", how="left", validate="one_to_one"
    ).merge(
        capacities, on="rrh_id", how="left", validate="one_to_one"
    )
    merged["traffic_load_mbph"] = pd.to_numeric(
        merged["traffic_load_mbph"], errors="raise"
    )
    merged["phy_capacity_max_mbph"] = pd.to_numeric(
        merged["phy_capacity_max_mbph"], errors="raise"
    )
    if (
        not np.isfinite(merged["phy_capacity_max_mbph"]).all()
        or (merged["phy_capacity_max_mbph"] < 0).any()
    ):
        raise ValueError("RRH PHY capacities must be finite and non-negative.")

    grouped = merged.groupby("bbu_pool_id").agg(
        rrh_count=("rrh_id", "size"),
        load_mbph=("traffic_load_mbph", "sum"),
        capacity_mbph=("phy_capacity_max_mbph", "sum"),
    )
    grouped["mean_distance_km"] = (
        merged.groupby("bbu_pool_id")["distance_km"].mean()
    )
    result = grouped.reindex(pool_ids).fillna(0)
    result.index.name = "bbu_pool_id"
    return result


def deployment_metrics(k, method, pool_metrics, assignments, network_load):
    pool_loads = pool_metrics["load_mbph"].to_numpy(dtype=float)
    mean_load = float(np.mean(pool_loads))
    std_load = float(np.std(pool_loads))
    return {
        "k": k,
        "method": METHOD_LABELS[method],
        "pool_count": len(pool_metrics),
        "rrh_count": len(assignments),
        "network_load_mbph": network_load,
        "max_pool_load_mbph": float(np.max(pool_loads)),
        "mean_pool_load_mbph": mean_load,
        "std_pool_load_mbph": std_load,
        "coefficient_of_variation": std_load / mean_load if mean_load else 0.0,
        "mean_pool_capacity_mbph": float(
            pool_metrics["capacity_mbph"].mean()
        ),
        "mean_distance_km": float(assignments["distance_km"].mean()),
    }


def distance_bin_counts(assignments):
    labels = list(DISTANCE_BINS)
    distances = pd.to_numeric(assignments["distance_km"], errors="raise")
    if (
        not np.isfinite(distances).all()
        or (distances < 0).any()
        or (distances > MAX_DISTANCE_KM + 1e-9).any()
    ):
        raise ValueError("Distances must be in the inclusive 0-4 km range.")
    bins = pd.cut(
        distances,
        bins=[-1e-12, 1, 2, 3, MAX_DISTANCE_KM + 1e-9],
        labels=labels,
        include_lowest=True,
        right=True,
    )
    return bins.value_counts().reindex(labels, fill_value=0).astype(int)


def hourly_assignment_changes(peak_assignment, hourly_assignment, hour, k):
    expected_ids = set(peak_assignment["rrh_id"].astype(str))
    validate_assignment(peak_assignment, expected_ids)
    validate_assignment(hourly_assignment, expected_ids)
    peak_pool = peak_assignment.set_index("rrh_id")["bbu_pool_id"]
    hourly_pool = hourly_assignment.set_index("rrh_id")["bbu_pool_id"]
    changed = int((peak_pool != hourly_pool.reindex(peak_pool.index)).sum())
    return {
        "k": k,
        "hour": hour,
        "changed_rrhs": changed,
        "changed_percent": changed / len(expected_ids) * 100,
    }


def _phase1_assignment_path(k):
    directory = BASE_DIR / "output" / f"BBU-pool-allocation_k{k}"
    matches = sorted(
        directory.glob(f"rrh_bbu_k{k}_s*_kmedian_assignments.csv")
    )
    if len(matches) != 1:
        raise FileNotFoundError(
            f"Expected exactly one Phase 1 assignment for K={k} in {directory}; "
            f"found {len(matches)}."
        )
    return matches[0]


def _analysis_directories(k):
    phase2_dir = BASE_DIR / "results" / "phase2"
    if k == 30:
        return phase2_dir / "baseline", phase2_dir / "balancing"
    return phase2_dir / f"baseline_k{k}", phase2_dir / f"balancing_k{k}"


def _read_assignment(path, initial=False):
    if initial:
        return pd.read_csv(
            path,
            dtype={"rrh_id": str, "site_id": str, "bbu_pool": str},
        )
    return pd.read_csv(
        path, dtype={"rrh_id": str, "bbu_pool_id": str}
    )


def _load_cell_scenario(capacities, case, profile_value, real_case_loads):
    if case == "Real Case":
        return real_case_loads
    normalized_loads = capacities[["rrh_id"]].copy()
    normalized_loads["traffic_load_mbph"] = (
        capacities["phy_capacity_max_mbph"] * profile_value
    )
    return normalized_loads


def _ranked_pool_counts(counts, k):
    subset = counts.loc[counts["k"] == k]
    pivot = subset.pivot(
        index="bbu_pool_id", columns="method", values="rrh_count"
    ).fillna(0)
    methods = ["Distance-only", "Traffic-balanced", "RRH-count-balanced"]
    ranked = pd.DataFrame({
        method: pivot[method].sort_values(
            ascending=False, kind="stable"
        ).to_numpy()
        for method in methods
    })
    ranked.index = np.arange(1, len(ranked) + 1)
    ranked.index.name = "pool_rank"
    return ranked


def _plot_rrh_counts(counts, k, output_dir):
    ranked_counts = _ranked_pool_counts(counts, k)
    methods = ["Distance-only", "Traffic-balanced", "RRH-count-balanced"]
    fig, axis = plt.subplots(figsize=(12, 6))
    for method in methods:
        axis.plot(
            ranked_counts.index,
            ranked_counts[method],
            marker="o",
            markersize=4,
            label=method,
        )
    axis.set(
        xlabel="BBU Pool rank (most to fewest RRHs)",
        ylabel="Assigned RRHs",
        title=f"RRHs per BBU Pool, K={k}",
    )
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_dir / f"rrhs_per_pool_k{k}.png", dpi=300)
    plt.close(fig)


def _plot_hourly_changes(changes, output_dir):
    hours = list(SENSITIVITY_HOURS)
    deployments = sorted(changes["k"].unique())
    positions = np.arange(len(hours))
    width = 0.8 / len(deployments)
    fig, axis = plt.subplots(figsize=(10, 6))
    for offset, k in enumerate(deployments):
        rows = (
            changes.loc[changes["k"] == k]
            .set_index("hour")
            .reindex(hours)
        )
        x = positions + (offset - (len(deployments) - 1) / 2) * width
        bars = axis.bar(x, rows["changed_rrhs"], width, label=f"K={k}")
        for bar, percent in zip(bars, rows["changed_percent"]):
            axis.annotate(
                f"{percent:.1f}%",
                (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                fontsize=8,
            )
    axis.set(
        xlabel="Traffic-balancing hour",
        ylabel="RRHs moved from the 13:50 assignment",
        title="Hourly assignment stability relative to peak adoption",
    )
    axis.set_xticks(positions, hours)
    axis.grid(axis="y", alpha=0.25)
    axis.legend(title="Deployment")
    fig.tight_layout()
    fig.savefig(output_dir / "hourly_assignment_changes.png", dpi=300)
    plt.close(fig)


def _plot_cell_sensitivity(cell_loads, k, output_dir, peak_time):
    fig, axes = plt.subplots(1, 2, figsize=(18, 7), sharey=True)
    states = (
        ("Distance-only", "Initial: distance-only"),
        ("Traffic-balanced", "After traffic balancing"),
    )
    colors = {
        "All Residential": "#d55e00",
        "All Office": "#0072b2",
        "Real Case": "#009e73",
    }
    for axis, (state, title) in zip(axes, states):
        subset = cell_loads.loc[
            (cell_loads["k"] == k) & (cell_loads["method"] == state)
        ]
        pivot = subset.pivot(
            index="bbu_pool_id",
            columns="traffic_case",
            values="load_mbph",
        )
        pools = sorted(pivot.index)
        pivot = pivot.reindex(pools)
        positions = np.arange(len(pools))
        width = 0.25
        for offset, case in enumerate(CELL_CASES):
            axis.bar(
                positions + (offset - 1) * width,
                pivot[case] / 1_000_000,
                width,
                label=case,
                color=colors[case],
            )
        axis.set(
            xlabel="BBU Pool",
            title=title,
        )
        axis.set_xticks(positions, pools, rotation=60, ha="right")
        axis.grid(axis="y", alpha=0.25)
        axis.legend()
    axes[0].set_ylabel("Assigned load [million MB/h]")
    fig.suptitle(
        f"Cell-type load sensitivity at {peak_time}, K={k}"
    )
    fig.tight_layout()
    fig.savefig(output_dir / f"cell_type_sensitivity_k{k}.png", dpi=300)
    plt.close(fig)


def _plot_distance_bins(distance_bins, deployments, output_dir):
    fig, axes = plt.subplots(1, len(deployments), figsize=(18, 5), sharey=True)
    methods = ["Distance-only", "Traffic-balanced", "RRH-count-balanced"]
    for axis, k in zip(axes, deployments):
        subset = distance_bins.loc[distance_bins["k"] == k]
        pivot = subset.pivot(
            index="distance_bin", columns="method", values="rrh_count"
        ).reindex(DISTANCE_BINS).fillna(0)
        positions = np.arange(len(DISTANCE_BINS))
        width = 0.25
        for offset, method in enumerate(methods):
            axis.bar(
                positions + (offset - 1) * width,
                pivot[method],
                width,
                label=method,
            )
        axis.set_title(f"K={k}")
        axis.set_xticks(positions, DISTANCE_BINS)
        axis.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("Assigned RRHs")
    axes[-1].legend(fontsize=8)
    fig.suptitle("Assigned RRHs by fronthaul-distance interval")
    fig.tight_layout()
    fig.savefig(output_dir / "distance_distribution_by_k.png", dpi=300)
    plt.close(fig)


def _plot_deployment_metrics(metrics, deployments, output_dir):
    methods = ["Distance-only", "Traffic-balanced", "RRH-count-balanced"]
    colors = ["#9e9e9e", "#0072b2", "#e69f00"]
    positions = np.arange(len(deployments))
    width = 0.24
    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    axes[0].set_title("Mean peak load per pool (error bar: ±1 std)")
    axes[1].set_title("Load imbalance (coefficient of variation)")
    axes[2].set_title("Mean available PHY capacity per pool")
    for offset, (method, color) in enumerate(zip(methods, colors)):
        rows = (
            metrics.loc[metrics["method"] == method]
            .set_index("k")
            .reindex(deployments)
        )
        x = positions + (offset - 1) * width
        axes[0].bar(
            x,
            rows["mean_pool_load_mbph"] / 1_000_000,
            width,
            yerr=rows["std_pool_load_mbph"] / 1_000_000,
            capsize=2,
            color=color,
            label=method,
        )
        axes[1].bar(
            x,
            rows["coefficient_of_variation"],
            width,
            color=color,
            label=method,
        )
        axes[2].bar(
            x,
            rows["mean_pool_capacity_mbph"] / 1_000_000,
            width,
            color=color,
            label=method,
        )
    for axis in axes:
        axis.set_xticks(positions, [f"K={k}" for k in deployments])
        axis.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("Million MB/h")
    axes[1].set_ylabel("Std / mean load")
    axes[2].set_ylabel("Million MB/h")
    axes[-1].legend(fontsize=8)
    fig.suptitle("Load balance and capacity sensitivity by deployment size")
    fig.tight_layout()
    fig.savefig(output_dir / "load_balance_by_deployment.png", dpi=300)
    plt.close(fig)


def _plot_before_after(pool_loads, k, output_dir):
    subset = pool_loads.loc[
        (pool_loads["k"] == k)
        & pool_loads["method"].isin(["Distance-only", "Traffic-balanced"])
    ]
    pivot = subset.pivot(
        index="bbu_pool_id", columns="method", values="load_mbph"
    )
    pools = sorted(pivot.index)
    pivot = pivot.reindex(pools)
    positions = np.arange(len(pools))
    width = 0.38
    fig, axis = plt.subplots(figsize=(max(14, len(pools) * 0.42), 6))
    axis.bar(
        positions - width / 2,
        pivot["Distance-only"] / 1_000_000,
        width,
        label="Before: distance-only",
        color="#9e9e9e",
    )
    axis.bar(
        positions + width / 2,
        pivot["Traffic-balanced"] / 1_000_000,
        width,
        label="After: traffic-balanced",
        color="#0072b2",
    )
    average = pivot["Distance-only"].sum() / len(pools) / 1_000_000
    axis.axhline(
        average,
        color="black",
        linestyle="--",
        linewidth=1.1,
        label="Network mean per pool",
    )
    axis.set(
        xlabel="BBU Pool",
        ylabel="Assigned load [million MB/h]",
        title=f"Peak load before and after traffic balancing, K={k}",
    )
    axis.set_xticks(positions, pools, rotation=60, ha="right")
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_dir / f"peak_load_before_after_k{k}.png", dpi=300)
    plt.close(fig)


def run_comparison(
    small_k=22,
    normal_k=30,
    large_k=40,
    scenario_file=DEFAULT_SCENARIO_FILE,
    load_file=DEFAULT_LOAD_FILE,
    output_dir=DEFAULT_OUTPUT_DIR,
):
    deployments = (small_k, normal_k, large_k)
    if len(set(deployments)) != len(deployments):
        raise ValueError("The few, normal, and many deployment sizes must differ.")

    scenario = pd.read_csv(scenario_file, dtype={"rrh_id": str})
    expected_rrh_ids = set(scenario["rrh_id"].astype(str))
    required_capacity = {"rrh_id", "phy_capacity_max_mbph"}
    if required_capacity - set(scenario.columns):
        raise ValueError("Scenario is missing per-RRH PHY capacity data.")
    capacities = scenario[list(required_capacity)].copy()
    capacities["rrh_id"] = capacities["rrh_id"].astype(str)
    capacities["phy_capacity_max_mbph"] = pd.to_numeric(
        capacities["phy_capacity_max_mbph"], errors="raise"
    )
    load_data = pd.read_csv(
        load_file,
        usecols=["rrh_id", "day_of_week", "time", "traffic_load_mbph"],
        dtype={"rrh_id": str, "day_of_week": str, "time": str},
    )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    metric_rows = []
    pool_metric_frames = []
    count_frames = []
    distance_bin_frames = []
    cell_load_frames = []
    hourly_change_rows = []
    peak_times = []
    deployments_by_k = {}

    for k in deployments:
        baseline_dir, balancing_dir = _analysis_directories(k)
        phase1_path = _phase1_assignment_path(k)
        phase1 = _read_assignment(phase1_path, initial=True)
        distance_assignment = build_distance_assignment(scenario, phase1)
        traffic_assignment = _read_assignment(
            balancing_dir / "assignment_traffic_peak.csv"
        )
        count_assignment = _read_assignment(
            balancing_dir / "assignment_rrh_count.csv"
        )
        assignments = {
            "distance": distance_assignment,
            "traffic": traffic_assignment,
            "rrh_count": count_assignment,
        }
        pool_ids = sorted(phase1["bbu_pool"].dropna().astype(str).unique())
        if len(pool_ids) != k:
            raise ValueError(
                f"Expected {k} Phase 1 pools, found {len(pool_ids)}."
            )
        for assignment in assignments.values():
            validate_assignment(assignment, expected_rrh_ids)

        profile_file = baseline_dir / "weekday_traffic_profiles.csv"
        profile_summary = pd.read_csv(profile_file)
        peak_time = detect_peak_time(profile_summary)
        peak_times.append(peak_time)
        peak_profile_rows = profile_summary.loc[
            profile_summary["time"] == peak_time
        ]
        if len(peak_profile_rows) != 1:
            raise ValueError(
                f"Expected one weekday profile sample at {peak_time}."
            )
        peak_profile = peak_profile_rows.iloc[0]
        peak_loads = weekday_mean_loads(load_data, peak_time)
        network_load = float(peak_loads["traffic_load_mbph"].sum())

        pool_metrics_by_method = {}
        for method, assignment in assignments.items():
            pool_metrics = aggregate_pool_metrics(
                assignment, peak_loads, capacities, pool_ids
            )
            pool_metrics_by_method[method] = pool_metrics
            metric_rows.append(
                deployment_metrics(
                    k,
                    method,
                    pool_metrics,
                    assignment,
                    network_load,
                )
            )
            frame = pool_metrics.reset_index()
            frame.insert(0, "method", METHOD_LABELS[method])
            frame.insert(0, "k", k)
            pool_metric_frames.append(frame)
            count_frames.append(
                frame[["k", "method", "bbu_pool_id", "rrh_count"]]
            )
            bins = distance_bin_counts(assignment)
            distance_bin_frames.append(pd.DataFrame({
                "k": k,
                "method": METHOD_LABELS[method],
                "distance_bin": bins.index,
                "rrh_count": bins.to_numpy(),
            }))

        baseline_report = pd.read_csv(
            baseline_dir / "bbu_pool_load_summary.csv"
        )
        report_loads = baseline_report.set_index("bbu_pool_id")[
            "real_case_peak_mbph"
        ].reindex(pool_ids)
        calculated_loads = pool_metrics_by_method["distance"][
            "load_mbph"
        ].reindex(pool_ids)
        if not np.allclose(
            report_loads.to_numpy(),
            calculated_loads.to_numpy(),
            rtol=1e-12,
            atol=1e-4,
        ):
            raise AssertionError(
                f"K={k} baseline load summary does not match the Phase 1 assignment."
            )

        hourly_assignments = {}
        for hour in SENSITIVITY_HOURS:
            hourly_assignment = _read_assignment(
                balancing_dir
                / f"assignment_traffic_{hour.replace(':', '')}.csv"
            )
            validate_assignment(hourly_assignment, expected_rrh_ids)
            hourly_assignments[hour] = hourly_assignment
            hourly_change_rows.append(
                hourly_assignment_changes(
                    traffic_assignment, hourly_assignment, hour, k
                )
            )

        for case in CELL_CASES:
            if case == "Real Case":
                cell_loads = peak_loads
            else:
                profile_value = float(peak_profile[case.replace("All ", "")])
                cell_loads = _load_cell_scenario(
                    capacities, case, profile_value, peak_loads
                )
            for method in ("distance", "traffic"):
                cell_metrics = aggregate_pool_metrics(
                    assignments[method], cell_loads, capacities, pool_ids
                )
                cell_frame = cell_metrics.reset_index()
                cell_frame["k"] = k
                cell_frame["method"] = METHOD_LABELS[method]
                cell_frame["traffic_case"] = case
                cell_load_frames.append(
                    cell_frame[
                        ["k", "method", "traffic_case", "bbu_pool_id", "load_mbph"]
                    ]
                )

        deployments_by_k[k] = {
            "peak_time": peak_time,
            "assignments": assignments,
            "pool_metrics": pool_metrics_by_method,
        }

    if len(set(peak_times)) != 1:
        raise ValueError(
            f"Deployments disagree on the weekday peak time: {sorted(set(peak_times))}"
        )
    peak_time = peak_times[0]

    metrics = pd.DataFrame(metric_rows)
    pool_metrics = pd.concat(pool_metric_frames, ignore_index=True)
    counts = pd.concat(count_frames, ignore_index=True)
    distance_bins = pd.concat(distance_bin_frames, ignore_index=True)
    cell_loads = pd.concat(cell_load_frames, ignore_index=True)
    hourly_changes = pd.DataFrame(hourly_change_rows)

    metrics.to_csv(output_dir / "deployment_metrics.csv", index=False)
    pool_metrics.to_csv(output_dir / "pool_metrics_at_peak.csv", index=False)
    counts.to_csv(output_dir / "rrhs_per_pool.csv", index=False)
    distance_bins.to_csv(output_dir / "distance_distribution.csv", index=False)
    cell_loads.to_csv(output_dir / "cell_type_loads_by_pool.csv", index=False)
    hourly_changes.to_csv(
        output_dir / "hourly_assignment_changes.csv", index=False
    )

    for k in deployments:
        _plot_rrh_counts(counts, k, output_dir)
        _plot_cell_sensitivity(cell_loads, k, output_dir, peak_time)
    _plot_hourly_changes(hourly_changes, output_dir)
    _plot_distance_bins(distance_bins, deployments, output_dir)
    _plot_deployment_metrics(metrics, deployments, output_dir)
    _plot_before_after(pool_metrics, 30, output_dir)
    _plot_before_after(pool_metrics, 40, output_dir)

    print(f"Weekday peak: {peak_time}")
    print(f"Saved tables and 300-dpi figures to: {output_dir}")
    print(
        metrics[
            [
                "k",
                "method",
                "max_pool_load_mbph",
                "std_pool_load_mbph",
                "coefficient_of_variation",
                "mean_distance_km",
                "mean_pool_capacity_mbph",
            ]
        ].to_string(index=False)
    )
    print("\nHourly assignment changes vs. traffic-balanced peak:")
    print(hourly_changes.to_string(index=False))
    return metrics, hourly_changes, output_dir


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Compare initial and balanced RRH assignments for K=22, 30, and 40."
        )
    )
    parser.add_argument("--small-k", type=int, default=DEFAULT_K_VALUES[0])
    parser.add_argument("--normal-k", type=int, default=DEFAULT_K_VALUES[1])
    parser.add_argument("--large-k", type=int, default=DEFAULT_K_VALUES[2])
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO_FILE)
    parser.add_argument("--loads", type=Path, default=DEFAULT_LOAD_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    run_comparison(
        small_k=args.small_k,
        normal_k=args.normal_k,
        large_k=args.large_k,
        scenario_file=args.scenario,
        load_file=args.loads,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
