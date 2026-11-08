#!/usr/bin/env python3
"""Generate synthetic RRHs strictly inside the KML polygon, with 200 m spacing."""
import argparse
import csv
import math
import random
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KML = ROOT / "data" / "input" / "selected_area.kml"
REAL = ROOT / "data" / "processed" / "350_CELLS.csv"
SYNTHETIC = ROOT / "data" / "processed" / "synthetic_rrhs.csv"
COMBINED = ROOT / "data" / "processed" / "scenario_sites.csv"
FIELDS = ["rrh_id", "latitude", "longitude", "source_type", "operator", "technology", "is_synthetic"]
EARTH_M = 6_371_000.0


def parse_ring(text):
    result = []
    for token in text.split():
        p = token.split(",")
        if len(p) >= 2:
            result.append((float(p[0]), float(p[1])))  # lon, lat
    return result


def load_kml(path):
    if not path.exists():
        raise FileNotFoundError(f"KML boundary not found: {path}")
    root = ET.parse(path).getroot()
    polygons = []
    for node in root.findall(".//{*}Polygon"):
        outer_node = node.find("./{*}outerBoundaryIs/{*}LinearRing/{*}coordinates")
        if outer_node is None or not outer_node.text:
            continue
        outer = parse_ring(outer_node.text)
        if len(outer) < 3:
            continue
        holes = []
        for inner in node.findall("./{*}innerBoundaryIs/{*}LinearRing/{*}coordinates"):
            if inner.text:
                ring = parse_ring(inner.text)
                if len(ring) >= 3:
                    holes.append(ring)
        polygons.append((outer, holes))
    if not polygons:
        raise ValueError("No valid Polygon geometry found in KML.")
    points = [p for outer, _ in polygons for p in outer]
    lons, lats = zip(*points)
    return polygons, (min(lats), max(lats), min(lons), max(lons))


def on_segment(x, y, a, b, eps=1e-12):
    x1, y1 = a
    x2, y2 = b
    cross = (x-x1)*(y2-y1) - (y-y1)*(x2-x1)
    if abs(cross) > eps:
        return False
    squared_length = (x2-x1)**2 + (y2-y1)**2
    if squared_length <= eps:
        return False
    dot = (x-x1)*(x2-x1) + (y-y1)*(y2-y1)
    return -eps <= dot <= squared_length + eps


def in_ring(lon, lat, ring):
    inside = False
    for i, a in enumerate(ring):
        b = ring[(i+1) % len(ring)]
        if on_segment(lon, lat, a, b):
            return False  # strict interior: boundary is excluded
        x1, y1 = a
        x2, y2 = b
        if (y1 > lat) != (y2 > lat):
            cross_x = (x2-x1)*(lat-y1)/(y2-y1) + x1
            if lon < cross_x:
                inside = not inside
    return inside


def inside_kml(lon, lat, polygons):
    for outer, holes in polygons:
        if in_ring(lon, lat, outer) and not any(in_ring(lon, lat, h) for h in holes):
            return True
    return False


def distance_m(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat-a_lat)
    dl = math.radians(b_lon-a_lon)
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*EARTH_M*math.asin(math.sqrt(min(1.0, h)))


def read_real(path):
    if not path.exists():
        raise FileNotFoundError(f"350-cell sample CSV not found: {path}")
    rows, seen = [], set()
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not {"Site ID", "coordinates"}.issubset(reader.fieldnames):
            raise ValueError("350_CELLS.csv must contain Site ID and coordinates.")
        for row in reader:
            try:
                lat, lon = (float(value.strip()) for value in row["coordinates"].split(",", 1))
            except (AttributeError, TypeError, ValueError):
                continue
            key = (round(lat, 8), round(lon, 8))
            if key in seen:
                continue
            seen.add(key)
            operators = [
                row.get(f"operator {i}", "").strip()
                for i in range(1, 5)
                if row.get(f"operator {i}", "").strip()
            ]
            technologies = list(dict.fromkeys(
                row.get(f"technology_op{i}", "").strip()
                for i in range(1, 5)
                if row.get(f"technology_op{i}", "").strip()
            ))
            rows.append({
                "rrh_id": row.get("Site ID") or f"REAL_SITE_{len(rows)+1:05d}",
                "latitude": f"{lat:.8f}", "longitude": f"{lon:.8f}",
                "source_type": "REAL_350_CELLS", "operator": "; ".join(operators),
                "technology": "; ".join(technologies) or "NOT_INFERRED_BY_GENERATOR",
                "is_synthetic": "False",
            })
    if not rows:
        raise ValueError("No valid coordinates found in 350_CELLS.csv.")
    return rows


def grid_key(lat, lon, cell, ref_lat):
    x = math.radians(lon)*EARTH_M*math.cos(math.radians(ref_lat))
    y = math.radians(lat)*EARTH_M
    return math.floor(x/cell), math.floor(y/cell)


def generate(count, polygons, bounds, real_rows, min_dist, seed, max_attempts):
    min_lat, max_lat, min_lon, max_lon = bounds
    ref_lat = (min_lat+max_lat)/2
    rng = random.Random(seed)
    occupied = defaultdict(list)
    for row in real_rows:
        lat, lon = float(row["latitude"]), float(row["longitude"])
        occupied[grid_key(lat, lon, min_dist, ref_lat)].append((lat, lon))

    output = []
    for i in range(1, count+1):
        chosen = None
        for _ in range(max_attempts):
            lat, lon = rng.uniform(min_lat, max_lat), rng.uniform(min_lon, max_lon)
            if not inside_kml(lon, lat, polygons):
                continue
            gx, gy = grid_key(lat, lon, min_dist, ref_lat)
            close = False
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for other_lat, other_lon in occupied.get((gx+dx, gy+dy), []):
                        if distance_m(lat, lon, other_lat, other_lon) < min_dist:
                            close = True
                            break
                    if close:
                        break
                if close:
                    break
            if not close:
                chosen = (lat, lon)
                occupied[(gx, gy)].append(chosen)
                break
        if chosen is None:
            raise RuntimeError(
                f"Placed {len(output)} of {count} requested points. "
                "Try reducing --count or --min-distance, or increasing "
                "--max-attempts-per-point."
            )
        lat, lon = chosen
        output.append({
            "rrh_id": f"SYN_RRH_{i:05d}", "latitude": f"{lat:.8f}",
            "longitude": f"{lon:.8f}", "source_type": "SYNTHETIC",
            "operator": "SYNTHETIC", "technology": "ASSUMED_FOR_SIMULATION",
            "is_synthetic": "True",
        })
    return output


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic RRHs inside the KML polygon.")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--min-distance", type=float, default=200.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-attempts-per-point", type=int, default=10000)
    args = parser.parse_args()
    if args.count < 0 or args.min_distance <= 0 or args.max_attempts_per_point <= 0:
        parser.error("count must be >= 0; distance and max attempts must be > 0")

    polygons, bounds = load_kml(KML)
    real = read_real(REAL)
    synthetic = generate(args.count, polygons, bounds, real, args.min_distance,
                         args.seed, args.max_attempts_per_point)
    write_csv(SYNTHETIC, synthetic)
    write_csv(COMBINED, real + synthetic)
    print("Synthetic RRH preprocessing completed successfully.")
    print(f"Unique real site coordinates: {len(real)}")
    print(f"Synthetic RRHs generated:     {len(synthetic)}")
    print(f"Minimum separation:           {args.min_distance:.1f} m")
    print(f"Random seed:                  {args.seed}")
    print("Location rule:                strictly inside KML polygon")
    print(f"Synthetic-only CSV:           {SYNTHETIC}")
    print(f"Combined scenario CSV:        {COMBINED}")


if __name__ == "__main__":
    main()
