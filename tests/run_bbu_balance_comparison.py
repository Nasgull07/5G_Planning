#!/usr/bin/env python3
"""Compare three RRH-count balancing options against a K-median baseline."""

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.optimize import linprog
from scipy.sparse import coo_matrix


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.s2.bbu_initial_assignment_kmedian import (  # noqa: E402
    generate_map,
)


DEFAULT_MAX_DISTANCE_KM = 4.0
SOFT_PENALTY_KM = 0.5
MAX_JOINT_ITERATIONS = 10


def _project(longitudes, latitudes):
    transformer = Transformer.from_crs(
        "EPSG:4326", "EPSG:25831", always_xy=True
    )
    x, y = transformer.transform(
        np.asarray(longitudes, dtype=float),
        np.asarray(latitudes, dtype=float),
    )
    return np.column_stack((np.asarray(x).reshape(-1), np.asarray(y).reshape(-1)))


def _distance_matrix(rrh_xy, pool_xy):
    return np.linalg.norm(
        rrh_xy[:, np.newaxis, :] - pool_xy[np.newaxis, :, :], axis=2
    ) / 1000.0


def _load_baseline(assignment_file, pool_file, max_distance_km):
    if not assignment_file.is_file() or not pool_file.is_file():
        raise FileNotFoundError(
            "The baseline assignment and pool-location CSVs are required: "
            f"{assignment_file} and {pool_file}"
        )
    rrhs = pd.read_csv(assignment_file, dtype={"rrh_id": str, "bbu_pool": str})
    pools = pd.read_csv(pool_file, dtype={"rrh_id": str, "bbu_pool": str})
    if rrhs["rrh_id"].isna().any() or rrhs["rrh_id"].duplicated().any():
        raise ValueError("Baseline must contain exactly one row per RRH.")
    if pools["bbu_pool"].isna().any() or pools["bbu_pool"].duplicated().any():
        raise ValueError("Baseline must contain exactly one row per BBU pool.")
    if set(rrhs["bbu_pool"]) - set(pools["bbu_pool"]):
        raise ValueError("Baseline assignments refer to an unknown BBU pool.")
    if not np.all(
        pd.to_numeric(rrhs["distance_to_bbu_km"], errors="raise")
        <= max_distance_km + 1e-9
    ):
        raise ValueError("Baseline contains an RRH farther than 4 km from its pool.")
    return rrhs, pools.sort_values("bbu_pool").reset_index(drop=True)


def _make_assignment_lp(
    distances,
    pool_count,
    cap=None,
    penalty=None,
    max_distance_km=DEFAULT_MAX_DISTANCE_KM,
):
    rrh_count = distances.shape[0]
    feasible = np.isfinite(distances) & (
        distances <= max_distance_km + 1e-9
    )
    rrh_indices, pool_indices = np.nonzero(feasible)
    edge_count = len(rrh_indices)
    if edge_count == 0:
        raise ValueError("No RRH-to-pool links satisfy the 4 km limit.")

    if penalty is None:
        objective = distances[rrh_indices, pool_indices]
        variable_count = edge_count
        equality_rows = rrh_count
        equality_row = rrh_indices
        equality_col = np.arange(edge_count)
        equality_value = np.ones(edge_count, dtype=float)
        equality_rhs = np.ones(rrh_count, dtype=float)
    else:
        target = rrh_count / pool_count
        slot_count = rrh_count
        slot_indices = np.arange(pool_count * slot_count)
        slot_pools = slot_indices // slot_count
        slot_numbers = slot_indices % slot_count + 1
        marginal_penalties = penalty * (
            (slot_numbers - target) ** 2
            - (slot_numbers - 1 - target) ** 2
        )
        objective = np.concatenate((
            distances[rrh_indices, pool_indices],
            marginal_penalties,
        ))
        variable_count = edge_count + len(slot_indices)
        equality_rows = rrh_count + pool_count
        equality_row = np.concatenate((
            rrh_indices,
            rrh_count + pool_indices,
            rrh_count + slot_pools,
        ))
        equality_col = np.concatenate((
            np.arange(edge_count),
            np.arange(edge_count),
            edge_count + slot_indices,
        ))
        equality_value = np.concatenate((
            np.ones(edge_count, dtype=float),
            np.ones(edge_count, dtype=float),
            -np.ones(len(slot_indices), dtype=float),
        ))
        equality_rhs = np.concatenate((
            np.ones(rrh_count, dtype=float),
            np.zeros(pool_count, dtype=float),
        ))

    if penalty is None:
        equality_matrix = coo_matrix(
            (equality_value, (equality_row, equality_col)),
            shape=(equality_rows, variable_count),
        ).tocsr()
        inequality_matrix = None
        inequality_rhs = None
        if cap is not None:
            inequality_matrix = coo_matrix(
                (
                    np.ones(edge_count, dtype=float),
                    (pool_indices, np.arange(edge_count)),
                ),
                shape=(pool_count, variable_count),
            ).tocsr()
            inequality_rhs = np.full(pool_count, cap, dtype=float)
    else:
        equality_matrix = coo_matrix(
            (equality_value, (equality_row, equality_col)),
            shape=(equality_rows, variable_count),
        ).tocsr()
        inequality_matrix = None
        inequality_rhs = None

    result = linprog(
        objective,
        A_ub=inequality_matrix,
        b_ub=inequality_rhs,
        A_eq=equality_matrix,
        b_eq=equality_rhs,
        bounds=(0, 1),
        method="highs",
    )
    if not result.success:
        return None

    edge_values = result.x[:edge_count]
    chosen_edges = np.flatnonzero(edge_values > 0.5)
    if len(chosen_edges) != rrh_count:
        raise AssertionError("The assignment optimizer returned fractional RRH links.")
    selected_rrhs = rrh_indices[chosen_edges]
    if len(np.unique(selected_rrhs)) != rrh_count:
        raise AssertionError("The assignment optimizer assigned an RRH more than once.")
    assignment = np.full(rrh_count, -1, dtype=int)
    assignment[selected_rrhs] = pool_indices[chosen_edges]
    if (assignment < 0).any():
        raise AssertionError("The assignment optimizer left an RRH unassigned.")
    return assignment


def _minimum_feasible_cap(
    distances, pool_count, max_distance_km=DEFAULT_MAX_DISTANCE_KM
):
    low = int(np.ceil(len(distances) / pool_count))
    high = len(distances)
    while low < high:
        middle = (low + high) // 2
        if _make_assignment_lp(
            distances,
            pool_count,
            cap=middle,
            max_distance_km=max_distance_km,
        ) is None:
            low = middle + 1
        else:
            high = middle
    return low


def _assignment_metrics(rrhs, pools, assignment, distances, method):
    pool_ids = pools["bbu_pool"].tolist()
    chosen_distances = distances[np.arange(len(rrhs)), assignment]
    assigned_pools = np.asarray(pool_ids, dtype=object)[assignment]
    counts = pd.Series(assigned_pools).value_counts().reindex(pool_ids, fill_value=0)
    rows = pd.DataFrame({
        "rrh_id": rrhs["rrh_id"].to_numpy(),
        "bbu_pool_id": assigned_pools,
        "distance_km": chosen_distances,
    })
    metrics = {
        "method": method,
        "rrh_count": len(rrhs),
        "pool_count": len(pools),
        "mean_distance_km": float(chosen_distances.mean()),
        "max_distance_km": float(chosen_distances.max()),
        "total_distance_km": float(chosen_distances.sum()),
        "min_rrhs_per_pool": int(counts.min()),
        "max_rrhs_per_pool": int(counts.max()),
        "std_rrhs_per_pool": float(counts.std(ddof=0)),
        "rrh_count_coefficient_of_variation": float(
            counts.std(ddof=0) / counts.mean()
        ),
    }
    return rows, counts, metrics


def _joint_refinement(
    rrh_xy,
    pools,
    pool_centers,
    initial_assignment,
    cap,
    max_distance_km=DEFAULT_MAX_DISTANCE_KM,
):
    within_rrh_distances = _distance_matrix(rrh_xy, rrh_xy)
    assignment = initial_assignment.copy()
    centers = pool_centers.copy()
    pool_count = len(pools)

    for _ in range(MAX_JOINT_ITERATIONS):
        changed = False
        for pool_index in range(pool_count):
            members = np.flatnonzero(assignment == pool_index)
            candidates = np.flatnonzero(
                np.all(
                    within_rrh_distances[:, members]
                    <= max_distance_km + 1e-9,
                    axis=1,
                )
            )
            candidate_costs = within_rrh_distances[np.ix_(candidates, members)].sum(axis=1)
            best_center = int(candidates[np.argmin(candidate_costs)])
            old_center = int(np.argmin(
                np.linalg.norm(rrh_xy - centers[pool_index], axis=1)
            ))
            if best_center != old_center:
                centers[pool_index] = rrh_xy[best_center]
                changed = True

        updated_distances = _distance_matrix(rrh_xy, centers)
        updated_assignment = _make_assignment_lp(
            updated_distances,
            pool_count,
            cap=cap,
            max_distance_km=max_distance_km,
        )
        if updated_assignment is None:
            raise AssertionError(
                "The previous assignment should remain feasible after "
                "coverage-preserving medoid updates."
            )
        stable = np.array_equal(updated_assignment, assignment)
        assignment = updated_assignment
        if stable and not changed:
            break
    return assignment, centers


def _pool_rows(rrhs, pools, centers, rrh_xy, counts):
    pool_rows = []
    for index, pool in pools.iterrows():
        medoid_index = int(np.argmin(
            np.linalg.norm(rrh_xy - centers[index], axis=1)
        ))
        row = rrhs.iloc[medoid_index].copy()
        row["bbu_pool"] = pool["bbu_pool"]
        row["num_assigned_rrhs"] = int(counts.loc[pool["bbu_pool"]])
        pool_rows.append(row)
    return pd.DataFrame(pool_rows).reset_index(drop=True)


def _write_map(rrhs, rows, pool_rows, path):
    map_rrhs = rrhs.copy()
    by_id = rows.set_index("rrh_id")
    map_rrhs["bbu_pool"] = map_rrhs["rrh_id"].map(by_id["bbu_pool_id"])
    map_rrhs["distance_to_bbu_km"] = map_rrhs["rrh_id"].map(
        by_id["distance_km"]
    )
    generate_map(map_rrhs, pool_rows, str(path))


def _write_count_plot(count_table, pool_ids, rrh_count, output_dir):
    methods = [
        "Baseline",
        "Hard-cap assignment",
        "Soft-penalty assignment",
        "Joint capacitated k-median",
    ]
    pivot = count_table.pivot(
        index="bbu_pool", columns="method", values="rrh_count"
    ).reindex(pool_ids)
    x = np.arange(len(pool_ids))
    width = 0.2
    fig, axis = plt.subplots(figsize=(16, 7))
    for offset, method in enumerate(methods):
        axis.bar(
            x + (offset - 1.5) * width,
            pivot[method],
            width,
            label=method,
        )
    axis.axhline(
        count_table.loc[count_table["method"] == "Baseline", "rrh_count"].mean(),
        color="black",
        linestyle="--",
        linewidth=1,
        label="Mean RRHs per pool",
    )
    axis.set(
        xlabel="BBU Pool",
        ylabel="Assigned RRHs",
        title=f"RRHs per BBU Pool: K={len(pool_ids)}, {rrh_count} RRHs",
    )
    axis.set_xticks(x, pool_ids, rotation=60, ha="right")
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "rrhs_per_pool_comparison.png", dpi=200)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="Compare RRH-count balancing options for a K-median baseline."
    )
    parser.add_argument("--k", type=int, default=30)
    parser.add_argument("--assignment-file", type=Path)
    parser.add_argument("--pool-file", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--max-distance-km",
        type=float,
        default=DEFAULT_MAX_DISTANCE_KM,
    )
    args = parser.parse_args()
    if not np.isfinite(args.max_distance_km) or args.max_distance_km <= 0:
        parser.error("--max-distance-km must be a positive finite number.")

    baseline_dir = (
        ROOT / "results" / "phase2" / "baseline"
        if args.k == 30
        else ROOT / "output" / f"BBU-pool-allocation_k{args.k}"
    )
    assignment_file = args.assignment_file or (
        baseline_dir / f"rrh_bbu_k{args.k}_s100_kmedian_assignments.csv"
    )
    pool_file = args.pool_file or (
        baseline_dir / f"bbu_pool_k{args.k}_s100_kmedian_locations.csv"
    )
    default_output_name = (
        "bbu_balance_comparison"
        if args.k == 30
        else f"bbu_balance_comparison_k{args.k}"
    )
    output_dir = args.output_dir or ROOT / "tests" / default_output_name
    output_dir.mkdir(parents=True, exist_ok=True)
    rrhs, pools = _load_baseline(
        assignment_file, pool_file, args.max_distance_km
    )
    rrh_count = len(rrhs)
    pool_count = len(pools)
    lower_target, extra_pools = divmod(rrh_count, pool_count)
    upper_target = lower_target + (1 if extra_pools else 0)
    pool_ids = pools["bbu_pool"].tolist()
    rrh_xy = _project(rrhs["longitude"], rrhs["latitude"])
    initial_pool_xy = _project(pools["longitude"], pools["latitude"])
    initial_distances = _distance_matrix(rrh_xy, initial_pool_xy)

    hard_cap = _minimum_feasible_cap(
        initial_distances, pool_count, args.max_distance_km
    )
    hard_assignment = _make_assignment_lp(
        initial_distances,
        pool_count,
        cap=hard_cap,
        max_distance_km=args.max_distance_km,
    )
    if hard_assignment is None:
        raise RuntimeError(f"No feasible hard-cap assignment found at cap {hard_cap}.")

    soft_assignment = _make_assignment_lp(
        initial_distances,
        pool_count,
        penalty=SOFT_PENALTY_KM,
        max_distance_km=args.max_distance_km,
    )
    if soft_assignment is None:
        raise RuntimeError("The soft-penalty assignment optimization failed.")

    joint_assignment, joint_centers = _joint_refinement(
        rrh_xy,
        pools,
        initial_pool_xy,
        hard_assignment,
        hard_cap,
        max_distance_km=args.max_distance_km,
    )
    joint_distances = _distance_matrix(rrh_xy, joint_centers)

    methods = [
        (
            "Baseline",
            np.asarray([
                pool_ids.index(pool) for pool in rrhs["bbu_pool"].tolist()
            ]),
            initial_distances,
            initial_pool_xy,
        ),
        (
            "Hard-cap assignment",
            hard_assignment,
            initial_distances,
            initial_pool_xy,
        ),
        (
            "Soft-penalty assignment",
            soft_assignment,
            initial_distances,
            initial_pool_xy,
        ),
        (
            "Joint capacitated k-median",
            joint_assignment,
            joint_distances,
            joint_centers,
        ),
    ]
    summaries = []
    count_rows = []
    for method, assignment, distances, centers in methods:
        rows, counts, metrics = _assignment_metrics(
            rrhs, pools, assignment, distances, method
        )
        if rows["distance_km"].max() > args.max_distance_km + 1e-9:
            raise AssertionError(
                f"{method} violated the {args.max_distance_km:g} km limit."
            )
        if method == "Hard-cap assignment":
            metrics["hard_cap_rrhs_per_pool"] = hard_cap
        elif method == "Soft-penalty assignment":
            metrics["soft_penalty_km_per_rrh_squared"] = SOFT_PENALTY_KM
        else:
            metrics["hard_cap_rrhs_per_pool"] = (
                hard_cap if method == "Joint capacitated k-median" else ""
            )
        summaries.append(metrics)
        count_rows.extend([
            {
                "method": method,
                "bbu_pool": pool_id,
                "rrh_count": int(counts.loc[pool_id]),
            }
            for pool_id in pool_ids
        ])
        rows.to_csv(
            output_dir / f"{method.lower().replace(' ', '_')}_assignments.csv",
            index=False,
        )
        pool_rows = _pool_rows(rrhs, pools, centers, rrh_xy, counts)
        pool_rows.to_csv(
            output_dir / f"{method.lower().replace(' ', '_')}_pool_locations.csv",
            index=False,
        )
        metrics["bbu_locations_moved"] = int(np.count_nonzero(
            np.linalg.norm(centers - initial_pool_xy, axis=1) > 1e-6
        ))
        map_path = output_dir / f"{method.lower().replace(' ', '_')}_map.html"
        _write_map(rrhs, rows, pool_rows, map_path)

    summary = pd.DataFrame(summaries)
    counts = pd.DataFrame(count_rows)
    summary.to_csv(output_dir / "comparison_summary.csv", index=False)
    counts.to_csv(output_dir / "pool_rrh_counts.csv", index=False)
    _write_count_plot(counts, pool_ids, rrh_count, output_dir)
    (output_dir / "experiment_notes.txt").write_text(
        "\n".join([
            f"K={pool_count}, {rrh_count} RRHs; the baseline pool locations are used.",
            "All assignments enforce a maximum RRH-to-pool distance of "
            f"{args.max_distance_km:g} km.",
            f"Hard-cap assignment uses the smallest feasible upper cap: {hard_cap}; "
            f"a {lower_target}/{upper_target} split is infeasible at the existing locations.",
            "Soft-penalty assignment minimizes total distance plus "
            f"{SOFT_PENALTY_KM} km * sum((pool_count - {rrh_count}/{pool_count})^2).",
            "Joint capacitated k-median alternates capacity-constrained "
            "minimum-distance assignment and coverage-preserving medoid updates.",
        ]),
        encoding="utf-8",
    )
    print(summary.to_string(index=False))
    print(f"\nComparison outputs: {output_dir}")


if __name__ == "__main__":
    main()
