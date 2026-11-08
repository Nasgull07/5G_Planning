#!/usr/bin/env python3
"""
Generate synthetic RRHs strictly inside the KML polygon with metric negative padding
and 200 m minimum separation, formatted for direct compatibility with bbu_initial_assignment_multi.py.
"""
import os
import argparse
import pandas as pd
import numpy as np
import json
import re
import html
import math
from pathlib import Path
from collections import defaultdict
from pyproj import Transformer
from shapely.geometry import Point, Polygon
import xml.etree.ElementTree as ET

# Definición de rutas por defecto
ROOT = Path(__file__).resolve().parent.parent
KML_PATH = ROOT / "data" / "input" / "selected_area.kml"
REAL_CSV = ROOT / "data" / "processed" / "350_CELLS.csv"
SYNTHETIC_CSV = ROOT / "data" / "processed" / "synthetic_rrhs.csv"
COMBINED_CSV = ROOT / "data" / "processed" / "scenario_sites.csv"

FIELDS = ["rrh_id", "site_id", "operator", "technology", "latitude", "longitude", "is_synthetic"]


# ============================================================
# 1. PARSE KML POLYGON
# ============================================================

def load_kml_polygon(kml_path):
    if not os.path.exists(kml_path):
        raise FileNotFoundError(f"KML file not found: {kml_path}")
        
    tree = ET.parse(kml_path)
    root = tree.getroot()
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    
    coord_elements = root.findall('.//kml:Polygon//kml:coordinates', ns)
    if not coord_elements:
        coord_elements = root.findall('.//coordinates', root.tag.split('}')[0].strip('{'))

    for elem in coord_elements:
        text = elem.text.strip()
        coords = []
        for point_str in text.split():
            parts = point_str.split(',')
            if len(parts) >= 2:
                lon, lat = float(parts[0]), float(parts[1])
                coords.append((lon, lat))
        if len(coords) >= 3:
            return coords
            
    raise ValueError("No valid Polygon coordinates found in KML.")


# ============================================================
# 2. LOAD & PARSE REAL RRHs (350_CELLS.csv or cellular_sites.csv)
# ============================================================

def load_real_rrhs(csv_path):
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Real sites CSV not found: {csv_path}")

    df = pd.read_csv(csv_path, dtype=str)
    df.columns = [
        re.sub(r"\s+", "_", html.unescape(str(c)).replace("*", "").strip().lower())
        for c in df.columns
    ]

    candidates = []
    for _, row in df.iterrows():
        lat, lon = None, None
        
        # Extraer coordenadas
        if "coordinates" in row and pd.notna(row["coordinates"]):
            parts = str(row["coordinates"]).split(",")
            if len(parts) >= 2:
                try:
                    lat, lon = float(parts[0].strip()), float(parts[1].strip())
                except ValueError:
                    continue
        elif "latitude" in row and "longitude" in row:
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (ValueError, TypeError):
                continue
                
        if lat is None or lon is None or not (35.0 <= lat <= 44.0 and -10.0 <= lon <= 5.0):
            continue

        # Extraer Site ID
        site_id = "UNKNOWN"
        if "site_id" in row and pd.notna(row["site_id"]):
            try:
                s_json = json.loads(row["site_id"])
                site_id = s_json[0] if s_json else "UNKNOWN"
            except Exception:
                site_id = str(row["site_id"])
        elif "gis_codigo" in row and pd.notna(row["gis_codigo"]):
            site_id = str(row["gis_codigo"])

        # Identificar operador y tecnología relevante (4G / 5G)
        op_cols = [c for c in df.columns if "operator" in c or c in ["código", "codigo"]]
        matched_op, tech_label, tech_priority = "UNKNOWN", "4G", 1
        
        for op_col in op_cols:
            op_val = row.get(op_col, "")
            if pd.isna(op_val) or not str(op_val).strip():
                continue
            
            idx_match = re.search(r"\d+", op_col)
            tech_col = f"technology_op{idx_match.group(0)}" if idx_match else "technology"
            tech_val = row.get(tech_col, "")
            
            if pd.isna(tech_val) or not str(tech_val).strip():
                tech_cols = [c for c in df.columns if "tech" in c]
                for tc in tech_cols:
                    if pd.notna(row.get(tc)):
                        tech_val = row.get(tc)
                        break
                        
            tech_str = str(tech_val).strip().upper()
            if "4G" not in tech_str and "5G" not in tech_str:
                continue
                
            matched_op = str(op_val).strip()
            tech_priority = 2 if "5G" in tech_str else 1
            tech_label = "5G" if "5G" in tech_str else "4G"
            break
            
        if matched_op != "UNKNOWN" or ("technology" in row and ("4G" in str(row["technology"]).upper() or "5G" in str(row["technology"]).upper())):
            candidates.append({
                "site_id": site_id,
                "operator": matched_op,
                "technology": tech_label,
                "tech_priority": tech_priority,
                "latitude": lat,
                "longitude": lon,
                "is_synthetic": False
            })

    if not candidates:
        raise ValueError("No valid 4G/5G real stations found in input CSV.")

    df_cand = pd.DataFrame(candidates)
    df_cand = df_cand.sort_values(
        by=["latitude", "longitude", "tech_priority"],
        ascending=[True, True, False]
    ).drop_duplicates(subset=["latitude", "longitude"], keep="first").reset_index(drop=True)
    
    df_cand["rrh_id"] = [f"RRH_{i+1:03d}" for i in range(len(df_cand))]
    return df_cand[FIELDS]


# ============================================================
# 3. GENERATE SYNTHETIC RRHs (PADDED POLYGON & SPATIAL GRID)
# ============================================================

def generate_synthetic_rrhs(count, kml_coords, real_df, min_distance=200.0, padding_km=-1.5, seed=42, max_attempts=10000):
    if count <= 0:
        return pd.DataFrame(columns=FIELDS)

    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:25831", always_xy=True)
    to_wgs = Transformer.from_crs("EPSG:25831", "EPSG:4326", always_xy=True)

    # 1. Proyectar KML a UTM 31N y aplicar Padding Negativo
    utm_kml_coords = [to_utm.transform(lon, lat) for lon, lat in kml_coords]
    poly_utm = Polygon(utm_kml_coords)

    if padding_km != 0.0:
        buffer_m = padding_km * 1000.0
        poly_utm = poly_utm.buffer(buffer_m)
        print(f"Applied spatial padding: {padding_km:+.2f} km ({buffer_m:+.1f} m) to KML boundary.")

    if poly_utm.is_empty:
        raise ValueError("Polygon padding reduced boundary area to zero. Decrease negative --padding_km.")

    min_x, min_y, max_x, max_y = poly_utm.bounds

    # 2. Construir Grilla Espacial UTM para consulta O(1)
    grid = defaultdict(list)
    cell_size = float(min_distance)

    # Insertar RRHs reales en la grilla espacial
    real_lons = real_df["longitude"].astype(float).values
    real_lats = real_df["latitude"].astype(float).values
    real_x, real_y = to_utm.transform(real_lons, real_lats)

    for rx, ry in zip(real_x, real_y):
        gx, gy = int(rx // cell_size), int(ry // cell_size)
        grid[(gx, gy)].append((rx, ry))

    # 3. Muestreo Aleatorio con Verificación de Distancia
    rng = np.random.RandomState(seed)
    synthetic_rows = []

    for i in range(1, count + 1):
        placed = False
        for _ in range(max_attempts):
            cand_x = rng.uniform(min_x, max_x)
            cand_y = rng.uniform(min_y, max_y)

            pt_utm = Point(cand_x, cand_y)
            if not poly_utm.contains(pt_utm):
                continue

            cgx, cgy = int(cand_x // cell_size), int(cand_y // cell_size)
            too_close = False

            # Consultar 9 celdas adyacentes (3x3)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for ox, oy in grid.get((cgx + dx, cgy + dy), []):
                        if math.hypot(cand_x - ox, cand_y - oy) < cell_size:
                            too_close = True
                            break
                    if too_close:
                        break
                if too_close:
                    break

            if not too_close:
                grid[(cgx, cgy)].append((cand_x, cand_y))
                cand_lon, cand_lat = to_wgs.transform(cand_x, cand_y)
                
                synthetic_rows.append({
                    "rrh_id": f"RRH_SYN_{i:03d}",
                    "site_id": f"SYNTH_SITE_{i:03d}",
                    "operator": "SYNTHETIC",
                    "technology": "5G" if i % 2 == 0 else "4G",
                    "latitude": f"{cand_lat:.6f}",
                    "longitude": f"{cand_lon:.6f}",
                    "is_synthetic": True
                })
                placed = True
                break

        if not placed:
            raise RuntimeError(
                f"Placed {len(synthetic_rows)} of {count} requested synthetic points. "
                "Try reducing --count or --min-distance, or adjusting --padding-km."
            )

    return pd.DataFrame(synthetic_rows)


# ============================================================
# 4. MAIN CLI LOGIC
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Generate synthetic RRHs with padding and 200m separation.")
    parser.add_argument("--count", type=int, default=50, help="Number of synthetic RRHs to generate (default: 50)")
    parser.add_argument("--min-distance", type=float, default=200.0, help="Minimum separation between RRHs in meters (default: 200.0)")
    parser.add_argument("--padding-km", type=float, default=-1.5, help="Boundary padding in km. Negative contracts inwards away from coast/sea (default: -1.5)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility (default: 42)")
    parser.add_argument("--kml-path", type=str, default=str(KML_PATH), help="Path to boundary KML file")
    parser.add_argument("--real-csv", type=str, default=str(REAL_CSV), help="Path to input real cells CSV")
    parser.add_argument("--synthetic-csv", type=str, default=str(SYNTHETIC_CSV), help="Output path for synthetic-only CSV")
    parser.add_argument("--combined-csv", type=str, default=str(COMBINED_CSV), help="Output path for combined scenario CSV")

    args = parser.parse_args()

    kml_coords = load_kml_polygon(args.kml_path)
    real_df = load_real_rrhs(args.real_csv)
    
    syn_df = generate_synthetic_rrhs(
        count=args.count,
        kml_coords=kml_coords,
        real_df=real_df,
        min_distance=args.min_distance,
        padding_km=args.padding_km,
        seed=args.seed
    )

    combined_df = pd.concat([real_df, syn_df], ignore_index=True)

    os.makedirs(os.path.dirname(args.synthetic_csv), exist_ok=True)
    os.makedirs(os.path.dirname(args.combined_csv), exist_ok=True)

    syn_df.to_csv(args.synthetic_csv, index=False)
    combined_df.to_csv(args.combined_csv, index=False)

    print("\n============================================================")
    print(" SYNTHETIC RRH PREPROCESSING COMPLETED")
    print("============================================================")
    print(f"Real physical RRHs loaded: {len(real_df)}")
    print(f"Synthetic RRHs generated:   {len(syn_df)}")
    print(f"Total scenario RRHs:        {len(combined_df)}")
    print(f"Polygon Padding:            {args.padding_km:+.2f} km")
    print(f"Minimum Separation:        {args.min_distance:.1f} m")
    print(f"Synthetic-only CSV:         {args.synthetic_csv}")
    print(f"Combined Scenario CSV:      {args.combined_csv}")


if __name__ == "__main__":
    main()