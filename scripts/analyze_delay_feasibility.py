#!/usr/bin/env python3
"""Evaluate fixed phase-one pools; no relocation, activation or capacity model."""

import argparse
import csv
import math
from pathlib import Path

from pyproj import Transformer


def read_locations(path, id_column):
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {id_column, "latitude", "longitude"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"{path}: required columns: {', '.join(sorted(required))}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path}: empty dataset")
    seen = set()
    for row in rows:
        identifier = row[id_column].strip()
        if not identifier or identifier in seen:
            raise ValueError(f"{path}: empty or duplicate {id_column}: {identifier!r}")
        seen.add(identifier)
        row[id_column] = identifier
        lat, lon = float(row["latitude"]), float(row["longitude"])
        if not (math.isfinite(lat) and math.isfinite(lon)
                and -90 < lat < 90 and -180 <= lon <= 180):
            raise ValueError(f"{path}: invalid coordinates for {identifier}")
    return rows


def analyze(rrhs, pools, max_rtt_us=200.0, speed_km_s=200000.0,
            route_factor=1.0, fixed_rtt_us=0.0):
    """Distances use the same EPSG:25831 projection as phase one.

    RTT = 2 * distance_km * route_factor / speed_km_s * 1e6 + fixed_rtt_us.
    The limit is strict: RTT == max_rtt_us is infeasible. Ties use pool ID.
    """
    parameters = (max_rtt_us, speed_km_s, route_factor, fixed_rtt_us)
    if not all(math.isfinite(value) for value in parameters):
        raise ValueError("Delay parameters must be finite")
    if max_rtt_us <= 0 or speed_km_s <= 0 or route_factor < 1 or fixed_rtt_us < 0:
        raise ValueError("Require max RTT > 0, speed > 0, route factor >= 1, fixed RTT >= 0")
    if not rrhs or not pools:
        raise ValueError("RRH and pool datasets must be nonempty")
    pools = sorted(pools, key=lambda row: row["bbu_pool"])
    pool_ids = {row["bbu_pool"] for row in pools}
    for row in rrhs:
        original = (row.get("bbu_pool") or "").strip()
        if original and original not in pool_ids:
            raise ValueError(f"{row['rrh_id']}: original pool {original!r} absent from pools input")
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:25831", always_xy=True)

    def project(row):
        point = transformer.transform(float(row["longitude"]), float(row["latitude"]))
        if not all(math.isfinite(value) for value in point):
            raise ValueError("Coordinates cannot be projected to EPSG:25831")
        return point

    centers = [(row["bbu_pool"], project(row)) for row in pools]
    summaries, links = [], []
    for rrh in rrhs:
        x, y = project(rrh)
        candidates = []
        for pool_id, (px, py) in centers:
            distance = math.hypot(x - px, y - py) / 1000
            rtt = 2 * distance * route_factor / speed_km_s * 1e6 + fixed_rtt_us
            candidates.append({
                "rrh_id": rrh["rrh_id"], "bbu_pool": pool_id,
                "distance_km": distance, "route_distance_km": distance * route_factor,
                "rtt_us": rtt, "is_feasible": rtt < max_rtt_us,
            })
        ranked = sorted(candidates, key=lambda row: (row["rtt_us"], row["bbu_pool"]))
        feasible = [row for row in ranked if row["is_feasible"]]
        best = feasible[0]["bbu_pool"] if feasible else ""
        original_id = (rrh.get("bbu_pool") or "").strip()
        original = next((row for row in candidates if row["bbu_pool"] == original_id), None)
        summary = {key: rrh.get(key, "") for key in (
            "rrh_id", "site_id", "latitude", "longitude", "is_synthetic", "operator", "technology")}
        summary.update({
            "original_bbu_pool": original_id,
            "original_rtt_us": original["rtt_us"] if original else "",
            "original_is_feasible": original["is_feasible"] if original else "",
            "nearest_bbu_pool": ranked[0]["bbu_pool"],
            "nearest_distance_km": ranked[0]["distance_km"],
            "nearest_rtt_us": ranked[0]["rtt_us"],
            "best_feasible_bbu_pool": best,
            "feasible_pool_count": len(feasible),
            "feasible_pools": ";".join(row["bbu_pool"] for row in feasible),
            "service_status": "served" if feasible else "unserved",
        })
        summaries.append(summary)
        for row in candidates:
            row["is_original_assignment"] = row["bbu_pool"] == original_id
            row["is_best_feasible"] = row["bbu_pool"] == best
            row["feasibility_group"] = (
                "infeasible" if not row["is_feasible"] else
                "only_option" if len(feasible) == 1 else
                "best_with_alternatives" if row["is_best_feasible"] else "alternative")
        links.extend(candidates)
    return summaries, links


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rrhs", type=Path, required=True,
                        help="Phase-one assignments CSV (or scenario CSV)")
    parser.add_argument("--pools", type=Path, required=True, help="Phase-one pool locations CSV")
    parser.add_argument("--output-dir", type=Path,
                        help="Default: output/delay (filenames include K and synthetic count)")
    parser.add_argument("--max-rtt-us", type=float, default=200.0)
    parser.add_argument("--speed-km-s", type=float, default=200000.0)
    parser.add_argument("--route-factor", type=float, default=1.0)
    parser.add_argument("--fixed-rtt-us", type=float, default=0.0,
                        help="Additional total round-trip delay, not one-way delay")
    args = parser.parse_args()
    directory = args.output_dir or (Path(__file__).resolve().parent.parent
                                  / "output" / "delay")
    try:
        rrhs = read_locations(args.rrhs, "rrh_id")
        pools = read_locations(args.pools, "bbu_pool")
        synthetic_count = sum(
            str(row.get("is_synthetic", "")).strip().lower() in {"true", "1", "t"}
            for row in rrhs
        )
        tag = f"k{len(pools)}" + (f"_s{synthetic_count}" if synthetic_count else "")
        targets = [directory / f"rrh_delay_{tag}_summary.csv",
                   directory / f"rrh_pool_{tag}_links.csv"]
        if any(target.resolve() in {args.rrhs.resolve(), args.pools.resolve()} for target in targets):
            raise ValueError("Output paths must not overwrite inputs")
        summaries, links = analyze(rrhs, pools, args.max_rtt_us, args.speed_km_s,
                                  args.route_factor, args.fixed_rtt_us)
        directory.mkdir(parents=True, exist_ok=True)
        write_csv(targets[0], summaries)
        write_csv(targets[1], links)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    served = sum(row["service_status"] == "served" for row in summaries)
    print(f"Fixed pools: {len(pools)} | RRHs: {len(rrhs)}")
    print(f"Served: {served} | Unserved: {len(rrhs) - served}")
    print(f"RTT limit: < {args.max_rtt_us:g} us (round trip)")
    for path in targets:
        print(path)


if __name__ == "__main__":
    main()
