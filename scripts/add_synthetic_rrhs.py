#!/usr/bin/env python3
"""
Generate extra synthetic RRH locations for C-RAN robustness tests.

Rules:
- Sample uniformly in the KML scenario's rectangular bounding box.
- Keep each synthetic point at least MIN_DISTANCE_METERS from every real
  site coordinate and every other generated synthetic point.
- Keep the source cellular_sites.csv unchanged.
- Label synthetic records clearly and save them in separate/combined CSVs.

Uses only the Python standard library.

Example (from the repository root):
    python scripts/add_synthetic_rrhs.py --count 50 --min-distance 200 --seed 42
    python scripts/add_synthetic_rrhs.py --count 0
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
KML_FILE = PROJECT_DIR / "data" / "input" / "selected_area.kml"
REAL_SITES_CSV = PROJECT_DIR / "data" / "processed" / "cellular_sites.csv"
SYNTHETIC_CSV = PROJECT_DIR / "data" / "processed" / "synthetic_rrhs.csv"
SCENARIO_CSV = PROJECT_DIR / "data" / "processed" / "scenario_sites.csv"

OUTPUT_FIELDS = [
    "rrh_id", "latitude", "longitude", "source_type",
    "operator", "technology", "is_synthetic",
]
EARTH_RADIUS_M = 6_371_000.0


def read_kml_bounds(path: Path) -> tuple[float, float, float, float]:
    """Return (min_lat, max_lat, min_lon, max_lon) from KML coordinates."""
    if not path.exists():
        raise FileNotFoundError(f"KML boundary not found: {path}")
    root = ET.parse(path).getroot()
    points = []
    for element in root.findall(".//{*}coordinates"):
        if not element.text:
            continue
        for token in element.text.split():
            parts = token.split(",")
            if len(parts) >= 2:
                points.append((float(parts[0]), float(parts[1])))
    if not points:
        raise ValueError(f"No coordinate vertices found in KML: {path}")
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return min(lats), max(lats), min(lons), max(lons)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two latitude/longitude points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, a)))


def read_real_sites(path: Path) -> list[dict]:
    """Read all valid real Infoantenas site coordinates."""
    if not path.exists():
        raise FileNotFoundError(
            f"Real sites CSV not found: {path}. Run scripts/download_cell_sites.py first."
        )
    rows = []
    seen = set()
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or not {"latitude", "longitude"}.issubset(reader.fieldnames):
            raise ValueError("cellular_sites.csv must contain latitude and longitude columns.")
        for row in reader:
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (TypeError, ValueError):
                continue
            key = (round(lat, 8), round(lon, 8))
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "rrh_id": row.get("Gis_Codigo") or row.get("feature_id") or f"REAL_SITE_{len(rows)+1:05d}",
                "latitude": f"{lat:.8f}",
                "longitude": f"{lon:.8f}",
                "source_type": "REAL_INFOANTENAS",
                "operator": row.get("Código", ""),
                "technology": "NOT_INFERRED_BY_GENERATOR",
                "is_synthetic": "False",
            })
    if not rows:
        raise ValueError("No valid real site coordinates were found.")
    return rows


def grid_key(lat: float, lon: float, cell_m: float, reference_lat: float) -> tuple[int, int]:
    """Map a coordinate to a local metre grid cell for efficient neighbour checks."""
    y_m = math.radians(lat) * EARTH_RADIUS_M
    x_m = math.radians(lon) * EARTH_RADIUS_M * math.cos(math.radians(reference_lat))
    return math.floor(x_m / cell_m), math.floor(y_m / cell_m)


def generate_synthetic(
    count: int,
    bounds: tuple[float, float, float, float],
    real_rows: list[dict],
    min_distance_m: float,
    seed: int,
    max_attempts_per_point: int,
) -> list[dict]:
    min_lat, max_lat, min_lon, max_lon = bounds
    rng = random.Random(seed)
    reference_lat = (min_lat + max_lat) / 2
    # A grid cell equal to the required separation means only adjacent cells
    # need checking. We still verify true geodesic distance using haversine.
    cell_size = min_distance_m
    occupied = defaultdict(list)

    for row in real_rows:
        lat, lon = float(row["latitude"]), float(row["longitude"])
        occupied[grid_key(lat, lon, cell_size, reference_lat)].append((lat, lon))

    generated = []
    for index in range(1, count + 1):
        accepted = None
        for _ in range(max_attempts_per_point):
            lat = rng.uniform(min_lat, max_lat)
            lon = rng.uniform(min_lon, max_lon)
            gx, gy = grid_key(lat, lon, cell_size, reference_lat)

            too_close = False
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for other_lat, other_lon in occupied.get((gx + dx, gy + dy), []):
                        if haversine_m(lat, lon, other_lat, other_lon) < min_distance_m:
                            too_close = True
                            break
                    if too_close:
                        break
                if too_close:
                    break

            if not too_close:
                accepted = (lat, lon)
                occupied[(gx, gy)].append(accepted)
                break

        if accepted is None:
            raise RuntimeError(
                f"Could place only {len(generated)} of {count} requested synthetic RRHs. "
                f"Try a smaller --count or smaller --min-distance. "
                f"Each point was allowed {max_attempts_per_point} attempts."
            )

        lat, lon = accepted
        generated.append({
            "rrh_id": f"SYN_RRH_{index:05d}",
            "latitude": f"{lat:.8f}",
            "longitude": f"{lon:.8f}",
            "source_type": "SYNTHETIC",
            "operator": "SYNTHETIC",
            "technology": "ASSUMED_FOR_SIMULATION",
            "is_synthetic": "True",
        })
    return generated


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate synthetic RRHs uniformly in the scenario bounding box."
    )
    parser.add_argument("--count", type=int, default=50,
                        help="number of synthetic RRHs to generate (default: 50)")
    parser.add_argument("--min-distance", type=float, default=200.0,
                        help="minimum separation in metres from real sites and other synthetic RRHs (default: 200)")
    parser.add_argument("--seed", type=int, default=42,
                        help="random seed for repeatable experiments (default: 42)")
    parser.add_argument("--max-attempts-per-point", type=int, default=10000,
                        help="maximum random placement attempts per requested point")
    args = parser.parse_args()

    if args.count < 0:
        parser.error("--count must be zero or greater")
    if args.min_distance <= 0:
        parser.error("--min-distance must be greater than zero")
    if args.max_attempts_per_point <= 0:
        parser.error("--max-attempts-per-point must be greater than zero")

    bounds = read_kml_bounds(KML_FILE)
    real_rows = read_real_sites(REAL_SITES_CSV)
    synthetic_rows = generate_synthetic(
        args.count, bounds, real_rows, args.min_distance,
        args.seed, args.max_attempts_per_point
    )

    # Do not modify the original Infoantenas download.
    write_csv(SYNTHETIC_CSV, synthetic_rows)
    write_csv(SCENARIO_CSV, real_rows + synthetic_rows)

    print("Synthetic RRH preprocessing completed successfully.")
    print(f"Unique real site coordinates: {len(real_rows)}")
    print(f"Synthetic RRHs generated:     {len(synthetic_rows)}")
    print(f"Minimum separation:           {args.min_distance:.1f} m")
    print(f"Random seed:                  {args.seed}")
    print(f"Synthetic-only CSV:           {SYNTHETIC_CSV}")
    print(f"Combined scenario CSV:        {SCENARIO_CSV}")
    print("\nNote: random points are sampled in the rectangular KML bounding box.")
    print("The script does not clip them to the irregular KML polygon.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
