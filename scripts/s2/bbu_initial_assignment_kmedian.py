#!/usr/bin/env python3
"""
BBU Pool Optimization & RRH Assignment Script (K-Median / K-Medoids Mode)

Minimizes total/average fronthaul distance (sum of distances: sum(d)) 
instead of minimax distance (max(d)), while requiring every RRH to be within
4 km of at least one BBU Pool.

Reads scenario_sites.csv, auto-detects synthetic RRHs (S), runs Partitioning 
Around Medoids (PAM) K-Medoids, and outputs CSVs and Folium maps tagged with '_kmedian'.

Outputs:
  - output/BBU-pool-allocation_k{K}/rrh_bbu_k{K}_s{S}_kmedian_assignments.csv
  - output/BBU-pool-allocation_k{K}/bbu_pool_k{K}_s{S}_kmedian_locations.csv
  - output/BBU-pool-allocation_k{K}/rrh_bbu_k{K}_s{S}_kmedian_assignments_map.html
"""

import os
import argparse
import pandas as pd
import numpy as np
import json
import re
import html
import folium
import matplotlib.pyplot as plt
from matplotlib.colors import to_hex
from pyproj import Transformer
from pathlib import Path

MAX_DISTANCE_KM = 4.0
MAX_DISTANCE_METRES = MAX_DISTANCE_KM * 1000


# ============================================================
# 1. LOAD & ROBUSTLY PARSE RRHs FROM scenario_sites.csv
# ============================================================

def load_rrhs(csv_path):
    """
    Loads scenario_sites.csv (or 350_CELLS.csv / synthetic datasets),
    parses JSON array Site IDs, multi-valued operators (';'), detects 5G/4G 
    technology priorities, auto-detects synthetic RRHs, and deduplicates 
    by physical coordinates keeping 5G > 4G.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Input file not found: {csv_path}")

    df = pd.read_csv(csv_path, dtype=str)

    # Normalize column names
    df.columns = [
        re.sub(r"\s+", "_", html.unescape(str(c)).replace("*", "").strip().lower())
        for c in df.columns
    ]

    candidates = []

    for idx, row in df.iterrows():
        lat, lon = None, None

        # Extract latitude and longitude
        lat_val = row.get("latitude") or row.get("lat")
        lon_val = row.get("longitude") or row.get("lon") or row.get("lng")

        if (pd.isna(lat_val) or pd.isna(lon_val)) and "coordinates" in row:
            coords_str = str(row["coordinates"]).strip()
            if "," in coords_str:
                parts = coords_str.split(",")
                try:
                    lat_val, lon_val = float(parts[0].strip()), float(parts[1].strip())
                except ValueError:
                    continue

        try:
            lat = float(lat_val)
            lon = float(lon_val)
        except (ValueError, TypeError):
            continue

        # Geographic bounds check for study area
        if not (35.0 <= lat <= 44.0 and -10.0 <= lon <= 5.0):
            continue

        # Parse Site ID / rrh_id
        raw_id = str(
            row.get("rrh_id") or row.get("site_id") or row.get("gis_codigo") or f"SITE_{idx+1:05d}"
        ).strip()

        site_id = "UNKNOWN"
        if raw_id.startswith("[") and raw_id.endswith("]"):
            try:
                parsed_json = json.loads(raw_id)
                site_id = str(parsed_json[0]).strip() if parsed_json else "UNKNOWN"
            except Exception:
                site_id = raw_id.strip("[]\"' ")
        else:
            site_id = raw_id

        # Auto-detect synthetic status
        is_syn_str = str(row.get("is_synthetic", "")).strip().lower()
        operator_str = str(row.get("operator", "")).strip()
        source_str = str(row.get("source_type", "")).strip().upper()

        is_synthetic = (
            is_syn_str in ["true", "1", "t"] or
            "SYNTHETIC" in operator_str.upper() or
            "SYNTHETIC" in source_str or
            site_id.startswith("SYN_") or
            site_id.startswith("SYNTH")
        )

        # Technology priority (5G=2 > 4G=1)
        tech_str = str(row.get("technology", "")).strip().upper()

        tech_cols = [c for c in df.columns if "tech" in c]
        for tc in tech_cols:
            if pd.notna(row.get(tc)):
                tech_str += " " + str(row.get(tc)).upper()

        if is_synthetic:
            syn_so_far = len([c for c in candidates if c["is_synthetic"]])
            tech_label = "5G" if syn_so_far % 2 == 0 else "4G"
            tech_priority = 2 if tech_label == "5G" else 1
        else:
            if "5G" in tech_str:
                tech_label = "5G"
                tech_priority = 2
            elif "4G" in tech_str:
                tech_label = "4G"
                tech_priority = 1
            else:
                continue

        # Operator parsing
        if not operator_str or operator_str.lower() == "nan":
            op_cols = [c for c in df.columns if "operator" in c]
            ops = [str(row[c]).strip() for c in op_cols if pd.notna(row[c]) and str(row[c]).strip()]
            operator_label = "; ".join(ops) if ops else ("SYNTHETIC" if is_synthetic else "SHARED")
        else:
            operator_label = operator_str

        candidates.append({
            "site_id": site_id,
            "operator": operator_label,
            "technology": tech_label,
            "tech_priority": tech_priority,
            "latitude": lat,
            "longitude": lon,
            "is_synthetic": is_synthetic
        })

    if not candidates:
        raise ValueError("No valid 4G/5G stations found in dataset.")

    df_cand = pd.DataFrame(candidates)

    df_cand = df_cand.sort_values(
        by=["latitude", "longitude", "tech_priority"],
        ascending=[True, True, False]
    )

    result = df_cand.drop_duplicates(
        subset=["latitude", "longitude"], 
        keep="first"
    ).copy().reset_index(drop=True)

    rrh_ids = []
    syn_counter = 1
    real_counter = 1
    for _, row in result.iterrows():
        if row["is_synthetic"]:
            rrh_ids.append(f"RRH_SYN_{syn_counter:03d}")
            syn_counter += 1
        else:
            rrh_ids.append(f"RRH_{real_counter:03d}")
            real_counter += 1

    result["rrh_id"] = rrh_ids

    return result[["rrh_id", "site_id", "operator", "technology", "latitude", "longitude", "is_synthetic"]]


# ============================================================
# 2. CONVERT GEOGRAPHIC COORDINATES TO METERS (UTM 31N)
# ============================================================

def project_coordinates(rrhs):
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:25831", always_xy=True)
    x, y = transformer.transform(rrhs["longitude"].values, rrhs["latitude"].values)
    return np.column_stack((x, y))


# ============================================================
# 3. K-MEDIAN / K-MEDOIDS ALGORITHM (PAM - Partitioning Around Medoids)
# ============================================================

def _build_feasible_medoids(distance_matrix, K, max_distance_m):
    point_count = len(distance_matrix)
    selected = []
    nearest_distances = np.full(point_count, np.inf)
    remaining = np.arange(point_count)

    while len(selected) < K:
        remaining = np.array(
            [index for index in remaining if index not in selected],
            dtype=int,
        )
        uncovered = nearest_distances > max_distance_m
        if uncovered.any():
            coverage_gain = np.count_nonzero(
                distance_matrix[uncovered][:, remaining] <= max_distance_m,
                axis=0,
            )
            best_gain = int(coverage_gain.max())
            if best_gain == 0:
                break
            candidates = remaining[coverage_gain == best_gain]
        else:
            candidates = remaining

        total_distances = np.minimum(
            nearest_distances[:, np.newaxis],
            distance_matrix[:, candidates],
        ).sum(axis=0)
        candidate = int(candidates[np.argmin(total_distances)])
        selected.append(candidate)
        nearest_distances = np.minimum(
            nearest_distances,
            distance_matrix[:, candidate],
        )

    if len(selected) != K or np.any(nearest_distances > max_distance_m):
        raise ValueError(
            f"Could not find a feasible placement with {K} BBU Pools "
            f"covering every RRH within {max_distance_m / 1000:g} km. "
            "Increase K or review the RRH locations."
        )
    return np.asarray(selected, dtype=int)


def k_medoids_kmedian(
    X,
    K,
    max_iter=100,
    max_distance_m=MAX_DISTANCE_METRES,
):
    """
    Minimize total distance with every RRH within max_distance_m of a medoid.

    Starts with unconstrained PAM, repairs coverage if needed, then accepts
    only distance-reducing swaps that preserve the coverage constraint.
    """
    N = len(X)
    if K < 1 or K > N:
        raise ValueError(f"K must be between 1 and {N}")

    # Distance matrix N x N in meters
    D = np.linalg.norm(X[:, np.newaxis, :] - X[np.newaxis, :, :], axis=2)

    # Phase 1: BUILD (Greedy Selection)
    first_medoid = np.argmin(D.sum(axis=1))
    selected = [first_medoid]

    current_best_dist = D[first_medoid].copy()

    for _ in range(1, K):
        best_gain = -1.0
        best_candidate = -1
        for c in range(N):
            if c in selected:
                continue
            new_dist = np.minimum(current_best_dist, D[c])
            gain = np.sum(current_best_dist - new_dist)
            if gain > best_gain:
                best_gain = gain
                best_candidate = c
        selected.append(best_candidate)
        current_best_dist = np.minimum(current_best_dist, D[best_candidate])

    selected = np.array(selected, dtype=int)

    # Phase 2: SWAP (Refinement)
    current_cost = np.sum(np.min(D[:, selected], axis=1))

    for _ in range(max_iter):
        improved = False
        for i in range(K):
            for candidate in range(N):
                if candidate in selected:
                    continue
                temp_selected = selected.copy()
                temp_selected[i] = candidate
                trial_cost = np.sum(np.min(D[:, temp_selected], axis=1))
                if trial_cost < current_cost - 1e-6:
                    current_cost = trial_cost
                    selected = temp_selected
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break

    if np.any(np.min(D[:, selected], axis=1) > max_distance_m):
        selected = _build_feasible_medoids(D, K, max_distance_m)

    current_distances = np.min(D[:, selected], axis=1)
    current_cost = current_distances.sum()
    for _ in range(max_iter):
        improved = False
        for i in range(K):
            for candidate in range(N):
                if candidate in selected:
                    continue
                trial_selected = selected.copy()
                trial_selected[i] = candidate
                trial_distances = np.min(D[:, trial_selected], axis=1)
                if (
                    np.all(trial_distances <= max_distance_m)
                    and trial_distances.sum() < current_cost - 1e-6
                ):
                    selected = trial_selected
                    current_distances = trial_distances
                    current_cost = current_distances.sum()
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break

    return selected.tolist()


# ============================================================
# 4. ASSIGN EACH RRH TO NEAREST BBU POOL
# ============================================================

def assign_rrhs_to_pools(
    rrhs,
    X,
    selected_indices,
    max_distance_m=MAX_DISTANCE_METRES,
):
    centers = X[selected_indices]
    distance_matrix = np.linalg.norm(
        X[:, np.newaxis, :] - centers[np.newaxis, :, :],
        axis=2
    )

    pool_ids = [f"BBU_{i+1:02d}" for i in range(len(selected_indices))]
    feasible_pool_indices = []
    feasible_pool_ids = []
    feasible_distances_km = []
    for distances in distance_matrix:
        ranked_feasible = sorted(
            np.flatnonzero(distances <= max_distance_m),
            key=lambda index: (distances[index], pool_ids[index]),
        )
        if not ranked_feasible:
            raise ValueError(
                "At least one RRH has no BBU Pool within "
                f"{max_distance_m / 1000:g} km."
            )
        feasible_pool_indices.append(ranked_feasible)
        feasible_pool_ids.append(
            json.dumps([pool_ids[index] for index in ranked_feasible])
        )
        feasible_distances_km.append(json.dumps([
            float(distances[index] / 1000.0)
            for index in ranked_feasible
        ]))

    assigned_pool = np.array(
        [indices[0] for indices in feasible_pool_indices],
        dtype=int,
    )
    assigned_distance = distance_matrix[np.arange(len(X)), assigned_pool]

    result = rrhs.copy()
    result["bbu_pool"] = [pool_ids[i] for i in assigned_pool]
    result["distance_to_bbu_km"] = assigned_distance / 1000.0
    result["feasible_bbu_pools"] = feasible_pool_ids
    result["feasible_distances_km"] = feasible_distances_km
    result["num_feasible_bbu_pools"] = [
        len(indices) for indices in feasible_pool_indices
    ]

    pools = rrhs.iloc[selected_indices].copy().reset_index(drop=True)
    pools["bbu_pool"] = pool_ids
    pools["num_assigned_rrhs"] = [
        np.sum(assigned_pool == i) for i in range(len(selected_indices))
    ]

    return result, pools


# ============================================================
# 5. MAP GENERATION WITH SYNTHETIC LAYER VISUAL TREATMENT
# ============================================================

def generate_map(rrhs, bbus, output_html_path):
    pool_ids = sorted(rrhs["bbu_pool"].dropna().unique(), key=lambda x: str(x))
    cmap = plt.get_cmap("tab10", max(len(pool_ids), 1))
    pool_colors = {pool_id: to_hex(cmap(i)) for i, pool_id in enumerate(pool_ids)}

    center_lat = rrhs["latitude"].mean()
    center_lon = rrhs["longitude"].mean()

    m = folium.Map(location=[center_lat, center_lon], zoom_start=11, tiles=None)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Esri Street Map", overlay=False, control=True
    ).add_to(m)

    real_rrh_layer = folium.FeatureGroup(name="Real RRHs (Shared Infrastructure)", show=True)
    syn_rrh_layer = folium.FeatureGroup(name="Synthetic RRHs (Stress Test)", show=True)

    for _, row in rrhs.iterrows():
        pool_id = row["bbu_pool"]
        color = pool_colors.get(pool_id, "gray")
        is_syn = row.get("is_synthetic", False)

        popup_text = f"""
        <b>RRH ID:</b> {row.get('rrh_id', 'N/A')}<br>
        <b>Type:</b> {'SYNTHETIC' if is_syn else 'REAL'}<br>
        <b>Site ID:</b> {row.get('site_id', 'N/A')}<br>
        <b>Tech:</b> {row.get('technology', 'N/A')}<br>
        <b>Operator(s):</b> {row.get('operator', 'N/A')}<br>
        <b>Assigned BBU:</b> {pool_id}<br>
        <b>Distance:</b> {row.get('distance_to_bbu_km', 'N/A'):.3f} km
        """

        if is_syn:
            folium.CircleMarker(
                location=[row["latitude"], row["longitude"]],
                radius=6, color="red", fill=True, fill_color=color, fill_opacity=0.6,
                weight=2, dash_array="4, 4", popup=folium.Popup(popup_text, max_width=320),
                tooltip=f"SYNTHETIC RRH {row.get('rrh_id')} -> {pool_id}"
            ).add_to(syn_rrh_layer)
        else:
            folium.CircleMarker(
                location=[row["latitude"], row["longitude"]],
                radius=5, color=color, fill=True, fill_color=color, fill_opacity=0.85,
                weight=1, popup=folium.Popup(popup_text, max_width=320),
                tooltip=f"REAL RRH {row.get('rrh_id')} [{row.get('technology')}] -> {pool_id}"
            ).add_to(real_rrh_layer)

    real_rrh_layer.add_to(m)
    syn_rrh_layer.add_to(m)

    bbu_layer = folium.FeatureGroup(name="BBU Pools (K-Median / PAM)", show=True)
    for i, row in bbus.iterrows():
        pool_id = row.get("bbu_pool", f"BBU_{i+1:02d}")
        is_syn_pool = row.get("is_synthetic", False)

        popup_text = f"""
        <b>BBU Pool:</b> {pool_id} (K-Median)<br>
        <b>Location Site:</b> {row.get('site_id', 'N/A')} ({'SYNTHETIC' if is_syn_pool else 'REAL'})<br>
        <b>Operator(s):</b> {row.get('operator', 'N/A')}<br>
        <b>Assigned RRHs:</b> {row.get('num_assigned_rrhs', 'N/A')}
        """

        folium.Marker(
            location=[row["latitude"], row["longitude"]],
            popup=folium.Popup(popup_text, max_width=320),
            tooltip=f"BBU Pool {pool_id} [K-Median] ({'SYNTH' if is_syn_pool else 'REAL'})",
            icon=folium.Icon(color="purple" if is_syn_pool else "darkblue", icon="server", prefix="fa")
        ).add_to(bbu_layer)

    bbu_layer.add_to(m)

    connection_layer = folium.FeatureGroup(name="RRH - BBU connections", show=True)
    bbu_coordinates = {
        row.get("bbu_pool", f"BBU_{i+1:02d}"): [row["latitude"], row["longitude"]]
        for i, row in bbus.iterrows()
    }

    for _, row in rrhs.iterrows():
        pool_id = row["bbu_pool"]
        if pool_id not in bbu_coordinates:
            continue
        is_syn = row.get("is_synthetic", False)

        folium.PolyLine(
            locations=[[row["latitude"], row["longitude"]], bbu_coordinates[pool_id]],
            color="red" if is_syn else pool_colors.get(pool_id, "gray"),
            weight=1.5 if is_syn else 1.2, opacity=0.4 if is_syn else 0.5,
            dash_array="3, 3" if is_syn else None
        ).add_to(connection_layer)

    connection_layer.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds([[rrhs["latitude"].min(), rrhs["longitude"].min()], [rrhs["latitude"].max(), rrhs["longitude"].max()]])
    m.save(output_html_path)


def resolve_output_directories(
    root,
    k_pools,
    data_output_dir=None,
    html_output_dir=None,
):
    default_output_dir = Path(root) / "output" / f"BBU-pool-allocation_k{k_pools}"
    data_dir = (
        Path(data_output_dir)
        if data_output_dir is not None
        else default_output_dir
    )
    html_dir = (
        Path(html_output_dir)
        if html_output_dir is not None
        else default_output_dir
    )
    return data_dir, html_dir


# ============================================================
# 6. MAIN EXECUTION
# ============================================================

def main():
    ROOT = Path(__file__).resolve().parent.parent.parent
    DEFAULT_INPUT = ROOT / "data" / "processed" / "scenario_sites.csv"

    parser = argparse.ArgumentParser(
        description="BBU Pool Optimizer - K-Median / K-Medoids (Minimizing Sum of Distances)"
    )
    parser.add_argument(
        "-k", "--k_pools", type=int, required=True,
        help="Número de BBU pools deseados (K)"
    )
    parser.add_argument(
        "--input_path", type=str, default=str(DEFAULT_INPUT),
        help=f"Ruta al CSV con el escenario (default: {DEFAULT_INPUT})"
    )
    parser.add_argument(
        "--data_output_dir", type=Path,
        help="Directorio CSV (por defecto: output/BBU-pool-allocation_k<K>)"
    )
    parser.add_argument(
        "--html_output_dir", type=Path,
        help="Directorio HTML (por defecto: output/BBU-pool-allocation_k<K>)"
    )

    args = parser.parse_args()

    K = args.k_pools
    data_output_dir, html_output_dir = resolve_output_directories(
        ROOT,
        K,
        data_output_dir=args.data_output_dir,
        html_output_dir=args.html_output_dir,
    )

    # 1. Load cells and auto-detect synthetic cells
    rrhs = load_rrhs(args.input_path)

    n_total = len(rrhs)
    n_synthetic = int(rrhs["is_synthetic"].sum())
    n_real = n_total - n_synthetic

    print("\n============================================================")
    print(" MODE: K-MEDIAN / K-MEDOIDS OPTIMIZATION (SUM DISTANCE MINIMIZATION)")
    print("============================================================")
    print(f"Input file loaded:         {args.input_path}")
    print(f"Real physical RRHs:        {n_real}")
    print(f"Synthetic RRHs detected:   {n_synthetic}")
    print(f"TOTAL RRHs in scenario:    {n_total}")
    print(
        f"Maximum RRH-to-BBU distance: {MAX_DISTANCE_KM:.1f} km "
        "(40 us propagation RTT)"
    )

    # 2. Project coordinates to UTM 31N meters
    X = project_coordinates(rrhs)

    # 3. K-Median Selection (PAM K-Medoids)
    try:
        selected_indices = k_medoids_kmedian(X, K)
        assignments, pools = assign_rrhs_to_pools(
            rrhs,
            X,
            selected_indices,
        )
    except ValueError as error:
        parser.error(str(error))

    os.makedirs(data_output_dir, exist_ok=True)
    os.makedirs(html_output_dir, exist_ok=True)

    # 5. Dynamic tag generation based on K and S with '_kmedian' suffix
    tag = f"k{K}_s{n_synthetic}_kmedian" if n_synthetic > 0 else f"k{K}_kmedian"

    assignments_file = data_output_dir / f"rrh_bbu_{tag}_assignments.csv"
    pools_file = data_output_dir / f"bbu_pool_{tag}_locations.csv"
    map_filename = html_output_dir / f"rrh_bbu_{tag}_assignments_map.html"

    assignments.to_csv(assignments_file, index=False)
    pools.to_csv(pools_file, index=False)
    generate_map(assignments, pools, str(map_filename))

    total_dist_sum_km = assignments['distance_to_bbu_km'].sum()
    print("\nPERFORMANCE METRICS (K-MEDIAN):")
    print(f"Total distance sum (sum d): {total_dist_sum_km:.3f} km")
    print(f"Average distance (avg d):   {assignments['distance_to_bbu_km'].mean():.3f} km")
    print(f"Maximum distance (max d):   {assignments['distance_to_bbu_km'].max():.3f} km")

    real_dist = assignments[~assignments['is_synthetic']]['distance_to_bbu_km']
    syn_dist = assignments[assignments['is_synthetic']]['distance_to_bbu_km']

    if not real_dist.empty:
        print(f" - Real RRHs    (Avg / Max): {real_dist.mean():.3f} km / {real_dist.max():.3f} km")
    if not syn_dist.empty:
        print(f" - Synthetic    (Avg / Max): {syn_dist.mean():.3f} km / {syn_dist.max():.3f} km")

    print("\nRESULTS EXPORTED SUCCESSFULLY:")
    print(f" - {assignments_file}")
    print(f" - {pools_file}")
    print(f" - {map_filename}")

    print("\nRRH assignment summary per BBU Pool:")
    summary = (
        assignments.groupby("bbu_pool")
        .agg(
            total_RRHs=("bbu_pool", "count"),
            real_RRHs=("is_synthetic", lambda s: (~s).sum()),
            synthetic_RRHs=("is_synthetic", "sum"),
            sum_dist_km=("distance_to_bbu_km", "sum"),
            mean_dist_km=("distance_to_bbu_km", "mean"),
            max_dist_km=("distance_to_bbu_km", "max")
        )
        .round(3)
    )
    print(summary.to_string())


if __name__ == "__main__":
    main()