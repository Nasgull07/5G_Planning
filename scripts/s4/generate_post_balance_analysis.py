#!/usr/bin/env python3
"""Generate the visual comparisons requested after S4 load balancing."""

import argparse
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

if __package__:
    from .balance_traffic import (
        BASE_DIR,
        DEFAULT_HOUR,
        DEFAULT_LOAD_FILE,
        assign_rrhs,
        build_connection_records,
    )
    from .generate_pre_balance_baseline import (
        DEFAULT_SCHEDULE_FILE,
        build_weekday_normalized_profiles,
    )
else:
    from balance_traffic import (
        BASE_DIR,
        DEFAULT_HOUR,
        DEFAULT_LOAD_FILE,
        assign_rrhs,
        build_connection_records,
    )
    from generate_pre_balance_baseline import (
        DEFAULT_SCHEDULE_FILE,
        build_weekday_normalized_profiles,
    )


DEFAULT_CAPACITY_FILE = (
    BASE_DIR
    / "scripts"
    / "s4"
    / "rrh_bbu_k40_s100_zoned_balanced_kmedian_assignments_with_traffic_and_capacity.csv"
)
DEFAULT_OUTPUT_DIR = BASE_DIR / "data" / "processed" / "s4_post_balance"
DEPLOYMENT_SIZES = (30, 35, 40)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
METHODS = ("distance", "traffic", "rrh_count")
METHOD_LABELS = {
    "distance": "Solo distancia",
    "traffic": "Balanceo por tráfico",
    "rrh_count": "Balanceo por número de RRH",
}
TRAFFIC_CASES = ("All Residential", "All Office", "Real Case")
TRAFFIC_CASE_LABELS = {
    "All Residential": "Todas Residential",
    "All Office": "Todas Office",
    "Real Case": "Escenario real",
}
COLORS = {
    "distance": "#777777",
    "traffic": "#2878B5",
    "rrh_count": "#F28E2B",
}
TRAFFIC_CASE_COLORS = {
    "All Residential": "#D55E00",
    "All Office": "#0072B2",
    "Real Case": "#009E73",
}


def _assignment_path(k):
    return (
        BASE_DIR
        / "scripts"
        / "s2"
        / "output"
        / f"BBU-pool-allocation_k{k}_zoned_balanced"
        / f"rrh_bbu_k{k}_s100_zoned_balanced_kmedian_assignments.csv"
    )


def read_weekday_load_matrix(load_file, rrh_ids):
    """Return one weekday-mean load row per time and RRH."""
    required = {"rrh_id", "day_of_week", "time", "traffic_load_mbph"}
    loads = pd.read_csv(
        load_file,
        usecols=lambda column: column in required,
        dtype={"rrh_id": str, "day_of_week": str, "time": str},
    )
    missing = required - set(loads.columns)
    if missing:
        raise ValueError(f"Traffic loads are missing columns: {sorted(missing)}")
    loads = loads.loc[
        loads["day_of_week"].isin(WEEKDAYS),
        ["rrh_id", "day_of_week", "time", "traffic_load_mbph"],
    ].copy()
    if loads.empty or loads[["rrh_id", "day_of_week", "time"]].isna().any().any():
        raise ValueError("Weekday traffic data is empty or has missing sample keys.")
    if loads.duplicated(["rrh_id", "day_of_week", "time"]).any():
        raise ValueError("Weekday traffic data contains duplicate samples.")

    expected_ids = {str(rrh_id) for rrh_id in rrh_ids}
    observed_ids = set(loads["rrh_id"])
    if expected_ids != observed_ids:
        raise ValueError(
            "Traffic-load RRHs do not match the assignments; "
            f"missing={len(expected_ids - observed_ids)}, "
            f"unexpected={len(observed_ids - expected_ids)}."
        )
    loads["traffic_load_mbph"] = pd.to_numeric(
        loads["traffic_load_mbph"], errors="raise"
    )
    values = loads["traffic_load_mbph"].to_numpy()
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Weekday traffic loads must be finite and non-negative.")
    day_counts = loads.groupby(["time", "rrh_id"])["day_of_week"].nunique()
    times = sorted(loads["time"].unique())
    if len(times) != 144 or len(day_counts) != 144 * len(expected_ids):
        raise ValueError("Every RRH must have all 144 weekday time samples.")
    if (day_counts != len(WEEKDAYS)).any():
        raise ValueError("Every RRH/time sample must include all five weekdays.")

    matrix = (
        loads.groupby(["time", "rrh_id"])["traffic_load_mbph"]
        .mean()
        .unstack("rrh_id")
        .reindex(index=times, columns=sorted(expected_ids))
    )
    if matrix.isna().any().any():
        raise ValueError("Weekday mean traffic matrix contains missing values.")
    return matrix


def make_distance_assignment(assignments, load_map):
    """Normalize the existing distance-only assignment to the analysis schema."""
    required = {"rrh_id", "site_id", "bbu_pool", "distance_to_bbu_km"}
    missing = required - set(assignments.columns)
    if missing:
        raise ValueError(
            f"Initial assignment is missing columns: {sorted(missing)}"
        )
    if assignments["rrh_id"].isna().any() or assignments["rrh_id"].duplicated().any():
        raise ValueError("Initial assignment RRH IDs must be present and unique.")
    result = assignments[
        ["rrh_id", "site_id", "bbu_pool", "distance_to_bbu_km"]
    ].copy()
    result = result.rename(columns={
        "bbu_pool": "bbu_pool_id",
        "distance_to_bbu_km": "distance_km",
    })
    result["rrh_id"] = result["rrh_id"].astype(str)
    result["site_id"] = result["site_id"].astype(str)
    if set(result["rrh_id"]) != set(load_map):
        raise ValueError("Initial assignment and traffic-load RRH IDs differ.")
    result["traffic_load_mbph"] = result["rrh_id"].map(load_map)
    return result


def aggregate_pool_metrics(assignment, load_map, capacity_map, pool_ids):
    """Aggregate assigned RRHs, load, capacity, and distance for each pool."""
    required = {"rrh_id", "bbu_pool_id", "distance_km"}
    missing = required - set(assignment.columns)
    if missing:
        raise ValueError(f"Assignment is missing columns: {sorted(missing)}")
    if assignment["rrh_id"].isna().any() or assignment["rrh_id"].duplicated().any():
        raise ValueError("Each assignment must contain unique, non-empty RRH IDs.")
    if set(assignment["rrh_id"].astype(str)) != set(load_map):
        raise ValueError("Assignment and load RRH IDs do not match.")
    if set(load_map) != set(capacity_map):
        raise ValueError("Load and capacity RRH IDs do not match.")

    values = assignment[["rrh_id", "bbu_pool_id", "distance_km"]].copy()
    values["rrh_id"] = values["rrh_id"].astype(str)
    values["distance_km"] = pd.to_numeric(values["distance_km"], errors="raise")
    values["traffic_load_mbph"] = values["rrh_id"].map(load_map)
    values["phy_capacity_max_mbph"] = values["rrh_id"].map(capacity_map)
    numbers = values[
        ["distance_km", "traffic_load_mbph", "phy_capacity_max_mbph"]
    ].to_numpy(dtype=float)
    if not np.isfinite(numbers).all() or (numbers < 0).any():
        raise ValueError("Distance, traffic, and capacity values must be finite and non-negative.")

    result = values.groupby("bbu_pool_id").agg(
        rrh_count=("rrh_id", "size"),
        traffic_load_mbph=("traffic_load_mbph", "sum"),
        capacity_mbph=("phy_capacity_max_mbph", "sum"),
        mean_distance_km=("distance_km", "mean"),
    ).reindex(pool_ids).fillna(0)
    result.index.name = "bbu_pool_id"
    if int(result["rrh_count"].sum()) != len(assignment):
        raise AssertionError("Pool aggregation lost or duplicated RRHs.")
    if not math.isclose(
        float(result["traffic_load_mbph"].sum()),
        float(sum(load_map.values())),
        rel_tol=1e-12,
        abs_tol=1e-5,
    ):
        raise AssertionError("Pool aggregation did not conserve traffic load.")
    if not math.isclose(
        float(result["capacity_mbph"].sum()),
        float(sum(capacity_map.values())),
        rel_tol=1e-12,
        abs_tol=1e-5,
    ):
        raise AssertionError("Pool aggregation did not conserve PHY capacity.")
    return result


def build_deployment_metrics(k, method, pool_metrics, assignment, network_load):
    loads = pool_metrics["traffic_load_mbph"].to_numpy(dtype=float)
    capacities = pool_metrics["capacity_mbph"].to_numpy(dtype=float)
    counts = pool_metrics["rrh_count"].to_numpy(dtype=float)
    mean_load = float(loads.mean())
    mean_capacity = float(capacities.mean())
    mean_count = float(counts.mean())
    return {
        "k": k,
        "method": METHOD_LABELS[method],
        "pool_count": len(pool_metrics),
        "rrh_count": len(assignment),
        "network_load_mbph": network_load,
        "max_pool_load_mbph": float(loads.max()),
        "mean_pool_load_mbph": mean_load,
        "std_pool_load_mbph": float(loads.std()),
        "load_coefficient_of_variation": (
            float(loads.std() / mean_load) if mean_load else 0.0
        ),
        "rrh_count_coefficient_of_variation": (
            float(counts.std() / mean_count) if mean_count else 0.0
        ),
        "mean_pool_capacity_mbph": mean_capacity,
        "std_pool_capacity_mbph": float(capacities.std()),
        "capacity_coefficient_of_variation": (
            float(capacities.std() / mean_capacity) if mean_capacity else 0.0
        ),
        "mean_distance_km": float(assignment["distance_km"].mean()),
    }


def _distance_bins(max_distance_km):
    upper = max(1, int(math.ceil(max_distance_km)))
    edges = [-1e-12] + [float(value) for value in range(1, upper + 1)]
    edges[-1] += 1e-9
    labels = [f"{value}-{value + 1} km" for value in range(upper)]
    return edges, labels


def build_distance_distribution(pool_assignments):
    max_distance = max(
        float(pd.to_numeric(frame["distance_km"], errors="raise").max())
        for by_method in pool_assignments.values()
        for frame in by_method.values()
    )
    edges, labels = _distance_bins(max_distance)
    rows = []
    for k, by_method in pool_assignments.items():
        for method, assignment in by_method.items():
            distances = pd.to_numeric(assignment["distance_km"], errors="raise")
            bins = pd.cut(
                distances,
                bins=edges,
                labels=labels,
                include_lowest=True,
                right=True,
            )
            counts = bins.value_counts().reindex(labels, fill_value=0)
            if int(counts.sum()) != len(assignment):
                raise AssertionError("Distance bins did not account for every RRH.")
            for label, count in counts.items():
                rows.append({
                    "k": k,
                    "method": METHOD_LABELS[method],
                    "distance_bin": label,
                    "rrh_count": int(count),
                })
    return pd.DataFrame(rows), labels


def hourly_assignment_changes(peak_assignment, hourly_assignment, hour, k):
    peak_pool = peak_assignment.set_index("rrh_id")["bbu_pool_id"]
    hourly_pool = hourly_assignment.set_index("rrh_id")["bbu_pool_id"]
    if set(peak_pool.index) != set(hourly_pool.index):
        raise ValueError("Peak and hourly assignments must contain the same RRHs.")
    changed = int((peak_pool != hourly_pool.reindex(peak_pool.index)).sum())
    return {
        "k": k,
        "hour": hour,
        "changed_rrhs": changed,
        "changed_percent": changed / len(peak_pool) * 100,
    }


def _plot_rrh_counts(pool_metrics, output_dir):
    fig, axes = plt.subplots(1, len(DEPLOYMENT_SIZES), figsize=(16, 5), sharey=True)
    for axis, k in zip(axes, DEPLOYMENT_SIZES):
        subset = pool_metrics.loc[pool_metrics["k"] == k]
        pivot = subset.pivot(
            index="bbu_pool_id", columns="method", values="rrh_count"
        ).fillna(0)
        for method in METHODS:
            label = METHOD_LABELS[method]
            ranked = np.sort(pivot[label].to_numpy())[::-1]
            axis.plot(
                np.arange(1, len(ranked) + 1),
                ranked,
                marker="o",
                markersize=3,
                label=label,
                color=COLORS[method],
            )
        axis.set_title(f"K={k}")
        axis.set_xlabel("Rango del pool (más RRH a menos)")
        axis.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("RRH asignadas")
    axes[-1].legend(fontsize=8)
    fig.suptitle("Distribución de RRH por pool: inicial y balanceos")
    fig.tight_layout()
    fig.savefig(output_dir / "rrhs_per_pool_by_deployment.png", dpi=250)
    plt.close(fig)


def _plot_hourly_changes(hourly_changes, output_dir, adopted_hour):
    fig, axis = plt.subplots(figsize=(15, 6))
    times = sorted(hourly_changes["hour"].unique())
    positions = np.arange(len(times))
    for k in DEPLOYMENT_SIZES:
        rows = hourly_changes.loc[hourly_changes["k"] == k].set_index("hour")
        rows = rows.reindex(times)
        axis.plot(
            positions,
            rows["changed_rrhs"],
            linewidth=1.8,
            label=f"K={k}",
        )
    if adopted_hour in times:
        peak_position = times.index(adopted_hour)
        axis.axvline(
            peak_position,
            color="#333333",
            linestyle="--",
            linewidth=1,
            label=f"Hora adoptada: {adopted_hour}",
        )
    tick_positions = np.arange(0, len(times), 12)
    axis.set_xticks(tick_positions, [times[index] for index in tick_positions])
    axis.set(
        xlabel="Hora del día (muestra de 10 minutos)",
        ylabel="RRH asignadas a otro pool respecto al pico",
        title=f"Cambios de asignación respecto a {adopted_hour}",
    )
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "hourly_assignment_changes.png", dpi=250)
    plt.close(fig)


def _plot_profile_sensitivity(profile_metrics, output_dir, hour):
    fig, axes = plt.subplots(
        len(DEPLOYMENT_SIZES),
        len(METHODS),
        figsize=(16, 12),
        sharex=True,
        sharey=True,
    )
    for row_index, k in enumerate(DEPLOYMENT_SIZES):
        for column_index, method in enumerate(METHODS):
            axis = axes[row_index, column_index]
            subset = profile_metrics.loc[
                (profile_metrics["k"] == k)
                & (profile_metrics["method"] == METHOD_LABELS[method])
            ]
            for case in TRAFFIC_CASES:
                values = subset.loc[
                    subset["traffic_case"] == case, "traffic_load_mbph"
                ].to_numpy(dtype=float)
                values = np.sort(values)[::-1] / 1_000_000
                axis.plot(
                    np.arange(1, len(values) + 1),
                    values,
                    marker=".",
                    markersize=3,
                    linewidth=1.5,
                    label=TRAFFIC_CASE_LABELS[case],
                    color=TRAFFIC_CASE_COLORS[case],
                )
            if row_index == 0:
                axis.set_title(METHOD_LABELS[method])
            if column_index == 0:
                axis.set_ylabel(f"K={k}\nCarga por pool (TB/h)")
            if row_index == len(DEPLOYMENT_SIZES) - 1:
                axis.set_xlabel("Rango del pool (mayor carga a menor)")
            axis.grid(alpha=0.2)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.96),
        ncol=3,
    )
    fig.suptitle(
        f"Balanceo bajo perfiles de tráfico alternativos ({hour})",
        y=0.995,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.91))
    fig.savefig(output_dir / "traffic_profile_sensitivity.png", dpi=250)
    plt.close(fig)


def _plot_distance_distribution(distance_distribution, labels, output_dir):
    fig, axes = plt.subplots(1, len(DEPLOYMENT_SIZES), figsize=(17, 5), sharey=True)
    width = 0.25
    for axis, k in zip(axes, DEPLOYMENT_SIZES):
        subset = distance_distribution.loc[distance_distribution["k"] == k]
        pivot = subset.pivot(
            index="distance_bin", columns="method", values="rrh_count"
        ).reindex(labels).fillna(0)
        positions = np.arange(len(labels))
        for offset, method in enumerate(METHODS):
            axis.bar(
                positions + (offset - 1) * width,
                pivot[METHOD_LABELS[method]],
                width,
                color=COLORS[method],
                label=METHOD_LABELS[method],
            )
        axis.set_title(f"K={k}")
        axis.set_xticks(positions, labels, rotation=35, ha="right")
        axis.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("RRH asignadas")
    axes[-1].legend(fontsize=8)
    fig.suptitle("RRH por intervalo de distancia al BBU pool")
    fig.tight_layout()
    fig.savefig(output_dir / "distance_distribution_by_deployment.png", dpi=250)
    plt.close(fig)


def _plot_pool_capacity(pool_metrics, output_dir):
    fig, axes = plt.subplots(1, len(DEPLOYMENT_SIZES), figsize=(16, 5), sharey=True)
    for axis, k in zip(axes, DEPLOYMENT_SIZES):
        subset = pool_metrics.loc[pool_metrics["k"] == k]
        for method in METHODS:
            values = subset.loc[
                subset["method"] == METHOD_LABELS[method], "capacity_mbph"
            ].to_numpy(dtype=float)
            values = np.sort(values)[::-1] / 1_000_000
            axis.plot(
                np.arange(1, len(values) + 1),
                values,
                marker="o",
                markersize=3,
                linewidth=1.5,
                label=METHOD_LABELS[method],
                color=COLORS[method],
            )
        mean_capacity = (
            subset.loc[
                subset["method"] == METHOD_LABELS["distance"],
                "capacity_mbph",
            ].mean()
            / 1_000_000
        )
        axis.axhline(
            mean_capacity,
            color="#444444",
            linestyle=":",
            linewidth=1,
            label="Media por pool",
        )
        axis.set_title(f"K={k}")
        axis.set_xlabel("Rango del pool (mayor capacidad a menor)")
        axis.grid(alpha=0.25)
    axes[0].set_ylabel("Capacidad PHY agregada por pool (TB/h)")
    axes[-1].legend(fontsize=8)
    fig.suptitle("Capacidad PHY asignada por pool (suma de sus RRH)")
    fig.tight_layout()
    fig.savefig(output_dir / "pool_capacity_by_deployment.png", dpi=250)
    plt.close(fig)


def _plot_deployment_metrics(metrics, output_dir):
    metric_specs = (
        ("load_coefficient_of_variation", "CV de carga de tráfico", None),
        (
            "rrh_count_coefficient_of_variation",
            "CV del número de RRH",
            None,
        ),
        (
            "mean_pool_capacity_mbph",
            "Capacidad PHY media por pool (TB/h)",
            "std_pool_capacity_mbph",
        ),
        ("mean_distance_km", "Distancia media (km)", None),
    )
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    positions = np.arange(len(DEPLOYMENT_SIZES))
    width = 0.24
    for axis, (metric, title, error_metric) in zip(axes.flat, metric_specs):
        for offset, method in enumerate(METHODS):
            rows = (
                metrics.loc[metrics["method"] == METHOD_LABELS[method]]
                .set_index("k")
                .reindex(DEPLOYMENT_SIZES)
            )
            values = rows[metric].to_numpy(dtype=float)
            if metric.endswith("_mbph"):
                values = values / 1_000_000
            errors = None
            if error_metric:
                errors = rows[error_metric].to_numpy(dtype=float) / 1_000_000
            axis.bar(
                positions + (offset - 1) * width,
                values,
                width,
                yerr=errors,
                capsize=3 if errors is not None else 0,
                color=COLORS[method],
                label=METHOD_LABELS[method],
            )
        axis.set_title(title)
        axis.set_xticks(positions, [f"K={k}" for k in DEPLOYMENT_SIZES])
        axis.grid(axis="y", alpha=0.25)
    axes[0, 0].set_ylabel("Desviación estándar / media")
    axes[0, 1].set_ylabel("Desviación estándar / media")
    axes[1, 0].set_ylabel("Capacidad agregada")
    axes[1, 1].set_ylabel("Kilómetros")
    axes[1, 1].legend(fontsize=8)
    fig.suptitle("Comparación global de los despliegues y métodos")
    fig.tight_layout()
    fig.savefig(output_dir / "deployment_metrics.png", dpi=250)
    plt.close(fig)


def _plot_ranked_loads(pool_metrics, output_dir, hour):
    fig, axes = plt.subplots(1, len(DEPLOYMENT_SIZES), figsize=(16, 5), sharey=True)
    for axis, k in zip(axes, DEPLOYMENT_SIZES):
        subset = pool_metrics.loc[pool_metrics["k"] == k]
        for method in METHODS:
            values = subset.loc[
                subset["method"] == METHOD_LABELS[method],
                "traffic_load_mbph",
            ].to_numpy(dtype=float)
            values = np.sort(values)[::-1] / 1_000_000
            axis.plot(
                np.arange(1, len(values) + 1),
                values,
                marker="o",
                markersize=3,
                linewidth=1.5,
                label=METHOD_LABELS[method],
                color=COLORS[method],
            )
        axis.set_title(f"K={k}")
        axis.set_xlabel("Rango del pool (mayor carga a menor)")
        axis.grid(alpha=0.25)
    axes[0].set_ylabel("Carga media (TB/h)")
    axes[-1].legend(fontsize=8)
    fig.suptitle(f"Carga por pool ordenada por rango a las {hour}")
    fig.tight_layout()
    fig.savefig(output_dir / "ranked_pool_loads_by_deployment.png", dpi=250)
    plt.close(fig)


def run_post_balance_analysis(
    hour=DEFAULT_HOUR,
    output_dir=DEFAULT_OUTPUT_DIR,
    load_file=DEFAULT_LOAD_FILE,
    capacity_file=DEFAULT_CAPACITY_FILE,
    schedule_file=DEFAULT_SCHEDULE_FILE,
):
    """Run hourly, traffic-profile, distance, capacity, and K comparisons."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    capacities = pd.read_csv(
        capacity_file,
        usecols=["rrh_id", "phy_capacity_max_mbph"],
        dtype={"rrh_id": str},
    )
    if capacities["rrh_id"].isna().any() or capacities["rrh_id"].duplicated().any():
        raise ValueError("RRH capacity IDs must be present and unique.")
    capacity_values = pd.to_numeric(
        capacities["phy_capacity_max_mbph"], errors="raise"
    )
    if not np.isfinite(capacity_values).all() or (capacity_values <= 0).any():
        raise ValueError("RRH PHY capacities must be finite and positive.")
    capacity_map = dict(zip(capacities["rrh_id"], capacity_values))
    rrh_ids = set(capacity_map)
    load_matrix = read_weekday_load_matrix(load_file, rrh_ids)
    if hour not in load_matrix.index:
        raise ValueError(
            f"Hour {hour!r} is not a ten-minute sample in the traffic data."
        )
    real_load_map = load_matrix.loc[hour].to_dict()
    network_load = float(sum(real_load_map.values()))

    schedule = pd.read_csv(schedule_file)
    normalized_profiles = build_weekday_normalized_profiles(schedule).set_index(
        "time"
    )
    if hour not in normalized_profiles.index:
        raise ValueError(f"Hour {hour!r} is missing from the traffic schedule.")
    profile_load_maps = {"Real Case": real_load_map}
    for case, profile in (
        ("All Residential", "Residential"),
        ("All Office", "Office"),
    ):
        factor = float(normalized_profiles.loc[hour, profile])
        profile_load_maps[case] = {
            rrh_id: float(capacity_map[rrh_id] * factor)
            for rrh_id in rrh_ids
        }

    assignments_by_k = {}
    records_by_k = {}
    pool_ids_by_k = {}
    pool_metric_frames = []
    deployment_metric_rows = []
    count_rows = []
    profile_metric_rows = []
    hourly_change_rows = []

    for k in DEPLOYMENT_SIZES:
        source = pd.read_csv(
            _assignment_path(k),
            dtype={
                "rrh_id": str,
                "site_id": str,
                "bbu_pool": str,
                "feasible_bbu_pools": str,
                "feasible_distances_km": str,
            },
        )
        if set(source["rrh_id"].astype(str)) != rrh_ids:
            raise ValueError(f"K={k} assignment RRHs do not match capacity data.")
        records = build_connection_records(source)
        pool_ids = sorted(source["bbu_pool"].astype(str).unique())
        if len(pool_ids) != k:
            raise ValueError(f"Expected {k} pools in K={k}, found {len(pool_ids)}.")
        records_by_k[k] = records
        pool_ids_by_k[k] = pool_ids

        initial_assignment = make_distance_assignment(source, real_load_map)
        assignments = {
            "distance": initial_assignment,
            "traffic": assign_rrhs(records, real_load_map, method="traffic"),
            "rrh_count": assign_rrhs(records, real_load_map, method="rrh_count"),
        }
        assignments_by_k[k] = assignments

        scenario_dir = output_dir / f"k{k}"
        scenario_dir.mkdir(parents=True, exist_ok=True)
        for method, assignment in assignments.items():
            assignment.to_csv(
                scenario_dir / f"assignment_{method}_{hour.replace(':', '')}.csv",
                index=False,
            )
            pool_metrics = aggregate_pool_metrics(
                assignment, real_load_map, capacity_map, pool_ids
            )
            frame = pool_metrics.reset_index()
            frame.insert(0, "method", METHOD_LABELS[method])
            frame.insert(0, "k", k)
            pool_metric_frames.append(frame)
            count_rows.extend(
                {
                    "k": k,
                    "method": METHOD_LABELS[method],
                    "bbu_pool_id": row.bbu_pool_id,
                    "rrh_count": int(row.rrh_count),
                }
                for row in frame.itertuples(index=False)
            )
            deployment_metric_rows.append(
                build_deployment_metrics(
                    k,
                    method,
                    pool_metrics,
                    assignment,
                    network_load,
                )
            )

        peak_traffic_assignment = assignments["traffic"]
        for time, row in load_matrix.iterrows():
            hour_assignment = assign_rrhs(
                records, row.to_dict(), method="traffic"
            )
            hourly_change_rows.append(
                hourly_assignment_changes(
                    peak_traffic_assignment,
                    hour_assignment,
                    time,
                    k,
                )
            )

        for case, load_map in profile_load_maps.items():
            for method in METHODS:
                assignment = (
                    assignments[method]
                    if case == "Real Case"
                    else (
                        initial_assignment
                        if method == "distance"
                        else assign_rrhs(records, load_map, method=method)
                    )
                )
                pool_metrics = aggregate_pool_metrics(
                    assignment, load_map, capacity_map, pool_ids
                ).reset_index()
                for row in pool_metrics.itertuples(index=False):
                    profile_metric_rows.append({
                        "k": k,
                        "method": METHOD_LABELS[method],
                        "traffic_case": case,
                        "bbu_pool_id": row.bbu_pool_id,
                        "rrh_count": int(row.rrh_count),
                        "traffic_load_mbph": float(row.traffic_load_mbph),
                        "capacity_mbph": float(row.capacity_mbph),
                        "mean_distance_km": float(row.mean_distance_km),
                    })

    pool_metrics = pd.concat(pool_metric_frames, ignore_index=True)
    deployment_metrics = pd.DataFrame(deployment_metric_rows)
    rrh_counts = pd.DataFrame(count_rows)
    profile_metrics = pd.DataFrame(profile_metric_rows)
    hourly_changes = pd.DataFrame(hourly_change_rows)
    distance_distribution, distance_labels = build_distance_distribution(
        assignments_by_k
    )

    pool_metrics.to_csv(output_dir / "pool_metrics_at_peak.csv", index=False)
    deployment_metrics.to_csv(
        output_dir / "deployment_metrics.csv", index=False
    )
    rrh_counts.to_csv(output_dir / "rrhs_per_pool.csv", index=False)
    profile_metrics.to_csv(
        output_dir / "traffic_profile_sensitivity_by_pool.csv", index=False
    )
    hourly_changes.to_csv(
        output_dir / "hourly_assignment_changes.csv", index=False
    )
    distance_distribution.to_csv(
        output_dir / "distance_distribution_by_deployment.csv", index=False
    )

    _plot_rrh_counts(pool_metrics, output_dir)
    _plot_hourly_changes(hourly_changes, output_dir, hour)
    _plot_profile_sensitivity(profile_metrics, output_dir, hour)
    _plot_distance_distribution(distance_distribution, distance_labels, output_dir)
    _plot_pool_capacity(pool_metrics, output_dir)
    _plot_deployment_metrics(deployment_metrics, output_dir)
    _plot_ranked_loads(pool_metrics, output_dir, hour)
    return {
        "hour": hour,
        "rrh_count": len(rrh_ids),
        "network_load_mbph": network_load,
        "pool_metrics": pool_metrics,
        "deployment_metrics": deployment_metrics,
        "hourly_changes": hourly_changes,
        "output_dir": output_dir,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate post-balancing S4 visual comparisons for K=30, 35, and 40."
        )
    )
    parser.add_argument(
        "-hour",
        "--hour",
        default=DEFAULT_HOUR,
        metavar="HH:MM",
        help="Ten-minute sample to analyze (default: %(default)s).",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--loads", type=Path, default=DEFAULT_LOAD_FILE)
    parser.add_argument("--capacities", type=Path, default=DEFAULT_CAPACITY_FILE)
    parser.add_argument("--schedule", type=Path, default=DEFAULT_SCHEDULE_FILE)
    args = parser.parse_args()

    result = run_post_balance_analysis(
        hour=args.hour,
        output_dir=args.output_dir,
        load_file=args.loads,
        capacity_file=args.capacities,
        schedule_file=args.schedule,
    )
    print(
        f"Analyzed {result['rrh_count']} RRHs at {result['hour']} "
        f"({result['network_load_mbph'] / 1_000_000:.3f} TB/h)."
    )
    print(f"Saved post-balancing tables and figures to {result['output_dir']}")


if __name__ == "__main__":
    main()
