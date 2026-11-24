#!/usr/bin/env python3
"""Create visual comparisons for the K=40 Phase 2 balancing results."""

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

if __package__:
    from .load_balancing_algorithms import detect_peak_time, weekday_mean_loads
else:
    from scripts.s4.load_balancing_algorithms import detect_peak_time, weekday_mean_loads


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_FILE = BASE_DIR / "data" / "processed" / "scenario_sites.csv"
DEFAULT_PHASE1_ASSIGNMENT = (
    BASE_DIR
    / "output"
    / "BBU-pool-allocation_k40"
    / "rrh_bbu_k40_s100_kmedian_assignments.csv"
)
DEFAULT_BASELINE_DIR = BASE_DIR / "results" / "phase2" / "baseline_k40"
DEFAULT_BALANCING_DIR = BASE_DIR / "results" / "phase2" / "balancing_k40"
DEFAULT_LOAD_FILE = BASE_DIR / "data" / "processed" / "rrh_traffic_load.csv"
DEFAULT_OUTPUT_DIR = DEFAULT_BALANCING_DIR / "visualizations"

ASSIGNMENT_FILES = {
    "08:00": "assignment_traffic_0800.csv",
    "15:00": "assignment_traffic_1500.csv",
    "21:00": "assignment_traffic_2100.csv",
}


def pool_loads_by_pool(assignment, rrh_loads):
    """Sum one mean-weekday load per RRH according to an assignment table."""
    required_assignment = {"rrh_id", "bbu_pool_id"}
    required_loads = {"rrh_id", "traffic_load_mbph"}
    missing_assignment = required_assignment - set(assignment.columns)
    missing_loads = required_loads - set(rrh_loads.columns)
    if missing_assignment:
        raise ValueError(
            f"Assignment is missing columns: {sorted(missing_assignment)}"
        )
    if missing_loads:
        raise ValueError(f"RRH loads are missing columns: {sorted(missing_loads)}")
    if assignment["rrh_id"].isna().any() or assignment["rrh_id"].duplicated().any():
        raise ValueError("Assignment must have exactly one row per RRH.")
    if rrh_loads["rrh_id"].isna().any() or rrh_loads["rrh_id"].duplicated().any():
        raise ValueError("Mean loads must have exactly one row per RRH.")

    assignment = assignment[["rrh_id", "bbu_pool_id"]].copy()
    rrh_loads = rrh_loads[["rrh_id", "traffic_load_mbph"]].copy()
    assignment["rrh_id"] = assignment["rrh_id"].astype(str)
    rrh_loads["rrh_id"] = rrh_loads["rrh_id"].astype(str)
    if set(assignment["rrh_id"]) != set(rrh_loads["rrh_id"]):
        raise ValueError("Assignment and load tables must contain the same RRHs.")

    rrh_loads["traffic_load_mbph"] = pd.to_numeric(
        rrh_loads["traffic_load_mbph"], errors="raise"
    )
    if (
        not np.isfinite(rrh_loads["traffic_load_mbph"]).all()
        or (rrh_loads["traffic_load_mbph"] < 0).any()
    ):
        raise ValueError("RRH loads must be finite and non-negative.")
    merged = assignment.merge(
        rrh_loads,
        on="rrh_id",
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    if (merged["_merge"] != "both").any():
        raise ValueError("Some RRHs did not receive a load in the assignment merge.")
    return merged.groupby("bbu_pool_id")["traffic_load_mbph"].sum()


def relative_pool_load_percent(pool_loads):
    """Express each pool's load as a percentage of the mean load per pool."""
    mean_load = pool_loads.mean()
    if not np.isfinite(mean_load) or mean_load <= 0:
        raise ValueError("Cannot normalize pool loads with a non-positive mean.")
    return pool_loads / mean_load * 100


def _validate_assignments(assignments, expected_rrh_ids):
    expected_rrh_ids = set(expected_rrh_ids)
    if (
        assignments["rrh_id"].duplicated().any()
        or set(assignments["rrh_id"]) != expected_rrh_ids
    ):
        raise ValueError(
            "A balancing assignment must contain every RRH exactly once."
        )


def _plot_peak_loads(pool_loads, output_dir):
    pools = pool_loads.index.tolist()
    positions = np.arange(len(pools))
    width = 0.27
    cases = (
        ("initial_baseline", "Phase 1 baseline", "#9e9e9e"),
        ("traffic_peak", "Traffic-balanced", "#0072b2"),
        ("rrh_count", "RRH-count-balanced", "#e69f00"),
    )
    fig, axis = plt.subplots(figsize=(18, 7))
    for offset, (column, label, color) in enumerate(cases):
        axis.bar(
            positions + (offset - 1) * width,
            pool_loads[column] / 1_000_000,
            width,
            label=label,
            color=color,
        )
    mean_pool_load = pool_loads["initial_baseline"].sum() / len(pools)
    axis.axhline(
        mean_pool_load / 1_000_000,
        color="black",
        linestyle="--",
        linewidth=1.2,
        label="Network mean per pool",
    )
    axis.set(
        xlabel="BBU Pool",
        ylabel="Assigned load [million MB/h]",
        title="K=40 assignments evaluated at the weekday peak",
    )
    axis.set_xticks(positions, pools, rotation=60, ha="right")
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "peak_pool_load_comparison.png", dpi=300)
    plt.close(fig)


def _plot_load_distributions(pool_loads, output_dir):
    columns = ["initial_baseline", "traffic_peak", "rrh_count"]
    labels = ["Phase 1 baseline", "Traffic-balanced", "RRH-count-balanced"]
    data = [pool_loads[column].to_numpy() / 1_000_000 for column in columns]
    fig, axis = plt.subplots(figsize=(9, 6))
    axis.boxplot(
        data,
        tick_labels=labels,
        showmeans=True,
        meanprops={
            "marker": "D",
            "markerfacecolor": "#1b9e77",
            "markeredgecolor": "#1b9e77",
        },
    )
    axis.scatter([], [], marker="D", color="#1b9e77", label="Mean")
    axis.set(
        ylabel="Assigned load [million MB/h]",
        title="Peak load distribution across the 40 BBU Pools",
    )
    axis.tick_params(axis="x", labelrotation=15)
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "peak_pool_load_distribution.png", dpi=300)
    plt.close(fig)


def _plot_pool_counts(pool_counts, output_dir):
    pools = pool_counts.index.tolist()
    positions = np.arange(len(pools))
    width = 0.27
    cases = (
        ("initial_baseline", "Phase 1 baseline", "#9e9e9e"),
        ("traffic_peak", "Traffic-balanced", "#0072b2"),
        ("rrh_count", "RRH-count-balanced", "#e69f00"),
    )
    fig, axis = plt.subplots(figsize=(18, 7))
    for offset, (column, label, color) in enumerate(cases):
        axis.bar(
            positions + (offset - 1) * width,
            pool_counts[column],
            width,
            label=label,
            color=color,
        )
    axis.axhline(
        pool_counts["initial_baseline"].sum() / len(pools),
        color="black",
        linestyle="--",
        linewidth=1.2,
        label="Mean RRHs per pool",
    )
    axis.set(
        xlabel="BBU Pool",
        ylabel="Assigned RRHs",
        title="RRH count per pool by assignment method",
    )
    axis.set_xticks(positions, pools, rotation=60, ha="right")
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "pool_rrh_count_comparison.png", dpi=300)
    plt.close(fig)


def _plot_sensitivity_heatmap(sensitivity, output_dir):
    values = sensitivity.to_numpy()
    fig, axis = plt.subplots(figsize=(18, 5))
    image = axis.imshow(values, aspect="auto", cmap="RdYlBu_r")
    axis.set_xticks(
        np.arange(len(sensitivity.columns)),
        sensitivity.columns,
        rotation=60,
        ha="right",
    )
    axis.set_yticks(np.arange(len(sensitivity.index)), sensitivity.index)
    axis.set(
        xlabel="BBU Pool",
        ylabel="Hour used to calculate the assignment",
        title="Traffic-balanced pool load relative to the mean at that hour",
    )
    colorbar = fig.colorbar(image, ax=axis)
    colorbar.set_label("Pool load / mean pool load [%]")
    fig.tight_layout()
    fig.savefig(output_dir / "traffic_assignment_sensitivity.png", dpi=300)
    plt.close(fig)


def _load_assignment(path):
    assignment = pd.read_csv(
        path, dtype={"rrh_id": str, "bbu_pool_id": str}
    )
    required = {"rrh_id", "bbu_pool_id"}
    missing = required - set(assignment.columns)
    if missing:
        raise ValueError(
            f"Assignment file {path} is missing columns: {sorted(missing)}"
        )
    return assignment


def run_visualization(
    scenario_file=DEFAULT_SCENARIO_FILE,
    phase1_assignment_file=DEFAULT_PHASE1_ASSIGNMENT,
    baseline_dir=DEFAULT_BASELINE_DIR,
    balancing_dir=DEFAULT_BALANCING_DIR,
    load_file=DEFAULT_LOAD_FILE,
    output_dir=DEFAULT_OUTPUT_DIR,
):
    scenario = pd.read_csv(scenario_file, dtype={"rrh_id": str})
    expected_rrh_ids = set(scenario["rrh_id"])
    phase1 = pd.read_csv(
        phase1_assignment_file,
        dtype={"rrh_id": str, "bbu_pool": str},
    )
    if not {"rrh_id", "bbu_pool"}.issubset(phase1.columns):
        raise ValueError("Phase 1 assignment needs rrh_id and bbu_pool columns.")
    if (
        phase1["rrh_id"].isna().any()
        or phase1["rrh_id"].duplicated().any()
        or phase1["bbu_pool"].isna().any()
        or len(phase1) != len(expected_rrh_ids)
    ):
        raise ValueError(
            "Phase 1 assignment must contain one pool for every scenario RRH."
        )
    baseline_summary = pd.read_csv(Path(baseline_dir) / "bbu_pool_load_summary.csv")
    profiles = pd.read_csv(Path(baseline_dir) / "weekday_traffic_profiles.csv")
    peak_time = detect_peak_time(profiles)
    load_data = pd.read_csv(
        load_file,
        usecols=["rrh_id", "day_of_week", "time", "traffic_load_mbph"],
        dtype={"rrh_id": str, "day_of_week": str, "time": str},
    )
    balancing_dir = Path(balancing_dir)
    output_dir = Path(output_dir)

    baseline_pools = set(baseline_summary["bbu_pool_id"].astype(str))
    if set(phase1["bbu_pool"].dropna().astype(str)) - baseline_pools:
        raise ValueError("Phase 1 pools do not match the baseline summary pools.")
    traffic_peak = _load_assignment(
        balancing_dir / "assignment_traffic_peak.csv"
    )
    rrh_count = _load_assignment(
        balancing_dir / "assignment_rrh_count.csv"
    )
    _validate_assignments(traffic_peak, expected_rrh_ids)
    _validate_assignments(rrh_count, expected_rrh_ids)

    peak_loads = weekday_mean_loads(load_data, peak_time)
    pool_ids = sorted(baseline_pools)
    peak_comparison = pd.DataFrame(index=pool_ids)
    baseline_loads = baseline_summary.set_index("bbu_pool_id")[
        "real_case_peak_mbph"
    ]
    peak_comparison["initial_baseline"] = baseline_loads.reindex(pool_ids)
    peak_comparison["traffic_peak"] = pool_loads_by_pool(
        traffic_peak, peak_loads
    ).reindex(pool_ids, fill_value=0)
    peak_comparison["rrh_count"] = pool_loads_by_pool(
        rrh_count, peak_loads
    ).reindex(pool_ids, fill_value=0)
    if peak_comparison.isna().any().any():
        raise ValueError("Baseline peak load summary is missing a BBU Pool.")

    phase1_counts = phase1.groupby("bbu_pool")["rrh_id"].size()
    peak_counts = traffic_peak.groupby("bbu_pool_id")["rrh_id"].size()
    rrh_counts = rrh_count.groupby("bbu_pool_id")["rrh_id"].size()
    pool_counts = pd.DataFrame(index=pool_ids)
    pool_counts["initial_baseline"] = phase1_counts.reindex(pool_ids, fill_value=0)
    pool_counts["traffic_peak"] = peak_counts.reindex(pool_ids, fill_value=0)
    pool_counts["rrh_count"] = rrh_counts.reindex(pool_ids, fill_value=0)

    assignment_by_time = {
        "08:00": _load_assignment(
            balancing_dir / ASSIGNMENT_FILES["08:00"]
        ),
        peak_time: traffic_peak,
        "15:00": _load_assignment(
            balancing_dir / ASSIGNMENT_FILES["15:00"]
        ),
        "21:00": _load_assignment(
            balancing_dir / ASSIGNMENT_FILES["21:00"]
        ),
    }
    sensitivity_values = {}
    summary_rows = []
    for column, label in (
        ("initial_baseline", "Phase 1 baseline"),
        ("traffic_peak", "Traffic-balanced"),
        ("rrh_count", "RRH-count-balanced"),
    ):
        loads = peak_comparison[column]
        summary_rows.append(_distribution_summary(label, peak_time, loads))

    for time, assignment in assignment_by_time.items():
        _validate_assignments(assignment, expected_rrh_ids)
        hourly_loads = peak_loads if time == peak_time else weekday_mean_loads(
            load_data, time
        )
        by_pool = pool_loads_by_pool(assignment, hourly_loads).reindex(
            pool_ids, fill_value=0
        )
        sensitivity_values[time] = relative_pool_load_percent(by_pool)
        summary_rows.append(
            _distribution_summary(f"Traffic assignment {time}", time, by_pool)
        )
    sensitivity = pd.DataFrame(sensitivity_values).T

    output_dir.mkdir(parents=True, exist_ok=True)
    _plot_peak_loads(peak_comparison, output_dir)
    _plot_load_distributions(peak_comparison, output_dir)
    _plot_pool_counts(pool_counts, output_dir)
    _plot_sensitivity_heatmap(sensitivity, output_dir)
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(output_dir / "balancing_visual_summary.csv", index=False)
    print(f"K=40 peak: {peak_time}")
    print(f"Saved visual comparisons to: {output_dir}")
    print(summary.to_string(index=False))
    return output_dir, summary


def _distribution_summary(label, time, pool_loads):
    values = pool_loads.to_numpy(dtype=float)
    mean = float(np.mean(values))
    standard_deviation = float(np.std(values))
    return {
        "scenario": label,
        "hour": time,
        "network_load_mbph": float(np.sum(values)),
        "mean_pool_load_mbph": mean,
        "std_pool_load_mbph": standard_deviation,
        "coefficient_of_variation": (
            standard_deviation / mean if mean > 0 else 0.0
        ),
        "min_pool_load_mbph": float(np.min(values)),
        "max_pool_load_mbph": float(np.max(values)),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Plot K=40 Phase 2 load-balancing results."
    )
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO_FILE)
    parser.add_argument(
        "--phase1-assignment",
        type=Path,
        default=DEFAULT_PHASE1_ASSIGNMENT,
    )
    parser.add_argument(
        "--baseline-dir", type=Path, default=DEFAULT_BASELINE_DIR
    )
    parser.add_argument(
        "--balancing-dir", type=Path, default=DEFAULT_BALANCING_DIR
    )
    parser.add_argument("--loads", type=Path, default=DEFAULT_LOAD_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    run_visualization(
        scenario_file=args.scenario,
        phase1_assignment_file=args.phase1_assignment,
        baseline_dir=args.baseline_dir,
        balancing_dir=args.balancing_dir,
        load_file=args.loads,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
