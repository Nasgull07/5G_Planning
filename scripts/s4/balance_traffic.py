#!/usr/bin/env python3
"""Balance RRH traffic across the feasible, fixed BBU Pool locations."""

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ASSIGNMENT_FILE = (
    BASE_DIR
    / "scripts"
    / "s4"
    / "rrh_bbu_k40_s100_zoned_balanced_kmedian_assignments_with_traffic_and_capacity.csv"
)
DEFAULT_LOAD_FILE = (
    BASE_DIR
    / "scripts"
    / "s4"
    / "rrh_traffic_load_k40_s100_zoned_balanced.csv"
)
DEFAULT_OUTPUT_DIR = BASE_DIR / "data" / "processed" / "s4_load_balancing"
DEFAULT_HOUR = "21:20"
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
ASSIGNMENT_COLUMNS = (
    "rrh_id",
    "site_id",
    "baseline_bbu_pool",
    "bbu_pool_id",
    "distance_km",
    "traffic_load_mbph",
    "feasible_pool_count",
    "is_forced",
)


def _parse_array(value, field, rrh_id):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSON in {field} for RRH {rrh_id!r}."
            ) from error
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(
            f"{field} must be a non-empty array for RRH {rrh_id!r}."
        )
    return list(value)


def build_connection_records(assignments):
    """Validate and normalize each RRH's feasible BBU Pool alternatives."""
    required = {
        "rrh_id",
        "site_id",
        "bbu_pool",
        "feasible_bbu_pools",
        "feasible_distances_km",
    }
    missing = required - set(assignments.columns)
    if missing:
        raise ValueError(
            f"Assignment data is missing columns: {sorted(missing)}"
        )
    if assignments["rrh_id"].isna().any() or assignments["rrh_id"].duplicated().any():
        raise ValueError("Assignment RRH IDs must be present and unique.")

    records = []
    for row in assignments.to_dict("records"):
        rrh_id = str(row["rrh_id"])
        pools = [
            str(pool).strip()
            for pool in _parse_array(
                row["feasible_bbu_pools"], "feasible_bbu_pools", rrh_id
            )
        ]
        if any(not pool for pool in pools) or len(set(pools)) != len(pools):
            raise ValueError(
                f"Feasible BBU Pool IDs must be unique and non-empty for {rrh_id!r}."
            )
        raw_distances = _parse_array(
            row["feasible_distances_km"], "feasible_distances_km", rrh_id
        )
        try:
            distances = [float(distance) for distance in raw_distances]
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Invalid feasible distance for RRH {rrh_id!r}."
            ) from error
        if len(pools) != len(distances):
            raise ValueError(
                f"Pool and distance arrays have different lengths for {rrh_id!r}."
            )
        if any(not math.isfinite(distance) or distance < 0 for distance in distances):
            raise ValueError(
                f"Feasible distances must be finite and non-negative for {rrh_id!r}."
            )
        if "max_allowed_distance_km" in row:
            max_distance = float(row["max_allowed_distance_km"])
            if not math.isfinite(max_distance) or max_distance <= 0:
                raise ValueError(
                    f"Invalid maximum distance for RRH {rrh_id!r}."
                )
            if any(distance > max_distance + 1e-6 for distance in distances):
                raise ValueError(
                    f"A feasible distance exceeds the maximum for RRH {rrh_id!r}."
                )

        baseline_pool = str(row["bbu_pool"]).strip()
        if baseline_pool not in pools:
            raise ValueError(
                f"Baseline BBU Pool is not feasible for RRH {rrh_id!r}."
            )
        records.append({
            "rrh_id": rrh_id,
            "site_id": str(row["site_id"]).strip(),
            "baseline_bbu_pool": baseline_pool,
            "feasible_bbu_pools": pools,
            "feasible_distances_km": distances,
            "feasible_pool_count": len(pools),
            "minimum_distance_km": min(distances),
        })
    if not records:
        raise ValueError("Assignment data contains no RRHs.")
    return records


def read_weekday_peak_loads(load_file, rrh_ids, hour=DEFAULT_HOUR):
    """Read one mean Monday-Friday load per RRH at the selected sample time."""
    required = {"rrh_id", "day_of_week", "time", "traffic_load_mbph"}
    loads = pd.read_csv(
        load_file,
        usecols=lambda column: column in required,
        dtype={"rrh_id": str, "day_of_week": str, "time": str},
    )
    missing = required - set(loads.columns)
    if missing:
        raise ValueError(
            f"Traffic-load data is missing columns: {sorted(missing)}"
        )
    selected = loads.loc[
        loads["day_of_week"].isin(WEEKDAYS) & (loads["time"] == hour),
        ["rrh_id", "day_of_week", "time", "traffic_load_mbph"],
    ].copy()
    if selected.empty:
        raise ValueError(f"No weekday traffic-load samples found for {hour}.")
    if selected[["rrh_id", "day_of_week"]].duplicated().any():
        raise ValueError(f"Duplicate weekday samples found for {hour}.")
    selected["traffic_load_mbph"] = pd.to_numeric(
        selected["traffic_load_mbph"], errors="raise"
    )
    if (
        not selected["traffic_load_mbph"].map(math.isfinite).all()
        or (selected["traffic_load_mbph"] < 0).any()
    ):
        raise ValueError(
            f"Traffic loads must be finite and non-negative at {hour}."
        )
    expected_ids = {str(rrh_id) for rrh_id in rrh_ids}
    observed_ids = set(selected["rrh_id"])
    if expected_ids != observed_ids:
        raise ValueError(
            "Assignment and traffic-load RRH IDs do not match; "
            f"missing={len(expected_ids - observed_ids)}, "
            f"unexpected={len(observed_ids - expected_ids)}."
        )
    weekday_counts = selected.groupby("rrh_id")["day_of_week"].nunique()
    if len(weekday_counts) != len(expected_ids) or (
        weekday_counts != len(WEEKDAYS)
    ).any():
        raise ValueError(
            f"Every RRH must have one load for all five weekdays at {hour}."
        )
    means = selected.groupby("rrh_id")["traffic_load_mbph"].mean()
    return means.to_dict()


def assign_rrhs(records, load_map, method="traffic"):
    """Assign RRHs greedily while preserving all feasible-pool constraints."""
    if method not in {"traffic", "rrh_count"}:
        raise ValueError("method must be 'traffic' or 'rrh_count'.")
    record_ids = {record["rrh_id"] for record in records}
    if record_ids != set(load_map):
        raise ValueError("Connection and traffic-load RRH IDs do not match.")
    if any(
        not math.isfinite(float(load_map[rrh_id]))
        or float(load_map[rrh_id]) < 0
        for rrh_id in record_ids
    ):
        raise ValueError("RRH loads must be finite and non-negative.")

    pool_ids = {
        pool
        for record in records
        for pool in record["feasible_bbu_pools"]
    }
    assignments = {}
    pool_loads = {pool: 0.0 for pool in pool_ids}
    pool_counts = {pool: 0 for pool in pool_ids}

    fixed = sorted(
        (record for record in records if record["feasible_pool_count"] == 1),
        key=lambda record: record["rrh_id"],
    )
    flexible = [
        record for record in records if record["feasible_pool_count"] > 1
    ]
    for record in fixed:
        pool = record["feasible_bbu_pools"][0]
        distance = record["feasible_distances_km"][0]
        assignments[record["rrh_id"]] = (pool, distance, True)
        pool_loads[pool] += float(load_map[record["rrh_id"]])
        pool_counts[pool] += 1

    if method == "traffic":
        flexible.sort(key=lambda record: (
            record["feasible_pool_count"],
            record["minimum_distance_km"],
            record["rrh_id"],
        ))
    else:
        flexible.sort(key=lambda record: (
            record["feasible_pool_count"],
            record["rrh_id"],
        ))

    for record in flexible:
        candidates = zip(
            record["feasible_bbu_pools"],
            record["feasible_distances_km"],
        )
        if method == "traffic":
            pool, distance = min(
                candidates,
                key=lambda candidate: (
                    pool_loads[candidate[0]],
                    candidate[1],
                    candidate[0],
                ),
            )
            pool_loads[pool] += float(load_map[record["rrh_id"]])
        else:
            pool, distance = min(
                candidates,
                key=lambda candidate: (
                    pool_counts[candidate[0]],
                    candidate[1],
                    candidate[0],
                ),
            )
        pool_counts[pool] += 1
        assignments[record["rrh_id"]] = (pool, distance, False)

    rows = []
    for record in records:
        pool, distance, is_forced = assignments[record["rrh_id"]]
        rows.append({
            "rrh_id": record["rrh_id"],
            "site_id": record["site_id"],
            "baseline_bbu_pool": record["baseline_bbu_pool"],
            "bbu_pool_id": pool,
            "distance_km": distance,
            "traffic_load_mbph": float(load_map[record["rrh_id"]]),
            "feasible_pool_count": record["feasible_pool_count"],
            "is_forced": is_forced,
        })
        if pool not in record["feasible_bbu_pools"]:
            raise AssertionError(
                f"RRH {record['rrh_id']!r} was assigned to an infeasible pool."
            )
        selected_distance = record["feasible_distances_km"][
            record["feasible_bbu_pools"].index(pool)
        ]
        if not math.isclose(distance, selected_distance, abs_tol=1e-9):
            raise AssertionError(
                f"Selected distance changed for RRH {record['rrh_id']!r}."
            )
    result = pd.DataFrame(rows, columns=ASSIGNMENT_COLUMNS)
    if len(result) != len(records) or result["rrh_id"].duplicated().any():
        raise AssertionError("Each RRH must be assigned exactly once.")
    return result


def build_pool_summary(traffic_assignment, count_assignment, pool_ids=None):
    """Compare pool loads and RRH counts for the two required heuristics."""
    def summarize(assignments, prefix):
        return (
            assignments.groupby("bbu_pool_id", as_index=False)
            .agg(
                **{
                    f"{prefix}_rrh_count": ("rrh_id", "size"),
                    f"{prefix}_traffic_load_mbph": (
                        "traffic_load_mbph",
                        "sum",
                    ),
                    f"{prefix}_mean_distance_km": ("distance_km", "mean"),
                }
            )
        )

    summary = summarize(traffic_assignment, "traffic").merge(
        summarize(count_assignment, "rrh_count"),
        on="bbu_pool_id",
        how="outer",
        validate="one_to_one",
    ).fillna({
        "traffic_rrh_count": 0,
        "traffic_traffic_load_mbph": 0.0,
        "traffic_mean_distance_km": 0.0,
        "rrh_count_rrh_count": 0,
        "rrh_count_traffic_load_mbph": 0.0,
        "rrh_count_mean_distance_km": 0.0,
    })
    if pool_ids is not None:
        summary = summary.set_index("bbu_pool_id").reindex(
            pool_ids, fill_value=0
        ).rename_axis("bbu_pool_id").reset_index()
    return summary.sort_values("bbu_pool_id")


def plot_pool_comparison(pool_summary, output_file, hour=DEFAULT_HOUR):
    """Plot traffic load and RRH counts for both assignment methods."""
    pool_summary = pool_summary.sort_values("bbu_pool_id")
    pool_labels = pool_summary["bbu_pool_id"].astype(str).tolist()
    positions = np.arange(len(pool_labels))
    bar_height = 0.38
    figure, (traffic_axis, count_axis) = plt.subplots(
        2, 1, figsize=(13, max(10, len(pool_labels) * 0.38)), sharey=True
    )

    traffic_axis.barh(
        positions - bar_height / 2,
        pool_summary["traffic_traffic_load_mbph"] / 1_000_000,
        height=bar_height,
        label="Balanceo por tráfico",
        color="#2878B5",
    )
    traffic_axis.barh(
        positions + bar_height / 2,
        pool_summary["rrh_count_traffic_load_mbph"] / 1_000_000,
        height=bar_height,
        label="Balanceo por número de RRH",
        color="#F28E2B",
    )
    traffic_axis.set_title("Carga de tráfico por BBU pool")
    traffic_axis.set_xlabel("Carga media (TB/h)")
    traffic_axis.legend(loc="lower right")

    count_axis.barh(
        positions - bar_height / 2,
        pool_summary["traffic_rrh_count"],
        height=bar_height,
        label="Balanceo por tráfico",
        color="#2878B5",
    )
    count_axis.barh(
        positions + bar_height / 2,
        pool_summary["rrh_count_rrh_count"],
        height=bar_height,
        label="Balanceo por número de RRH",
        color="#F28E2B",
    )
    count_axis.set_title("Número de RRH por BBU pool")
    count_axis.set_xlabel("RRH asignadas")
    count_axis.legend(loc="lower right")
    count_axis.set_yticks(positions)
    count_axis.set_yticklabels(pool_labels)
    count_axis.invert_yaxis()

    for axis in (traffic_axis, count_axis):
        axis.grid(axis="x", alpha=0.25)
        axis.set_axisbelow(True)

    figure.suptitle(
        f"Comparación de asignaciones a las {hour} (promedio laborable)",
        fontsize=14,
    )
    figure.tight_layout(rect=(0, 0, 1, 0.98))
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_file, dpi=180, bbox_inches="tight")
    plt.close(figure)
    return output_file


def plot_ranked_pool_loads(pool_summary, output_file, hour=DEFAULT_HOUR):
    """Compare pool traffic distributions after ranking each method."""
    traffic_loads, count_method_loads = _rank_pool_loads(pool_summary)
    ranks = np.arange(1, len(pool_summary) + 1)

    figure, axis = plt.subplots(figsize=(11, 6))
    axis.plot(
        ranks,
        traffic_loads,
        marker="o",
        markersize=4,
        linewidth=2,
        label="Balanceo por tráfico",
        color="#2878B5",
    )
    axis.plot(
        ranks,
        count_method_loads,
        marker="o",
        markersize=4,
        linewidth=2,
        label="Balanceo por número de RRH",
        color="#F28E2B",
    )
    axis.set_title(
        f"Carga por pool ordenada de mayor a menor ({hour})"
    )
    axis.set_xlabel("Rango del BBU pool (1 = mayor carga)")
    axis.set_ylabel("Carga media (TB/h)")
    axis.set_xlim(1, len(ranks))
    axis.grid(alpha=0.25)
    axis.legend()
    figure.tight_layout()

    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_file, dpi=180, bbox_inches="tight")
    plt.close(figure)
    return output_file


def _rank_pool_loads(pool_summary):
    traffic_loads = np.sort(
        pool_summary["traffic_traffic_load_mbph"].to_numpy()
    )[::-1] / 1_000_000
    count_method_loads = np.sort(
        pool_summary["rrh_count_traffic_load_mbph"].to_numpy()
    )[::-1] / 1_000_000
    return traffic_loads, count_method_loads


def run_balancing(
    assignment_file=DEFAULT_ASSIGNMENT_FILE,
    load_file=DEFAULT_LOAD_FILE,
    output_dir=DEFAULT_OUTPUT_DIR,
    hour=DEFAULT_HOUR,
):
    assignments = pd.read_csv(
        assignment_file,
        dtype={
            "rrh_id": str,
            "site_id": str,
            "bbu_pool": str,
            "feasible_bbu_pools": str,
            "feasible_distances_km": str,
        },
    )
    records = build_connection_records(assignments)
    load_map = read_weekday_peak_loads(
        load_file,
        [record["rrh_id"] for record in records],
        hour=hour,
    )
    traffic_assignment = assign_rrhs(records, load_map, method="traffic")
    count_assignment = assign_rrhs(records, load_map, method="rrh_count")
    pool_ids = sorted({
        pool
        for record in records
        for pool in record["feasible_bbu_pools"]
    })
    pool_summary = build_pool_summary(
        traffic_assignment, count_assignment, pool_ids=pool_ids
    )
    expected_load = sum(load_map.values())
    for name, result in (
        ("traffic", traffic_assignment),
        ("RRH-count", count_assignment),
    ):
        if not math.isclose(
            float(result["traffic_load_mbph"].sum()),
            expected_load,
            rel_tol=1e-12,
            abs_tol=1e-6,
        ):
            raise AssertionError(f"{name} assignment does not conserve load.")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    hour_suffix = hour.replace(":", "")
    traffic_path = output_dir / f"assignment_traffic_{hour_suffix}.csv"
    count_path = output_dir / f"assignment_rrh_count_{hour_suffix}.csv"
    summary_path = output_dir / f"pool_load_comparison_{hour_suffix}.csv"
    plot_path = output_dir / f"pool_balance_comparison_{hour_suffix}.png"
    ranked_plot_path = output_dir / f"pool_load_ranked_{hour_suffix}.png"
    traffic_assignment.to_csv(traffic_path, index=False)
    count_assignment.to_csv(count_path, index=False)
    pool_summary.to_csv(summary_path, index=False)
    plot_pool_comparison(pool_summary, plot_path, hour=hour)
    plot_ranked_pool_loads(pool_summary, ranked_plot_path, hour=hour)
    return {
        "hour": hour,
        "rrh_count": len(records),
        "pool_count": int(pool_summary["bbu_pool_id"].nunique()),
        "traffic_assignment": traffic_assignment,
        "count_assignment": count_assignment,
        "pool_summary": pool_summary,
        "traffic_output": traffic_path,
        "count_output": count_path,
        "summary_output": summary_path,
        "plot_output": plot_path,
        "ranked_plot_output": ranked_plot_path,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Greedy BBU Pool traffic balancing and RRH-count comparison "
            "using only the feasible links from the initial assignment."
        )
    )
    parser.add_argument("--assignments", type=Path, default=DEFAULT_ASSIGNMENT_FILE)
    parser.add_argument("--loads", type=Path, default=DEFAULT_LOAD_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "-hour",
        "--hour",
        default=DEFAULT_HOUR,
        metavar="HH:MM",
        help=(
            "Ten-minute traffic sample to balance, e.g. 21:20 "
            "(default: %(default)s)."
        ),
    )
    args = parser.parse_args()

    result = run_balancing(
        assignment_file=args.assignments,
        load_file=args.loads,
        output_dir=args.output_dir,
        hour=args.hour,
    )
    print(
        f"Balanced {result['rrh_count']} RRHs across "
        f"{result['pool_count']} BBU Pools at {result['hour']}."
    )
    print(f"Traffic assignment: {result['traffic_output']}")
    print(f"RRH-count assignment: {result['count_output']}")
    print(f"Pool summary: {result['summary_output']}")
    print(f"Pool comparison plot: {result['plot_output']}")
    print(f"Ranked pool-load plot: {result['ranked_plot_output']}")


if __name__ == "__main__":
    main()
