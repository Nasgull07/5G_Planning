#!/usr/bin/env python3
"""
BBU Pool Optimization & RRH Assignment Script (Joint Zoned & Load-Balanced K-Median)

1. Reads two territory polygons from a single KML file (inputs/separated_area.kml).
2. Allocates BBU pool quotas proportionally to RRH counts (K = K1 + K2).
3. Jointly optimizes BBU pool locations AND load-balanced RRH assignments across
   the unified network (allowing cross-border links) under per-territory
   maximum distance / RTT latency constraints.
"""

import os
import re
import json
import html
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
import folium
import matplotlib.pyplot as plt
from matplotlib.colors import to_hex
from matplotlib.path import Path as MplPath
from pyproj import Transformer

DEFAULT_MAX_DISTANCE_KM = 4.0
RTT_US_PER_KM = 10.0  # 4.0 km <-> 40 us propagation RTT


# ============================================================
# 0. PROJECT ROOT & SINGLE-KML MULTI-POLYGON PARSING
# ============================================================

def find_project_root(start_path: Path) -> Path:
    """Walks up from script location to locate the repository root."""
    for parent in [start_path.resolve().parent, *start_path.resolve().parents]:
        if (parent / "data").exists() or (parent / "inputs").exists():
            return parent
    return start_path.resolve().parent.parent


def _parse_kml_coord_string(coord_text: str) -> np.ndarray:
    """Parses KML 'lon,lat[,alt]' tuples into an (M, 2) array of [lon, lat]."""
    coords = []
    for token in coord_text.strip().split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                coords.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return np.array(coords, dtype=float) if coords else np.empty((0, 2), dtype=float)


def _polygon_area_m2(ring_xy: np.ndarray) -> float:
    """Computes planar area in m^2 using the shoelace formula."""
    if len(ring_xy) < 3:
        return 0.0
    x = ring_xy[:, 0]
    y = ring_xy[:, 1]
    return 0.5 * float(np.abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _extract_polygon_dict(poly_elem: ET.Element, transformer: Transformer) -> dict | None:
    outer_coords_elem = poly_elem.find(".//outerBoundaryIs//coordinates")
    if outer_coords_elem is None or not outer_coords_elem.text:
        return None
    outer_lonlat = _parse_kml_coord_string(outer_coords_elem.text)
    if len(outer_lonlat) < 3:
        return None

    ox, oy = transformer.transform(outer_lonlat[:, 0], outer_lonlat[:, 1])
    outer_xy = np.column_stack((ox, oy))
    net_area_m2 = _polygon_area_m2(outer_xy)

    holes_xy = []
    holes_lonlat = []
    for inner_elem in poly_elem.findall(".//innerBoundaryIs//coordinates"):
        if inner_elem.text:
            h_ll = _parse_kml_coord_string(inner_elem.text)
            if len(h_ll) >= 3:
                hx, hy = transformer.transform(h_ll[:, 0], h_ll[:, 1])
                h_xy = np.column_stack((hx, hy))
                holes_xy.append(h_xy)
                holes_lonlat.append(h_ll)
                net_area_m2 = max(0.0, net_area_m2 - _polygon_area_m2(h_xy))

    return {
        "outer_lonlat": outer_lonlat,
        "outer_xy": outer_xy,
        "outer_path": MplPath(outer_xy),
        "holes_lonlat": holes_lonlat,
        "holes_xy": holes_xy,
        "hole_paths": [MplPath(h) for h in holes_xy],
        "area_m2": net_area_m2,
    }


def load_territories_from_kml(kml_path: Path, transformer: Transformer) -> list[dict]:
    """
    Extracts the two territory polygons from a single KML file (separated_area.kml).
    Sorts extracted territories from smallest to largest area so:
      - Index 0 (Area 1): Inner / smaller area (e.g. Barcelona urban core)
      - Index 1 (Area 2): Outer / larger area (e.g. surroundings)
    """
    if not kml_path.exists():
        raise FileNotFoundError(f"KML file not found: {kml_path}")

    tree = ET.parse(kml_path)
    root = tree.getroot()

    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]

    territories = []

    placemarks = [pm for pm in root.findall(".//Placemark") if pm.find(".//Polygon") is not None]
    if len(placemarks) >= 2:
        for idx, pm in enumerate(placemarks):
            name_elem = pm.find("name")
            name = (
                name_elem.text.strip()
                if (name_elem is not None and name_elem.text and name_elem.text.strip())
                else f"Area_{idx + 1}"
            )
            polys = []
            for poly_elem in pm.findall(".//Polygon"):
                p_dict = _extract_polygon_dict(poly_elem, transformer)
                if p_dict is not None:
                    polys.append(p_dict)
            if polys:
                territories.append({
                    "name": name,
                    "polygons": polys,
                    "area_km2": sum(p["area_m2"] for p in polys) / 1e6,
                })
    else:
        all_poly_elems = root.findall(".//Polygon")
        for idx, poly_elem in enumerate(all_poly_elems):
            p_dict = _extract_polygon_dict(poly_elem, transformer)
            if p_dict is not None:
                territories.append({
                    "name": f"Area_{idx + 1}",
                    "polygons": [p_dict],
                    "area_km2": p_dict["area_m2"] / 1e6,
                })

    if len(territories) != 2:
        raise ValueError(
            f"Expected exactly 2 territory polygons inside '{kml_path}', "
            f"but found {len(territories)}."
        )

    territories.sort(key=lambda t: t["area_km2"])

    if territories[0]["name"] == territories[1]["name"]:
        base_name = territories[0]["name"]
        territories[0]["name"] = f"{base_name}_Inner"
        territories[1]["name"] = f"{base_name}_Outer"

    return territories


def _point_to_segment_distance_sq(points_xy: np.ndarray, ring_xy: np.ndarray) -> np.ndarray:
    """Computes squared Euclidean distance from each point (N, 2) to a closed ring."""
    p1 = ring_xy[:-1]
    p2 = ring_xy[1:]
    seg = p2 - p1
    seg_len_sq = np.sum(seg ** 2, axis=1)
    seg_len_sq = np.where(seg_len_sq == 0, 1e-12, seg_len_sq)

    min_dist_sq = np.full(len(points_xy), np.inf)
    for i in range(len(p1)):
        v = points_xy - p1[i]
        t = np.clip((v @ seg[i]) / seg_len_sq[i], 0.0, 1.0)
        proj = p1[i] + t[:, None] * seg[i]
        d_sq = np.sum((points_xy - proj) ** 2, axis=1)
        min_dist_sq = np.minimum(min_dist_sq, d_sq)
    return min_dist_sq


def classify_rrhs_by_territories(
    X: np.ndarray,
    territories: list[dict],
) -> tuple[np.ndarray, int]:
    """
    Assigns each projected RRH point X (N, 2) to territory index 0 or 1.
    Points falling slightly outside both KML polygons are snapped to the nearest
    territory boundary.
    """
    n_points = len(X)
    in_territory = np.zeros((n_points, len(territories)), dtype=bool)

    for t_idx, terr in enumerate(territories):
        for poly in terr["polygons"]:
            inside = poly["outer_path"].contains_points(X, radius=1e-3)
            for hole_path in poly["hole_paths"]:
                inside &= ~hole_path.contains_points(X, radius=-1e-3)
            in_territory[:, t_idx] |= inside

    zone_ids = np.full(n_points, -1, dtype=int)
    for t_idx in range(len(territories)):
        unassigned_and_in = (zone_ids == -1) & in_territory[:, t_idx]
        zone_ids[unassigned_and_in] = t_idx

    outside_mask = zone_ids == -1
    n_outside = int(np.sum(outside_mask))

    if n_outside > 0:
        outside_pts = X[outside_mask]
        dist_to_terr = np.full((n_outside, len(territories)), np.inf)
        for t_idx, terr in enumerate(territories):
            for poly in terr["polygons"]:
                ring = poly["outer_xy"]
                if not np.allclose(ring[0], ring[-1]):
                    ring = np.vstack([ring, ring[0]])
                d_sq = _point_to_segment_distance_sq(outside_pts, ring)
                dist_to_terr[:, t_idx] = np.minimum(dist_to_terr[:, t_idx], d_sq)
        zone_ids[outside_mask] = np.argmin(dist_to_terr, axis=1)

    return zone_ids, n_outside


def compute_proportional_quotas(zone_ids: np.ndarray, total_k: int) -> list[int]:
    """
    Splits total_k proportionally to the number of RRHs in each territory,
    guaranteeing sum(quotas) == total_k and at least 1 BBU pool per non-empty
    territory whenever total_k >= number of non-empty territories.
    """
    counts = np.bincount(zone_ids, minlength=2)
    total_rrhs = int(counts.sum())
    if total_k < 1 or total_k > total_rrhs:
        raise ValueError(f"K ({total_k}) must be between 1 and total RRHs ({total_rrhs}).")

    exact_quotas = total_k * (counts / total_rrhs)
    q0 = int(round(exact_quotas[0]))
    quotas = [q0, total_k - q0]

    non_empty = int(np.sum(counts > 0))
    if total_k >= non_empty:
        for z in range(2):
            if counts[z] > 0 and quotas[z] == 0:
                quotas[z] += 1
                quotas[1 - z] -= 1

    for z in range(2):
        if quotas[z] > counts[z]:
            excess = quotas[z] - int(counts[z])
            quotas[z] = int(counts[z])
            quotas[1 - z] += excess

    return quotas


# ============================================================
# 1. LOAD & ROBUSTLY PARSE RRHs FROM scenario_sites.csv
# ============================================================

def load_rrhs(csv_path: str) -> pd.DataFrame:
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Input file not found: {csv_path}")

    df = pd.read_csv(csv_path, dtype=str)
    df.columns = [
        re.sub(r"\s+", "_", html.unescape(str(c)).replace("*", "").strip().lower())
        for c in df.columns
    ]

    candidates = []
    for idx, row in df.iterrows():
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

        if not (35.0 <= lat <= 44.0 and -10.0 <= lon <= 5.0):
            continue

        raw_id = str(
            row.get("rrh_id") or row.get("site_id") or row.get("gis_codigo") or f"SITE_{idx+1:05d}"
        ).strip()

        if raw_id.startswith("[") and raw_id.endswith("]"):
            try:
                parsed_json = json.loads(raw_id)
                site_id = str(parsed_json[0]).strip() if parsed_json else "UNKNOWN"
            except Exception:
                site_id = raw_id.strip("[]\"' ")
        else:
            site_id = raw_id

        is_syn_str = str(row.get("is_synthetic", "")).strip().lower()
        operator_str = str(row.get("operator", "")).strip()
        source_str = str(row.get("source_type", "")).strip().upper()

        is_synthetic = (
            is_syn_str in ["true", "1", "t"]
            or "SYNTHETIC" in operator_str.upper()
            or "SYNTHETIC" in source_str
            or site_id.startswith("SYN_")
            or site_id.startswith("SYNTH")
        )

        tech_str = str(row.get("technology", "")).strip().upper()
        for tc in [c for c in df.columns if "tech" in c]:
            if pd.notna(row.get(tc)):
                tech_str += " " + str(row.get(tc)).upper()

        if is_synthetic:
            syn_so_far = sum(1 for c in candidates if c["is_synthetic"])
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

        if not operator_str or operator_str.lower() == "nan":
            op_cols = [c for c in df.columns if "operator" in c]
            ops = [str(row[c]).strip() for c in op_cols if pd.notna(row.get(c)) and str(row.get(c)).strip()]
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
            "is_synthetic": is_synthetic,
        })

    if not candidates:
        raise ValueError("No valid 4G/5G stations found in dataset.")

    df_cand = pd.DataFrame(candidates).sort_values(
        by=["latitude", "longitude", "tech_priority"],
        ascending=[True, True, False],
    )
    result = df_cand.drop_duplicates(subset=["latitude", "longitude"], keep="first").copy().reset_index(drop=True)

    rrh_ids = []
    syn_counter, real_counter = 1, 1
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
# 2. LOAD-BALANCED RRH-TO-BBU ASSIGNMENT (VECTORIZED AUGMENTING PATHS)
# ============================================================

def balance_bbu_assignments(
    distance_matrix: np.ndarray,
    feasible_pool_indices: list[list[int]],
    initial_assigned: np.ndarray,
    host_rrh_to_pool: dict[int, int],
    max_extra_distance_m: float | None = None,
) -> np.ndarray:
    """
    Rebalances RRH-to-BBU assignments across all feasible options to minimize
    load variance (sum(L_j - N/K)^2) and peak load (max(L_j)), while minimizing
    the total added fronthaul distance via vectorized Bellman-Ford paths.
    """
    n_rrhs, k_pools = distance_matrix.shape
    assigned = initial_assigned.copy()
    loads = np.bincount(assigned, minlength=k_pools)

    if k_pools <= 1 or int(loads.max() - loads.min()) <= 1:
        return assigned

    # Precompute static list of valid (rrh, alt_pool) transitions
    valid_rrhs = []
    valid_alts = []
    for rrh_idx in range(n_rrhs):
        if rrh_idx in host_rrh_to_pool:
            continue
        nearest_dist = distance_matrix[rrh_idx, initial_assigned[rrh_idx]]
        for p in feasible_pool_indices[rrh_idx]:
            if max_extra_distance_m is not None:
                if distance_matrix[rrh_idx, p] > nearest_dist + max_extra_distance_m + 1e-6:
                    continue
            valid_rrhs.append(rrh_idx)
            valid_alts.append(int(p))

    if not valid_rrhs:
        return assigned

    valid_rrhs_arr = np.asarray(valid_rrhs, dtype=int)
    valid_alts_arr = np.asarray(valid_alts, dtype=int)
    valid_alt_dists = distance_matrix[valid_rrhs_arr, valid_alts_arr]

    while int(loads.max() - loads.min()) > 1:
        moved = False
        cur_pools = assigned[valid_rrhs_arr]
        diff_mask = cur_pools != valid_alts_arr
        if not np.any(diff_mask):
            break

        r_sub = valid_rrhs_arr[diff_mask]
        u_sub = cur_pools[diff_mask]
        v_sub = valid_alts_arr[diff_mask]
        delta_d = valid_alt_dists[diff_mask] - distance_matrix[r_sub, u_sub]

        # Build (K, K) minimum-cost transfer matrix in vectorized order
        order = np.argsort(delta_d)[::-1]
        best_edge_cost = np.full((k_pools, k_pools), np.inf)
        best_edge_rrh = np.full((k_pools, k_pools), -1, dtype=int)
        best_edge_cost[u_sub[order], v_sub[order]] = delta_d[order]
        best_edge_rrh[u_sub[order], v_sub[order]] = r_sub[order]

        for src in np.argsort(-loads):
            src_int = int(src)
            src_load = int(loads[src_int])
            if src_load <= 1:
                continue

            # Vectorized Bellman-Ford over (K, K) pool graph
            dist_cost = np.full(k_pools, np.inf)
            parent_pool = np.full(k_pools, -1, dtype=int)
            dist_cost[src_int] = 0.0

            for _ in range(k_pools - 1):
                cand_costs = dist_cost[:, np.newaxis] + best_edge_cost
                min_prev = np.argmin(cand_costs, axis=0)
                min_vals = cand_costs[min_prev, np.arange(k_pools)]
                improved_nodes = min_vals < dist_cost - 1e-9
                if not np.any(improved_nodes):
                    break
                dist_cost[improved_nodes] = min_vals[improved_nodes]
                parent_pool[improved_nodes] = min_prev[improved_nodes]

            best_dst = -1
            best_key = (src_load, np.inf)
            for dst in range(k_pools):
                if loads[dst] <= src_load - 2 and np.isfinite(dist_cost[dst]):
                    key = (int(loads[dst]), float(dist_cost[dst]))
                    if key < best_key:
                        best_key = key
                        best_dst = dst

            if best_dst != -1:
                curr = best_dst
                while curr != src_int:
                    prev = int(parent_pool[curr])
                    assigned[int(best_edge_rrh[prev, curr])] = curr
                    curr = prev
                loads[src_int] -= 1
                loads[best_dst] += 1
                moved = True
                break

        if not moved:
            break

    return assigned


def _fast_balanced_proxy_eval(
    dist_mat: np.ndarray,
    selected: np.ndarray,
    max_dist_per_rrh_m: np.ndarray,
    balance_weight: float,
    max_extra_distance_m: float | None,
) -> float:
    """
    Fast vectorized proxy evaluator (< 0.1 ms per candidate) used during
    medoid swap search to estimate post-balancing distance + load variance.
    """
    n_rrhs, k_pools = dist_mat.shape
    nearest_pool = np.argmin(dist_mat, axis=1)
    nearest_dist = dist_mat[np.arange(n_rrhs), nearest_pool]
    assigned = nearest_pool.copy()
    assigned[selected] = np.arange(k_pools)

    loads = np.bincount(assigned, minlength=k_pools)
    cap = int(np.ceil(n_rrhs / float(k_pools)))

    allowed = dist_mat <= max_dist_per_rrh_m[:, np.newaxis]
    if max_extra_distance_m is not None:
        allowed &= dist_mat <= (nearest_dist + max_extra_distance_m + 1e-6)[:, np.newaxis]
    allowed[selected, :] = False

    # Two fast vectorized load-shedding sweeps from overloaded to underloaded pools
    for _ in range(2):
        overloaded = np.flatnonzero(loads > cap)
        underloaded = np.flatnonzero(loads < cap)
        if len(overloaded) == 0 or len(underloaded) == 0:
            break

        cand_dists = np.where(allowed[:, underloaded], dist_mat[:, underloaded], np.inf)
        best_u_slot = np.argmin(cand_dists, axis=1)
        best_u_dist = cand_dists[np.arange(n_rrhs), best_u_slot]
        best_u_pool = underloaded[best_u_slot]

        for p in overloaded[np.argsort(-loads[overloaded])]:
            excess = int(loads[p] - cap)
            if excess <= 0:
                continue
            r_in_p = np.flatnonzero((assigned == p) & np.isfinite(best_u_dist))
            if len(r_in_p) == 0:
                continue
            penalties = best_u_dist[r_in_p] - dist_mat[r_in_p, p]
            for r_idx in r_in_p[np.argsort(penalties)]:
                dst_p = int(best_u_pool[r_idx])
                if loads[dst_p] < cap:
                    assigned[r_idx] = dst_p
                    loads[p] -= 1
                    loads[dst_p] += 1
                    excess -= 1
                    if excess == 0:
                        break

    total_dist_m = float(dist_mat[np.arange(n_rrhs), assigned].sum())
    ideal_load = n_rrhs / float(k_pools)
    imbalance_sse = float(np.sum((loads.astype(float) - ideal_load) ** 2))
    return total_dist_m + balance_weight * imbalance_sse


# ============================================================
# 3. JOINT ZONED & LOAD-AWARE K-MEDOIDS ALGORITHM
# ============================================================

def _build_feasible_medoids_zoned(
    D: np.ndarray,
    zone_ids: np.ndarray,
    zone_quotas: list[int],
    max_dist_per_rrh_m: np.ndarray,
) -> np.ndarray:
    n_points = len(D)
    total_k = sum(zone_quotas)
    target_cap = max(1, int(np.ceil(n_points / float(total_k))))
    selected = []
    zone_counts = [0, 0]
    nearest_distances = np.full(n_points, np.inf)

    while len(selected) < total_k:
        eligible_zones = [z for z in range(len(zone_quotas)) if zone_counts[z] < zone_quotas[z]]
        remaining = np.array(
            [idx for idx in range(n_points) if idx not in selected and zone_ids[idx] in eligible_zones],
            dtype=int,
        )
        if len(remaining) == 0:
            break

        uncovered = nearest_distances > max_dist_per_rrh_m
        if uncovered.any():
            coverage_gain = np.count_nonzero(
                D[uncovered][:, remaining] <= max_dist_per_rrh_m[uncovered, np.newaxis],
                axis=0,
            )
            best_gain = int(coverage_gain.max())
            candidates = remaining[coverage_gain == best_gain] if best_gain > 0 else remaining
        else:
            candidates = remaining

        finite_base = np.where(np.isfinite(nearest_distances), nearest_distances, 1e6)
        gains = np.maximum(0.0, finite_base[:, np.newaxis] - D[:, candidates])
        if len(gains) > target_cap:
            top_gains = np.partition(gains, len(gains) - target_cap, axis=0)[-target_cap:]
        else:
            top_gains = gains
        chosen = int(candidates[np.argmax(top_gains.sum(axis=0))])
        selected.append(chosen)
        zone_counts[int(zone_ids[chosen])] += 1
        nearest_distances = np.minimum(nearest_distances, D[:, chosen])

    if len(selected) != total_k or np.any(nearest_distances > max_dist_per_rrh_m):
        uncovered_mask = nearest_distances > max_dist_per_rrh_m
        uncovered_by_zone = np.bincount(zone_ids[uncovered_mask], minlength=2)
        raise ValueError(
            f"No feasible placement found for K={total_k} (quotas {zone_quotas}) "
            f"under the per-territory distance limits. "
            f"Uncovered RRHs -> Area 1: {uncovered_by_zone[0]}, Area 2: {uncovered_by_zone[1]}. "
            "Increase K, relax area max distances, or pass --allow_feasibility_rebalance."
        )

    return np.asarray(selected, dtype=int)


def _run_distance_swap(
    D: np.ndarray,
    selected: np.ndarray,
    zone_ids: np.ndarray,
    max_iter: int,
    max_dist_per_rrh_m: np.ndarray | None = None,
) -> np.ndarray:
    n_points = len(D)
    k_pools = len(selected)
    zone_candidates = {z: np.flatnonzero(zone_ids == z) for z in np.unique(zone_ids)}

    for _ in range(max_iter):
        improved = False
        dist_to_sel = D[:, selected]
        best_slot = np.argmin(dist_to_sel, axis=1)
        best_dist = dist_to_sel[np.arange(n_points), best_slot]
        second_dist = (
            np.partition(dist_to_sel, 1, axis=1)[:, 1]
            if k_pools >= 2
            else np.full(n_points, np.inf)
        )
        current_cost = float(best_dist.sum())
        selected_set = set(selected.tolist())

        for i in range(k_pools):
            medoid_idx = int(selected[i])
            z = int(zone_ids[medoid_idx])
            base_dist = np.where(best_slot == i, second_dist, best_dist)

            for cand in zone_candidates[z]:
                cand_int = int(cand)
                if cand_int in selected_set:
                    continue
                trial_distances = np.minimum(base_dist, D[:, cand_int])
                if max_dist_per_rrh_m is not None and np.any(trial_distances > max_dist_per_rrh_m):
                    continue
                trial_cost = float(trial_distances.sum())
                if trial_cost < current_cost - 1e-6:
                    selected[i] = cand_int
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break

    return selected


def _run_joint_load_aware_swap(
    D: np.ndarray,
    selected: np.ndarray,
    zone_ids: np.ndarray,
    max_dist_per_rrh_m: np.ndarray,
    balance_weight: float,
    max_extra_distance_m: float | None,
    max_iter: int = 15,
    max_neighbors: int = 25,
) -> np.ndarray:
    """
    Fast joint location + load-balance SWAP using vectorized proxy evaluation
    over the nearest intra-zone neighbors.
    """
    n_points = len(D)
    k_pools = len(selected)
    zone_candidates = {z: np.flatnonzero(zone_ids == z) for z in np.unique(zone_ids)}

    current_score = _fast_balanced_proxy_eval(
        D[:, selected], selected, max_dist_per_rrh_m, balance_weight, max_extra_distance_m
    )

    for _ in range(max_iter):
        improved = False
        dist_to_sel = D[:, selected]
        best_slot = np.argmin(dist_to_sel, axis=1)
        best_dist = dist_to_sel[np.arange(n_points), best_slot]
        second_dist = (
            np.partition(dist_to_sel, 1, axis=1)[:, 1]
            if k_pools >= 2
            else np.full(n_points, np.inf)
        )
        selected_set = set(selected.tolist())

        # Prioritize pools furthest from ideal load
        cur_loads = np.bincount(best_slot, minlength=k_pools)
        ideal_load = n_points / float(k_pools)
        medoid_order = np.argsort(-np.abs(cur_loads - ideal_load))

        for i in medoid_order:
            i_int = int(i)
            medoid_idx = int(selected[i_int])
            z = int(zone_ids[medoid_idx])
            base_dist = np.where(best_slot == i_int, second_dist, best_dist)
            orphans = np.flatnonzero(base_dist > max_dist_per_rrh_m)

            z_cands = zone_candidates[z]
            if len(orphans) > 0:
                valid_mask = np.all(
                    D[np.ix_(orphans, z_cands)] <= max_dist_per_rrh_m[orphans, np.newaxis],
                    axis=0,
                )
                feasible_cands = z_cands[valid_mask]
            else:
                feasible_cands = z_cands

            if len(feasible_cands) > max_neighbors:
                nearest_order = np.argsort(D[medoid_idx, feasible_cands])[:max_neighbors]
                feasible_cands = feasible_cands[nearest_order]

            best_cand = -1
            best_cand_score = current_score - 1e-4

            for cand in feasible_cands:
                cand_int = int(cand)
                if cand_int in selected_set:
                    continue
                trial_selected = selected.copy()
                trial_selected[i_int] = cand_int
                score = _fast_balanced_proxy_eval(
                    D[:, trial_selected],
                    trial_selected,
                    max_dist_per_rrh_m,
                    balance_weight,
                    max_extra_distance_m,
                )
                if score < best_cand_score:
                    best_cand_score = score
                    best_cand = cand_int

            if best_cand != -1:
                selected[i_int] = best_cand
                current_score = best_cand_score
                improved = True
                break

        if not improved:
            break

    return selected


def k_medoids_kmedian_zoned(
    X: np.ndarray,
    zone_ids: np.ndarray,
    zone_quotas: list[int],
    max_dist_per_rrh_m: np.ndarray,
    assignment_mode: str = "balanced",
    balance_weight: float = 150.0,
    max_extra_distance_m: float | None = None,
    max_iter: int = 100,
) -> list[int]:
    n_points = len(X)
    total_k = sum(zone_quotas)
    target_cap = max(1, int(np.ceil(n_points / float(total_k))))
    D = np.linalg.norm(X[:, np.newaxis, :] - X[np.newaxis, :, :], axis=2)

    selected = []
    zone_counts = [0, 0]
    current_best_dist = np.full(n_points, np.inf)

    for _ in range(total_k):
        eligible_zones = [z for z in range(len(zone_quotas)) if zone_counts[z] < zone_quotas[z]]
        candidates = np.array(
            [c for c in range(n_points) if c not in selected and zone_ids[c] in eligible_zones],
            dtype=int,
        )
        if assignment_mode == "balanced" and balance_weight > 0.0:
            finite_base = np.where(np.isfinite(current_best_dist), current_best_dist, 1e6)
            gains = np.maximum(0.0, finite_base[:, np.newaxis] - D[:, candidates])
            if len(gains) > target_cap:
                top_gains = np.partition(gains, len(gains) - target_cap, axis=0)[-target_cap:]
            else:
                top_gains = gains
            best_cand = int(candidates[np.argmax(top_gains.sum(axis=0))])
        else:
            trial_dists = np.minimum(current_best_dist[:, np.newaxis], D[:, candidates])
            best_cand = int(candidates[np.argmin(trial_dists.sum(axis=0))])

        selected.append(best_cand)
        zone_counts[int(zone_ids[best_cand])] += 1
        current_best_dist = np.minimum(current_best_dist, D[:, best_cand])

    selected = np.array(selected, dtype=int)
    selected = _run_distance_swap(D, selected, zone_ids, max_iter=max_iter, max_dist_per_rrh_m=None)

    if np.any(np.min(D[:, selected], axis=1) > max_dist_per_rrh_m):
        selected = _build_feasible_medoids_zoned(D, zone_ids, zone_quotas, max_dist_per_rrh_m)

    selected = _run_distance_swap(
        D, selected, zone_ids, max_iter=max_iter, max_dist_per_rrh_m=max_dist_per_rrh_m
    )

    if assignment_mode == "balanced" and balance_weight > 0.0:
        selected = _run_joint_load_aware_swap(
            D=D,
            selected=selected,
            zone_ids=zone_ids,
            max_dist_per_rrh_m=max_dist_per_rrh_m,
            balance_weight=balance_weight,
            max_extra_distance_m=max_extra_distance_m,
        )

    return selected.tolist()


# ============================================================
# 4. ASSIGN RRHs TO BBU POOLS & EXPORT METRICS
# ============================================================

def assign_rrhs_to_pools(
    rrhs: pd.DataFrame,
    X: np.ndarray,
    selected_indices: list[int],
    max_dist_per_rrh_m: np.ndarray,
    assignment_mode: str = "balanced",
    max_extra_distance_m: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered_indices = sorted(
        selected_indices,
        key=lambda idx: (int(rrhs.iloc[idx]["territory_id"]), str(rrhs.iloc[idx]["rrh_id"])),
    )
    host_rrh_to_pool = {
        int(rrh_idx): pool_slot
        for pool_slot, rrh_idx in enumerate(ordered_indices)
    }

    centers = X[ordered_indices]
    distance_matrix = np.linalg.norm(
        X[:, np.newaxis, :] - centers[np.newaxis, :, :],
        axis=2,
    )

    pool_ids = [f"BBU_{i+1:02d}" for i in range(len(ordered_indices))]
    pool_territories = [rrhs.iloc[idx]["territory_name"] for idx in ordered_indices]

    feasible_pool_indices = []
    feasible_pool_ids = []
    feasible_distances_km = []

    for rrh_idx, distances in enumerate(distance_matrix):
        rrh_limit_m = max_dist_per_rrh_m[rrh_idx]
        ranked_feasible = sorted(
            np.flatnonzero(distances <= rrh_limit_m),
            key=lambda index: (distances[index], pool_ids[index]),
        )
        if not ranked_feasible:
            raise ValueError(
                f"RRH {rrhs.iloc[rrh_idx]['rrh_id']} ({rrhs.iloc[rrh_idx]['territory_name']}) "
                f"has no BBU Pool within {rrh_limit_m / 1000.0:g} km."
            )
        feasible_pool_indices.append(ranked_feasible)
        feasible_pool_ids.append(json.dumps([pool_ids[idx] for idx in ranked_feasible]))
        feasible_distances_km.append(
            json.dumps([round(float(distances[idx] / 1000.0), 4) for idx in ranked_feasible])
        )

    nearest_pool = np.array([indices[0] for indices in feasible_pool_indices], dtype=int)
    nearest_distance = distance_matrix[np.arange(len(X)), nearest_pool]

    if assignment_mode == "balanced":
        assigned_pool = balance_bbu_assignments(
            distance_matrix=distance_matrix,
            feasible_pool_indices=feasible_pool_indices,
            initial_assigned=nearest_pool,
            host_rrh_to_pool=host_rrh_to_pool,
            max_extra_distance_m=max_extra_distance_m,
        )
    else:
        assigned_pool = nearest_pool.copy()

    assigned_distance = distance_matrix[np.arange(len(X)), assigned_pool]

    result = rrhs.copy()
    result["bbu_pool"] = [pool_ids[i] for i in assigned_pool]
    result["bbu_territory"] = [pool_territories[i] for i in assigned_pool]
    result["nearest_bbu_pool"] = [pool_ids[i] for i in nearest_pool]
    result["reassigned_for_balance"] = assigned_pool != nearest_pool
    result["cross_territory_link"] = result["territory_name"] != result["bbu_territory"]
    result["distance_to_bbu_km"] = assigned_distance / 1000.0
    result["nearest_distance_km"] = nearest_distance / 1000.0
    result["distance_penalty_km"] = (assigned_distance - nearest_distance) / 1000.0
    result["latency_rtt_us"] = result["distance_to_bbu_km"] * RTT_US_PER_KM
    result["max_allowed_distance_km"] = max_dist_per_rrh_m / 1000.0
    result["max_allowed_rtt_us"] = result["max_allowed_distance_km"] * RTT_US_PER_KM
    result["feasible_bbu_pools"] = feasible_pool_ids
    result["feasible_distances_km"] = feasible_distances_km
    result["num_feasible_bbu_pools"] = [len(indices) for indices in feasible_pool_indices]

    pools = rrhs.iloc[ordered_indices].copy().reset_index(drop=True)
    pools["bbu_pool"] = pool_ids
    pools["num_assigned_rrhs"] = [int(np.sum(assigned_pool == i)) for i in range(len(ordered_indices))]
    pools["num_nearest_rrhs"] = [int(np.sum(nearest_pool == i)) for i in range(len(ordered_indices))]
    pools["num_cross_territory_rrhs"] = [
        int(np.sum((assigned_pool == i) & (result["cross_territory_link"].values)))
        for i in range(len(ordered_indices))
    ]

    return result, pools


# ============================================================
# 5. MAP GENERATION WITH KML TERRITORIES & BALANCE INDICATORS
# ============================================================

def generate_map(
    rrhs: pd.DataFrame,
    bbus: pd.DataFrame,
    territories: list[dict],
    zone_max_dist_km: list[float],
    output_html_path: str,
) -> None:
    pool_ids = sorted(rrhs["bbu_pool"].dropna().unique(), key=str)
    cmap = plt.get_cmap("tab20", max(len(pool_ids), 1))
    pool_colors = {pool_id: to_hex(cmap(i % 20)) for i, pool_id in enumerate(pool_ids)}

    m = folium.Map(
        location=[rrhs["latitude"].mean(), rrhs["longitude"].mean()],
        zoom_start=11,
        tiles=None,
    )
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Esri Street Map",
        overlay=False,
        control=True,
    ).add_to(m)

    territory_colors = ["#1f77b4", "#ff7f0e"]
    for t_idx, terr in enumerate(territories):
        max_km = zone_max_dist_km[t_idx]
        max_us = max_km * RTT_US_PER_KM
        t_layer = folium.FeatureGroup(
            name=f"Territory {t_idx + 1}: {terr['name']} (≤ {max_km:g} km)",
            show=True,
        )
        t_color = territory_colors[t_idx % len(territory_colors)]
        for poly in terr["polygons"]:
            outer_latlon = [[lat, lon] for lon, lat in poly["outer_lonlat"]]
            holes_latlon = [
                [[lat, lon] for lon, lat in hole]
                for hole in poly["holes_lonlat"]
            ]
            locations = [outer_latlon, *holes_latlon] if holes_latlon else outer_latlon
            folium.Polygon(
                locations=locations,
                color=t_color,
                weight=2.5,
                fill=True,
                fill_color=t_color,
                fill_opacity=0.07,
                tooltip=(
                    f"Area {t_idx + 1}: {terr['name']} ({terr['area_km2']:.1f} km²) | "
                    f"Limit: {max_km:g} km ({max_us:g} us RTT)"
                ),
            ).add_to(t_layer)
        t_layer.add_to(m)

    real_rrh_layer = folium.FeatureGroup(name="Real RRHs", show=True)
    syn_rrh_layer = folium.FeatureGroup(name="Synthetic RRHs", show=True)

    for _, row in rrhs.iterrows():
        pool_id = row["bbu_pool"]
        color = pool_colors.get(pool_id, "gray")
        is_syn = bool(row.get("is_synthetic", False))
        is_reassigned = bool(row.get("reassigned_for_balance", False))
        cross_tag = " [CROSS-ZONE]" if row.get("cross_territory_link", False) else ""
        bal_tag = (
            f" (Balanced from {row['nearest_bbu_pool']}, +{row['distance_penalty_km']:.3f} km)"
            if is_reassigned
            else ""
        )

        popup_text = f"""
        <b>RRH ID:</b> {row.get('rrh_id')}<br>
        <b>Territory:</b> {row.get('territory_name')}<br>
        <b>Type:</b> {'SYNTHETIC' if is_syn else 'REAL'}<br>
        <b>Site ID:</b> {row.get('site_id')}<br>
        <b>Tech:</b> {row.get('technology')}<br>
        <b>Assigned BBU:</b> {pool_id} ({row.get('bbu_territory')}){cross_tag}{bal_tag}<br>
        <b>Distance / RTT:</b> {row.get('distance_to_bbu_km'):.3f} km / {row.get('latency_rtt_us'):.1f} us<br>
        <b>Territory Limit:</b> {row.get('max_allowed_distance_km'):g} km / {row.get('max_allowed_rtt_us'):g} us
        """

        if is_syn:
            folium.CircleMarker(
                location=[row["latitude"], row["longitude"]],
                radius=6,
                color="red",
                fill=True,
                fill_color=color,
                fill_opacity=0.65,
                weight=2,
                dash_array="4, 4",
                popup=folium.Popup(popup_text, max_width=360),
                tooltip=f"SYNTH {row.get('rrh_id')} ({row.get('territory_name')}) -> {pool_id}",
            ).add_to(syn_rrh_layer)
        else:
            folium.CircleMarker(
                location=[row["latitude"], row["longitude"]],
                radius=5,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.85,
                weight=1,
                popup=folium.Popup(popup_text, max_width=360),
                tooltip=f"{row.get('rrh_id')} ({row.get('territory_name')}) -> {pool_id}",
            ).add_to(real_rrh_layer)

    real_rrh_layer.add_to(m)
    syn_rrh_layer.add_to(m)

    bbu_layer = folium.FeatureGroup(name="BBU Pools (Joint Zoned + Balanced)", show=True)
    for _, row in bbus.iterrows():
        pool_id = row["bbu_pool"]
        is_syn_pool = bool(row.get("is_synthetic", False))
        popup_text = f"""
        <b>BBU Pool:</b> {pool_id}<br>
        <b>Territory:</b> {row.get('territory_name')}<br>
        <b>Location Site:</b> {row.get('site_id')} ({'SYNTHETIC' if is_syn_pool else 'REAL'})<br>
        <b>Assigned RRHs (Balanced):</b> {row.get('num_assigned_rrhs')}
        (Nearest-only at these sites: {row.get('num_nearest_rrhs')})<br>
        <b>Cross-territory RRHs:</b> {row.get('num_cross_territory_rrhs')}
        """
        folium.Marker(
            location=[row["latitude"], row["longitude"]],
            popup=folium.Popup(popup_text, max_width=340),
            tooltip=f"{pool_id} [{row.get('territory_name')}] ({row.get('num_assigned_rrhs')} RRHs)",
            icon=folium.Icon(
                color="purple" if is_syn_pool else "darkblue",
                icon="server",
                prefix="fa",
            ),
        ).add_to(bbu_layer)
    bbu_layer.add_to(m)

    connection_layer = folium.FeatureGroup(name="RRH - BBU Connections", show=True)
    bbu_coords = {
        row["bbu_pool"]: [row["latitude"], row["longitude"]]
        for _, row in bbus.iterrows()
    }
    for _, row in rrhs.iterrows():
        pool_id = row["bbu_pool"]
        if pool_id not in bbu_coords:
            continue
        is_cross = bool(row.get("cross_territory_link", False))
        is_reassigned = bool(row.get("reassigned_for_balance", False))
        folium.PolyLine(
            locations=[[row["latitude"], row["longitude"]], bbu_coords[pool_id]],
            color="#d62728" if is_cross else pool_colors.get(pool_id, "gray"),
            weight=2.0 if (is_cross or is_reassigned) else 1.2,
            opacity=0.75 if (is_cross or is_reassigned) else 0.45,
            dash_array="5, 5" if (is_cross or is_reassigned) else None,
        ).add_to(connection_layer)
    connection_layer.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds([
        [rrhs["latitude"].min(), rrhs["longitude"].min()],
        [rrhs["latitude"].max(), rrhs["longitude"].max()],
    ])
    m.save(output_html_path)


# ============================================================
# 6. MAIN EXECUTION
# ============================================================

def resolve_zone_max_distances_km(args) -> list[float]:
    base_dist_km = (
        args.max_latency_us / RTT_US_PER_KM
        if args.max_latency_us is not None
        else args.max_distance_km
    )

    if args.area1_max_latency_us is not None:
        area1_km = args.area1_max_latency_us / RTT_US_PER_KM
    elif args.area1_max_distance_km is not None:
        area1_km = args.area1_max_distance_km
    else:
        area1_km = base_dist_km

    if args.area2_max_latency_us is not None:
        area2_km = args.area2_max_latency_us / RTT_US_PER_KM
    elif args.area2_max_distance_km is not None:
        area2_km = args.area2_max_distance_km
    else:
        area2_km = base_dist_km

    if area1_km <= 0 or area2_km <= 0:
        raise ValueError("Maximum distances and latencies must be strictly positive.")

    return [float(area1_km), float(area2_km)]


def main():
    root = find_project_root(Path(__file__))
    default_input = root / "data" / "processed" / "scenario_sites.csv"
    default_kml = root / "data" / "input" / "separated_area.kml"

    parser = argparse.ArgumentParser(
        description="Joint Zoned & Load-Balanced BBU Pool Optimizer"
    )
    parser.add_argument(
        "-k", "--k_pools", type=int, default=40,
        help="Número total de BBU pools a repartir entre ambas áreas (default: 40)",
    )
    parser.add_argument(
        "--assignment_mode", choices=["balanced", "nearest"], default="balanced",
        help="Modo de asignación RRH->BBU: 'balanced' (default) o 'nearest'",
    )
    parser.add_argument(
        "--balance_weight", type=float, default=150.0,
        help="Peso (en metros por unidad de varianza de carga) para reubicar BBU pools buscando equilibrio (default: 150.0; 0 = solo distancia al ubicar)",
    )
    parser.add_argument(
        "--max_extra_distance_km", type=float, default=None,
        help="Penalización máxima de distancia (en km) respecto al BBU más cercano al balancear (default: sin límite extra dentro del umbral del área)",
    )
    parser.add_argument(
        "--max_distance_km", type=float, default=DEFAULT_MAX_DISTANCE_KM,
        help=f"Distancia máxima general en km si no se especifica por área (default: {DEFAULT_MAX_DISTANCE_KM})",
    )
    parser.add_argument(
        "--max_latency_us", type=float, default=None,
        help="Latencia máxima general RTT en us (sobreescribe --max_distance_km usando 10 us/km)",
    )
    parser.add_argument(
        "--area1_max_distance_km", type=float, default=None,
        help="Distancia máxima en km para el Área 1 (polígono interior / menor superficie, ej. Barcelona)",
    )
    parser.add_argument(
        "--area2_max_distance_km", type=float, default=None,
        help="Distancia máxima en km para el Área 2 (polígono exterior / mayor superficie, ej. Afueras)",
    )
    parser.add_argument(
        "--area1_max_latency_us", type=float, default=None,
        help="Latencia máxima RTT en us para el Área 1 (sobreescribe --area1_max_distance_km)",
    )
    parser.add_argument(
        "--area2_max_latency_us", type=float, default=None,
        help="Latencia máxima RTT en us para el Área 2 (sobreescribe --area2_max_distance_km)",
    )
    parser.add_argument(
        "--input_path", type=str, default=str(default_input),
        help=f"Ruta al CSV de celdas (default: {default_input})",
    )
    parser.add_argument(
        "--kml_path", type=str, default=str(default_kml),
        help=f"Ruta al KML con los dos polígonos (default: {default_kml})",
    )
    parser.add_argument(
        "--allow_feasibility_rebalance", action="store_true",
        help="Transfiere BBU pools entre áreas si el reparto proporcional exacto no alcanza a cubrir la distancia máxima",
    )
    parser.add_argument(
        "--output_dir", type=Path, default=None,
        help="Directorio de salida (por defecto: test2/output/BBU-pool-allocation_k<K>_zoned_<mode>)",
    )

    args = parser.parse_args()

    try:
        zone_max_dist_km = resolve_zone_max_distances_km(args)
    except ValueError as err:
        parser.error(str(err))

    # 1. Load RRHs and project to UTM 31N meters
    rrhs = load_rrhs(args.input_path)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:25831", always_xy=True)
    x, y = transformer.transform(rrhs["longitude"].values, rrhs["latitude"].values)
    X = np.column_stack((x, y))

    # 2. Load both polygons from inputs/separated_area.kml & classify RRHs
    territories = load_territories_from_kml(Path(args.kml_path), transformer)
    zone_ids, n_snapped = classify_rrhs_by_territories(X, territories)

    rrhs["territory_id"] = zone_ids
    rrhs["territory_name"] = [territories[z]["name"] for z in zone_ids]

    zone_max_dist_m = np.array([d * 1000.0 for d in zone_max_dist_km], dtype=float)
    max_dist_per_rrh_m = zone_max_dist_m[zone_ids]

    # 3. Automatic proportional split of K
    quotas = compute_proportional_quotas(zone_ids, args.k_pools)
    counts = np.bincount(zone_ids, minlength=2)
    ideal_rrhs_per_pool = len(rrhs) / float(args.k_pools)

    print("\n============================================================")
    print(" JOINT ZONED & LOAD-BALANCED K-MEDIAN OPTIMIZATION")
    print("============================================================")
    print(f"Input CSV:                 {args.input_path}")
    print(f"Input KML:                 {args.kml_path}")
    print(f"Assignment mode:           {args.assignment_mode.upper()} (balance_weight={args.balance_weight:g})")
    print(f"Total RRHs:                {len(rrhs)} (Real: {(~rrhs['is_synthetic']).sum()}, Synth: {rrhs['is_synthetic'].sum()})")
    print(f"Ideal target load:         {ideal_rrhs_per_pool:.2f} RRHs / BBU pool")
    if n_snapped > 0:
        print(f"Boundary-snapped RRHs:     {n_snapped} (assigned to nearest polygon)")
    print("\nTERRITORIAL DISTRIBUTION, CONSTRAINTS & PROPORTIONAL BBU ALLOCATION:")
    for z in range(2):
        pct_rrh = 100.0 * counts[z] / len(rrhs)
        pct_bbu = 100.0 * quotas[z] / args.k_pools
        d_km = zone_max_dist_km[z]
        rtt_us = d_km * RTT_US_PER_KM
        print(
            f" - Area {z + 1} [{territories[z]['name']:<16}] ({territories[z]['area_km2']:6.1f} km²) | "
            f"Limit: {d_km:g} km ({rtt_us:g} us) | "
            f"RRHs: {counts[z]:4d} ({pct_rrh:5.1f}%) -> BBU Pools: {quotas[z]:2d} ({pct_bbu:5.1f}%)"
        )

    # 4. Run joint zoned & load-aware K-Medoids placement
    max_extra_m = args.max_extra_distance_km * 1000.0 if args.max_extra_distance_km is not None else None
    active_quotas = list(quotas)
    while True:
        try:
            selected_indices = k_medoids_kmedian_zoned(
                X=X,
                zone_ids=zone_ids,
                zone_quotas=active_quotas,
                max_dist_per_rrh_m=max_dist_per_rrh_m,
                assignment_mode=args.assignment_mode,
                balance_weight=args.balance_weight,
                max_extra_distance_m=max_extra_m,
            )
            break
        except ValueError as err:
            if not args.allow_feasibility_rebalance:
                parser.error(str(err))

            rebalanced = False
            for target_z in [1, 0]:
                donor_z = 1 - target_z
                if active_quotas[donor_z] > 1 and active_quotas[target_z] < counts[target_z]:
                    active_quotas[target_z] += 1
                    active_quotas[donor_z] -= 1
                    print(f"[Rebalance] Adjusting quotas to {active_quotas} to satisfy per-area coverage...")
                    rebalanced = True
                    break
            if not rebalanced:
                parser.error(str(err))

    # 5. Finalize assignments & export
    assignments, pools = assign_rrhs_to_pools(
        rrhs=rrhs,
        X=X,
        selected_indices=selected_indices,
        max_dist_per_rrh_m=max_dist_per_rrh_m,
        assignment_mode=args.assignment_mode,
        max_extra_distance_m=max_extra_m,
    )

    out_dir = args.output_dir or (
        Path(__file__).resolve().parent / "output" / f"BBU-pool-allocation_k{args.k_pools}_zoned_{args.assignment_mode}"
    )
    os.makedirs(out_dir, exist_ok=True)

    n_syn = int(rrhs["is_synthetic"].sum())
    mode_tag = f"zoned_{args.assignment_mode}_kmedian"
    tag = f"k{args.k_pools}_s{n_syn}_{mode_tag}" if n_syn > 0 else f"k{args.k_pools}_{mode_tag}"

    assignments_file = out_dir / f"rrh_bbu_{tag}_assignments.csv"
    pools_file = out_dir / f"bbu_pool_{tag}_locations.csv"
    map_file = out_dir / f"rrh_bbu_{tag}_assignments_map.html"

    assignments.to_csv(assignments_file, index=False)
    pools.to_csv(pools_file, index=False)
    generate_map(assignments, pools, territories, zone_max_dist_km, str(map_file))

    print("\nGLOBAL PERFORMANCE METRICS:")
    print(f" - Total distance sum (sum d): {assignments['distance_to_bbu_km'].sum():.3f} km")
    print(f" - Average distance (avg d):   {assignments['distance_to_bbu_km'].mean():.3f} km (nearest at these sites: {assignments['nearest_distance_km'].mean():.3f} km)")
    print(f" - Maximum distance (max d):   {assignments['distance_to_bbu_km'].max():.3f} km")
    print(f" - Reassigned for balance:     {int(assignments['reassigned_for_balance'].sum())} RRHs")
    print(f" - Cross-territory RRH links:  {int(assignments['cross_territory_link'].sum())} RRHs")

    print("\nLOAD BALANCE COMPARISON (RRHs PER BBU POOL):")
    near_loads = pools["num_nearest_rrhs"]
    bal_loads = pools["num_assigned_rrhs"]
    print(
        f" - Nearest-only at these sites -> Min: {near_loads.min():2d} | Avg: {near_loads.mean():5.2f} | "
        f"Max: {near_loads.max():2d} | StdDev: {near_loads.std():5.2f}"
    )
    print(
        f" - Joint balanced ({args.assignment_mode:<8})  -> Min: {bal_loads.min():2d} | Avg: {bal_loads.mean():5.2f} | "
        f"Max: {bal_loads.max():2d} | StdDev: {bal_loads.std():5.2f}"
    )

    print("\nRRHs PER BBU POOL BY TERRITORY:")
    pool_summary = (
        pools.groupby("territory_name")
        .agg(
            bbu_pools=("bbu_pool", "count"),
            nearest_min=("num_nearest_rrhs", "min"),
            nearest_max=("num_nearest_rrhs", "max"),
            nearest_stddev=("num_nearest_rrhs", lambda values: values.std(ddof=0)),
            balanced_min=("num_assigned_rrhs", "min"),
            balanced_avg=("num_assigned_rrhs", "mean"),
            balanced_max=("num_assigned_rrhs", "max"),
            balanced_stddev=("num_assigned_rrhs", lambda values: values.std(ddof=0)),
        )
        .round(2)
    )
    print(pool_summary.to_string())
    territory_summary_file = out_dir / f"territory_load_summary_{tag}.csv"
    pool_summary.reset_index().to_csv(territory_summary_file, index=False)

    print("\nEXPORTED FILES:")
    print(f" - {assignments_file}")
    print(f" - {pools_file}")
    print(f" - {territory_summary_file}")
    print(f" - {map_file}")


if __name__ == "__main__":
    main()