#!/usr/bin/env python3
"""Greedy traffic- and RRH-count-based BBU Pool assignment algorithms."""

import argparse
import json
import math
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[3]
DEFAULT_SCENARIO_FILE = BASE_DIR / "data" / "processed" / "scenario_sites.csv"
DEFAULT_ASSIGNMENT_FILE = (
    BASE_DIR
    / "results"
    / "phase2"
    / "baseline"
    / "rrh_bbu_k30_s100_kmedian_assignments.csv"
)
DEFAULT_PROFILE_FILE = (
    BASE_DIR / "results" / "phase2" / "baseline" / "weekday_traffic_profiles.csv"
)
DEFAULT_LOAD_FILE = BASE_DIR / "data" / "processed" / "rrh_traffic_load.csv"
DEFAULT_OUTPUT_DIR = BASE_DIR / "results" / "phase2" / "balancing"

WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}
SENSITIVITY_HOURS = ("08:00", "15:00", "21:00")
MAX_DISTANCE_KM = 4.0
ASSIGNMENT_COLUMNS = ["rrh_id", "bbu_pool_id", "distance_km"]


def _extract_site_id(rrh_id):
    value = str(rrh_id).strip()
    if value.startswith("[") and value.endswith("]"):
        parsed = json.loads(value)
        if not isinstance(parsed, list) or not parsed:
            raise ValueError(f"Invalid source RRH ID: {rrh_id!r}")
        return str(parsed[0]).strip()
    return value


def _parse_array(value, field, rrh_id):
    if isinstance(value, str):
        try:
            values = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSON in {field} for RRH {rrh_id!r}."
            ) from error
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise ValueError(f"Missing or invalid {field} for RRH {rrh_id!r}.")
    if not isinstance(values, list) or not values:
        raise ValueError(f"{field} must be a non-empty array for RRH {rrh_id!r}.")
    return values


def build_connection_matrix(scenario, phase1_assignment):
    """Attach the Phase 1 feasible-pool arrays to original scenario RRH IDs."""
    scenario_required = {"rrh_id"}
    assignment_required = {
        "site_id",
        "feasible_bbu_pools",
        "feasible_distances_km",
    }
    missing_scenario = scenario_required - set(scenario.columns)
    missing_assignment = assignment_required - set(phase1_assignment.columns)
    if missing_scenario:
        raise ValueError(
            f"Scenario is missing columns: {sorted(missing_scenario)}"
        )
    if missing_assignment:
        raise ValueError(
            f"Phase 1 assignment is missing columns: {sorted(missing_assignment)}"
        )

    site_map = scenario[["rrh_id"]].copy()
    if site_map["rrh_id"].isna().any() or site_map["rrh_id"].duplicated().any():
        raise ValueError("Scenario RRH IDs must be present and unique.")
    site_map["rrh_id"] = site_map["rrh_id"].astype(str)
    site_map["site_id"] = site_map["rrh_id"].map(_extract_site_id)

    pool_map = phase1_assignment[
        ["site_id", "feasible_bbu_pools", "feasible_distances_km"]
    ].copy()
    pool_map["site_id"] = pool_map["site_id"].astype(str)
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
            "Some scenario RRHs have no Phase 1 connection list; "
            f"example site IDs: {missing_ids}"
        )
    return mapped[
        ["rrh_id", "feasible_bbu_pools", "feasible_distances_km"]
    ]


def weekday_mean_loads(load_data, time):
    """Return one RRH load per ID: the mean of Monday-Friday at ``time``."""
    required = {"rrh_id", "day_of_week", "time", "traffic_load_mbph"}
    missing = required - set(load_data.columns)
    if missing:
        raise ValueError(f"Traffic-load data is missing columns: {sorted(missing)}")
    selected = load_data.loc[
        load_data["day_of_week"].isin(WEEKDAYS)
        & (load_data["time"] == time),
        ["rrh_id", "day_of_week", "time", "traffic_load_mbph"],
    ].copy()
    if selected.empty:
        raise ValueError(f"No weekday traffic-load samples found for {time}.")
    if selected[["rrh_id", "day_of_week"]].duplicated().any():
        raise ValueError(f"Duplicate weekday load samples found for {time}.")
    selected["traffic_load_mbph"] = pd.to_numeric(
        selected["traffic_load_mbph"], errors="raise"
    )
    if not selected["traffic_load_mbph"].map(math.isfinite).all():
        raise ValueError(f"Non-finite traffic loads found for {time}.")
    if (selected["traffic_load_mbph"] < 0).any():
        raise ValueError(f"Negative traffic loads found for {time}.")

    weekday_counts = selected.groupby("rrh_id")["day_of_week"].nunique()
    if (weekday_counts != len(WEEKDAYS)).any():
        raise ValueError(
            f"Every RRH must have one load for each weekday at {time}."
        )
    means = (
        selected.groupby("rrh_id", as_index=False)["traffic_load_mbph"]
        .mean()
    )
    return means


def detect_peak_time(profile_summary):
    required = {"time", "combined_normalized_sum"}
    missing = required - set(profile_summary.columns)
    if missing:
        raise ValueError(
            f"Weekday profile summary is missing columns: {sorted(missing)}"
        )
    values = pd.to_numeric(
        profile_summary["combined_normalized_sum"], errors="raise"
    )
    if values.empty or not values.map(math.isfinite).all():
        raise ValueError("Weekday profile summary has no finite peak samples.")
    return str(profile_summary.loc[values.idxmax(), "time"])


def _normalize_connections(connections):
    required = {
        "rrh_id",
        "feasible_bbu_pools",
        "feasible_distances_km",
    }
    missing = required - set(connections.columns)
    if missing:
        raise ValueError(
            f"Connection matrix is missing columns: {sorted(missing)}"
        )
    if connections["rrh_id"].isna().any() or connections["rrh_id"].duplicated().any():
        raise ValueError("Connection-matrix RRH IDs must be present and unique.")

    records = []
    for row in connections.to_dict("records"):
        rrh_id = str(row["rrh_id"])
        pools = _parse_array(
            row["feasible_bbu_pools"], "feasible_bbu_pools", rrh_id
        )
        distances = _parse_array(
            row["feasible_distances_km"], "feasible_distances_km", rrh_id
        )
        if len(pools) != len(distances):
            raise ValueError(
                f"Pool and distance arrays have different lengths for {rrh_id!r}."
            )
        pools = [str(pool).strip() for pool in pools]
        if any(not pool for pool in pools) or len(set(pools)) != len(pools):
            raise ValueError(f"Invalid or duplicate BBU Pool IDs for {rrh_id!r}.")
        try:
            distances = [float(distance) for distance in distances]
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Invalid candidate distance for RRH {rrh_id!r}."
            ) from error
        if any(
            not math.isfinite(distance)
            or distance < 0
            or distance > MAX_DISTANCE_KM + 1e-9
            for distance in distances
        ):
            raise ValueError(
                f"Candidate distances for {rrh_id!r} must be finite and "
                f"within {MAX_DISTANCE_KM:g} km."
            )
        records.append({
            "rrh_id": rrh_id,
            "feasible_bbu_pools": pools,
            "feasible_distances_km": distances,
            "option_count": len(pools),
            "minimum_distance_km": min(distances),
        })
    if not records:
        raise ValueError("Connection matrix contains no RRHs.")
    return records


def _normalize_load_map(loads, rrh_ids):
    required = {"rrh_id", "traffic_load_mbph"}
    missing = required - set(loads.columns)
    if missing:
        raise ValueError(f"RRH loads are missing columns: {sorted(missing)}")
    if loads["rrh_id"].isna().any() or loads["rrh_id"].duplicated().any():
        raise ValueError("There must be exactly one load row per RRH.")
    load_values = pd.to_numeric(loads["traffic_load_mbph"], errors="raise")
    if not load_values.map(math.isfinite).all() or (load_values < 0).any():
        raise ValueError("RRH loads must be finite and non-negative.")
    load_map = dict(zip(loads["rrh_id"].astype(str), load_values.astype(float)))
    expected_ids = set(rrh_ids)
    actual_ids = set(load_map)
    if expected_ids != actual_ids:
        raise ValueError(
            "Connection and load RRH IDs do not match; "
            f"missing loads={len(expected_ids - actual_ids)}, "
            f"unexpected loads={len(actual_ids - expected_ids)}."
        )
    return load_map


def _initialize_state(records, load_map):
    pool_ids = {
        pool
        for record in records
        for pool in record["feasible_bbu_pools"]
    }
    assignments = {}
    pool_loads = {pool: 0.0 for pool in pool_ids}
    pool_counts = {pool: 0 for pool in pool_ids}
    remaining = []
    for record in records:
        if record["option_count"] == 1:
            pool = record["feasible_bbu_pools"][0]
            distance = record["feasible_distances_km"][0]
            assignments[record["rrh_id"]] = (pool, distance)
            pool_loads[pool] += load_map[record["rrh_id"]]
            pool_counts[pool] += 1
        else:
            remaining.append(record)
    return assignments, pool_loads, pool_counts, remaining


def initialize_unique_assignments(connections, loads):
    """Perform Step b and expose its fixed assignments and base totals."""
    records = _normalize_connections(connections)
    load_map = _normalize_load_map(loads, [record["rrh_id"] for record in records])
    assignments, pool_loads, pool_counts, remaining = _initialize_state(
        records, load_map
    )
    unique_rows = [
        {
            "rrh_id": rrh_id,
            "bbu_pool_id": pool,
            "distance_km": distance,
        }
        for rrh_id, (pool, distance) in assignments.items()
    ]
    remaining_rows = [
        {
            "rrh_id": record["rrh_id"],
            "feasible_bbu_pools": record["feasible_bbu_pools"],
            "feasible_distances_km": record["feasible_distances_km"],
            "option_count": record["option_count"],
            "minimum_distance_km": record["minimum_distance_km"],
        }
        for record in remaining
    ]
    return (
        pd.DataFrame(unique_rows, columns=ASSIGNMENT_COLUMNS),
        pool_loads,
        pool_counts,
        pd.DataFrame(remaining_rows),
    )


def order_remaining_rrhs(remaining_connections, method="traffic"):
    """Order ambiguous RRHs by fewest choices, with method-specific tie-breaks."""
    if method not in {"traffic", "count"}:
        raise ValueError("method must be 'traffic' or 'count'.")
    records = _normalize_connections(remaining_connections)
    records = [record for record in records if record["option_count"] > 1]
    if method == "traffic":
        records.sort(key=lambda row: (
            row["option_count"],
            row["minimum_distance_km"],
            row["rrh_id"],
        ))
    else:
        records.sort(key=lambda row: (row["option_count"], row["rrh_id"]))
    return [record["rrh_id"] for record in records]


def _run_assignment(connections, loads, method):
    records = _normalize_connections(connections)
    load_map = _normalize_load_map(loads, [record["rrh_id"] for record in records])
    assignments, pool_loads, pool_counts, remaining = _initialize_state(
        records, load_map
    )
    remaining_ids = order_remaining_rrhs(
        pd.DataFrame(remaining), method=method
    )
    records_by_id = {record["rrh_id"]: record for record in remaining}
    for rrh_id in remaining_ids:
        record = records_by_id[rrh_id]
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
            pool_loads[pool] += load_map[rrh_id]
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
        assignments[rrh_id] = (pool, distance)

    rows = [
        {
            "rrh_id": record["rrh_id"],
            "bbu_pool_id": assignments[record["rrh_id"]][0],
            "distance_km": assignments[record["rrh_id"]][1],
        }
        for record in records
    ]
    result = pd.DataFrame(rows, columns=ASSIGNMENT_COLUMNS)
    if result["rrh_id"].duplicated().any() or len(result) != len(records):
        raise AssertionError("The assignment did not preserve all RRHs exactly once.")
    return result


def assign_by_traffic(connections, loads):
    """Assign flexible RRHs to the least-loaded feasible pool in real time."""
    return _run_assignment(connections, loads, method="traffic")


def assign_by_rrh_count(connections, loads):
    """Assign flexible RRHs to the pool with the fewest current RRHs."""
    return _run_assignment(connections, loads, method="count")


def run_balancing(
    scenario_file=DEFAULT_SCENARIO_FILE,
    assignment_file=DEFAULT_ASSIGNMENT_FILE,
    profile_file=DEFAULT_PROFILE_FILE,
    load_file=DEFAULT_LOAD_FILE,
    output_dir=DEFAULT_OUTPUT_DIR,
):
    scenario = pd.read_csv(scenario_file, dtype={"rrh_id": str})
    phase1_assignment = pd.read_csv(
        assignment_file,
        dtype={
            "site_id": str,
            "feasible_bbu_pools": str,
            "feasible_distances_km": str,
        },
    )
    connections = build_connection_matrix(scenario, phase1_assignment)
    profile_summary = pd.read_csv(profile_file)
    peak_time = detect_peak_time(profile_summary)
    load_data = pd.read_csv(
        load_file,
        usecols=["rrh_id", "day_of_week", "time", "traffic_load_mbph"],
        dtype={"rrh_id": str, "day_of_week": str, "time": str},
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    peak_loads = weekday_mean_loads(load_data, peak_time)
    peak_assignments = assign_by_traffic(connections, peak_loads)
    peak_assignments.to_csv(
        output_dir / "assignment_traffic_peak.csv", index=False
    )

    count_assignments = assign_by_rrh_count(connections, peak_loads)
    count_assignments.to_csv(
        output_dir / "assignment_rrh_count.csv", index=False
    )

    for time in SENSITIVITY_HOURS:
        loads_at_time = weekday_mean_loads(load_data, time)
        assignments = assign_by_traffic(connections, loads_at_time)
        assignments.to_csv(
            output_dir / f"assignment_traffic_{time.replace(':', '')}.csv",
            index=False,
        )
    return peak_time, output_dir


def main():
    parser = argparse.ArgumentParser(
        description="Generate greedy BBU Pool load-balancing assignments."
    )
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO_FILE)
    parser.add_argument(
        "--phase1-assignment", type=Path, default=DEFAULT_ASSIGNMENT_FILE
    )
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILE_FILE)
    parser.add_argument("--loads", type=Path, default=DEFAULT_LOAD_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    peak_time, output_dir = run_balancing(
        scenario_file=args.scenario,
        assignment_file=args.phase1_assignment,
        profile_file=args.profiles,
        load_file=args.loads,
        output_dir=args.output_dir,
    )
    print(f"Peak weekday traffic time: {peak_time}")
    print(f"Saved balancing assignments to: {output_dir}")


if __name__ == "__main__":
    main()
