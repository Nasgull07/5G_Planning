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
from scipy.spatial import ConvexHull
import xml.etree.ElementTree as ET


# ============================================================
# HELPER: POINT-IN-POLYGON (RAY-CASTING ALGORITHM)
# ============================================================

def point_in_polygon(x, y, poly):
    """
    Determina si un punto (x, y) está dentro de un polígono 2D arbitrario.
    poly: lista de tuplas [(lon1, lat1), (lon2, lat2), ...]
    """
    n = len(poly)
    inside = False
    p1x, p1y = poly[0]
    for i in range(n + 1):
        p2x, p2y = poly[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside


def parse_kml_polygon(kml_path):
    """Extrae las coordenadas del polígono delimitador desde un archivo KML."""
    tree = ET.parse(kml_path)
    root = tree.getroot()
    
    # Namespaces KML
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
    return None


# ============================================================
# 1. LOAD REAL RRHs
# ============================================================

def load_rrhs_shared(csv_path):
    df = pd.read_csv(csv_path, dtype=str)

    df.columns = [
        re.sub(r"\s+", "_",
               html.unescape(str(c)).replace("*", "").strip().lower())
        for c in df.columns
    ]

    candidates = []

    for _, row in df.iterrows():
        coords_str = str(row.get("coordinates", "")).strip()
        if not coords_str or "," not in coords_str:
            continue

        parts = coords_str.split(",")
        try:
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
        except ValueError:
            continue

        if not (35.0 <= lat <= 44.0 and -10.0 <= lon <= 5.0):
            continue

        try:
            site_ids = json.loads(row["site_id"])
            site_id = site_ids[0] if site_ids else "UNKNOWN"
        except (ValueError, TypeError, KeyError):
            site_id = str(row.get("site_id", "UNKNOWN"))

        op_cols = [c for c in df.columns if "operator" in c]

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

            tech_priority = 2 if "5G" in tech_str else 1
            tech_label = "5G" if "5G" in tech_str else "4G"

            candidates.append({
                "site_id": site_id,
                "operator": str(op_val).strip(),
                "technology": tech_label,
                "tech_priority": tech_priority,
                "latitude": lat,
                "longitude": lon,
                "is_synthetic": False
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

    result["rrh_id"] = [f"RRH_{i+1:03d}" for i in range(len(result))]

    return result[["rrh_id", "site_id", "operator", "technology", "latitude", "longitude", "is_synthetic"]]


# ============================================================
# 2. INJECT SYNTHETIC RRHs STRICTLY INSIDE POLYGON
# ============================================================

def inject_synthetic_rrhs(rrhs_real, n_synthetic, kml_path=None, seed=42):
    if n_synthetic <= 0:
        return rrhs_real

    # Determinar el polígono límite (KML o Convex Hull de celdas reales)
    polygon = None
    if kml_path and os.path.exists(kml_path):
        polygon = parse_kml_polygon(kml_path)
        if polygon:
            print(f"Using polygon boundary loaded from KML: {kml_path}")

    if polygon is None:
        points = rrhs_real[['longitude', 'latitude']].values
        hull = ConvexHull(points)
        polygon = [tuple(points[v]) for v in hull.vertices]
        print("Using Convex Hull boundary polygon derived from real RRHs")

    poly_arr = np.array(polygon)
    min_lon, min_lat = poly_arr[:, 0].min(), poly_arr[:, 1].min()
    max_lon, max_lat = poly_arr[:, 0].max(), poly_arr[:, 1].max()

    rng = np.random.RandomState(seed)
    synthetic_list = []

    # Generación por muestreo de rechazo dentro del polígono exacto
    while len(synthetic_list) < n_synthetic:
        batch_lons = rng.uniform(min_lon, max_lon, n_synthetic * 3)
        batch_lats = rng.uniform(min_lat, max_lat, n_synthetic * 3)

        for lon, lat in zip(batch_lons, batch_lats):
            if point_in_polygon(lon, lat, polygon):
                synthetic_list.append({
                    "rrh_id": f"RRH_SYN_{len(synthetic_list)+1:03d}",
                    "site_id": f"SYNTH_SITE_{len(synthetic_list)+1:03d}",
                    "operator": "SYNTHETIC",
                    "technology": "5G" if len(synthetic_list) % 2 == 0 else "4G",
                    "latitude": lat,
                    "longitude": lon,
                    "is_synthetic": True
                })
                if len(synthetic_list) == n_synthetic:
                    break

    df_syn = pd.DataFrame(synthetic_list)
    combined = pd.concat([rrhs_real, df_syn], ignore_index=True)
    return combined


# ============================================================
# 3. PROJECT COORDINATES
# ============================================================

def project_coordinates(rrhs):
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:25831", always_xy=True)
    x, y = transformer.transform(rrhs["longitude"].values, rrhs["latitude"].values)
    return np.column_stack((x, y))


# ============================================================
# 4. FARTHEST-FIRST K-CENTER
# ============================================================

def farthest_first_k_center(X, K):
    n_candidates = len(X)
    if K < 1 or K > n_candidates:
        raise ValueError(f"K must be between 1 and {n_candidates}")

    centroid = X.mean(axis=0)
    first = np.argmax(np.linalg.norm(X - centroid, axis=1))
    selected = [first]

    nearest_distances = np.linalg.norm(X - X[first], axis=1)

    for _ in range(1, K):
        next_center = np.argmax(nearest_distances)
        selected.append(next_center)
        new_distances = np.linalg.norm(X - X[next_center], axis=1)
        nearest_distances = np.minimum(nearest_distances, new_distances)

    return selected


# ============================================================
# 5. ASSIGN RRHs TO POOLS
# ============================================================

def assign_rrhs_to_pools(rrhs, X, selected_indices):
    centers = X[selected_indices]
    distance_matrix = np.linalg.norm(X[:, np.newaxis, :] - centers[np.newaxis, :, :], axis=2)

    assigned_pool = np.argmin(distance_matrix, axis=1)
    assigned_distance = distance_matrix[np.arange(len(X)), assigned_pool]

    result = rrhs.copy()
    result["bbu_pool"] = [f"BBU_{i+1:02d}" for i in assigned_pool]
    result["distance_to_bbu_km"] = assigned_distance / 1000

    pools = rrhs.iloc[selected_indices].copy().reset_index(drop=True)
    pools["bbu_pool"] = [f"BBU_{i+1:02d}" for i in range(len(pools))]
    pools["num_assigned_rrhs"] = [np.sum(assigned_pool == i) for i in range(len(selected_indices))]

    return result, pools


# ============================================================
# 6. MAP GENERATION
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
        <b>Assigned BBU:</b> {pool_id}<br>
        <b>Distance:</b> {row.get('distance_to_bbu_km', 'N/A')} km
        """

        if is_syn:
            folium.CircleMarker(
                location=[row["latitude"], row["longitude"]],
                radius=6, color="red", fill=True, fill_color=color, fill_opacity=0.6,
                weight=2, dash_array="4, 4", popup=folium.Popup(popup_text, max_width=300),
                tooltip=f"SYNTHETIC RRH {row.get('rrh_id')} -> {pool_id}"
            ).add_to(syn_rrh_layer)
        else:
            folium.CircleMarker(
                location=[row["latitude"], row["longitude"]],
                radius=5, color=color, fill=True, fill_color=color, fill_opacity=0.85,
                weight=1, popup=folium.Popup(popup_text, max_width=300),
                tooltip=f"REAL RRH {row.get('rrh_id')} [{row.get('technology')}] -> {pool_id}"
            ).add_to(real_rrh_layer)

    real_rrh_layer.add_to(m)
    syn_rrh_layer.add_to(m)

    bbu_layer = folium.FeatureGroup(name="BBU Pools", show=True)
    for i, row in bbus.iterrows():
        pool_id = row.get("bbu_pool", i + 1)
        is_syn_pool = row.get("is_synthetic", False)

        popup_text = f"""
        <b>BBU Pool:</b> {pool_id}<br>
        <b>Location Site:</b> {row.get('site_id', 'N/A')} ({'SYNTHETIC' if is_syn_pool else 'REAL'})<br>
        <b>Assigned RRHs:</b> {row.get('num_assigned_rrhs', 'N/A')}
        """

        folium.Marker(
            location=[row["latitude"], row["longitude"]],
            popup=folium.Popup(popup_text, max_width=300),
            tooltip=f"BBU Pool {pool_id} ({'SYNTH' if is_syn_pool else 'REAL'})",
            icon=folium.Icon(color="orange" if is_syn_pool else "black", icon="server", prefix="fa")
        ).add_to(bbu_layer)

    bbu_layer.add_to(m)

    connection_layer = folium.FeatureGroup(name="RRH - BBU connections", show=True)
    bbu_coordinates = {
        row.get("bbu_pool", i + 1): [row["latitude"], row["longitude"]]
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


# ============================================================
# 7. MAIN EXECUTION
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="BBU Pool Optimizer with Polygon-Constrained Synthetic RRHs Injection")
    parser.add_argument("-k", "--k_pools", type=int, required=True, help="Número de BBU pools deseados (K)")
    parser.add_argument("-s", "--n_synthetic", type=int, default=0, help="Número de RRHs sintéticas a inyectar aleatoriamente")
    parser.add_argument("--kml_path", type=str, default=None, help="Ruta al archivo KML con el área delimitada")
    parser.add_argument("--seed", type=int, default=42, help="Semilla aleatoria (default: 42)")
    parser.add_argument("--input_path", type=str, default="../data/processed/350_CELLS.csv", help="Ruta al CSV de celdas reales")
    parser.add_argument("--data_output_dir", type=str, default="../data/processed/", help="Directorio de salida CSV")
    parser.add_argument("--html_output_dir", type=str, default="../output/", help="Directorio de salida HTML")

    args = parser.parse_args()

    os.makedirs(args.data_output_dir, exist_ok=True)
    os.makedirs(args.html_output_dir, exist_ok=True)

    rrhs_real = load_rrhs_shared(args.input_path)
    rrhs = inject_synthetic_rrhs(rrhs_real, args.n_synthetic, kml_path=args.kml_path, seed=args.seed)

    print("\n============================================================")
    print(" MODE: SHARED INFRASTRUCTURE (STRICT POLYGON CONTAINMENT)")
    print("============================================================")
    print(f"Real physical RRHs loaded: {len(rrhs_real)}")
    print(f"Synthetic RRHs injected:   {args.n_synthetic}")
    print(f"TOTAL RRHs in scenario:    {len(rrhs)}")

    X = project_coordinates(rrhs)
    selected_indices = farthest_first_k_center(X, args.k_pools)
    assignments, pools = assign_rrhs_to_pools(rrhs, X, selected_indices)

    print("\nBBU POOL LOCATIONS:")
    print(pools[["bbu_pool", "site_id", "is_synthetic", "latitude", "longitude", "num_assigned_rrhs"]].to_string(index=False))

    print("\nPERFORMANCE METRICS:")
    print(f"Maximum distance: {assignments['distance_to_bbu_km'].max():.3f} km")
    print(f"Average distance: {assignments['distance_to_bbu_km'].mean():.3f} km")

    tag = f"k{args.k_pools}_s{args.n_synthetic}_shared" if args.n_synthetic > 0 else f"k{args.k_pools}_shared"
    assignments_file = os.path.join(args.data_output_dir, f"rrh_bbu_{tag}_assignments.csv")
    pools_file = os.path.join(args.data_output_dir, f"bbu_pool_{tag}_locations.csv")
    map_filename = os.path.join(args.html_output_dir, f"rrh_bbu_{tag}_assignments_map.html")

    assignments.to_csv(assignments_file, index=False)
    pools.to_csv(pools_file, index=False)
    generate_map(assignments, pools, map_filename)

    print("\nRESULTS EXPORTED SUCCESSFULLY:")
    print(f" - {assignments_file}")
    print(f" - {pools_file}")
    print(f" - {map_filename}")


if __name__ == "__main__":
    main()