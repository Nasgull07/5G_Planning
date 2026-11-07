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

# ============================================================
# OPERATOR ALIASES AND MAPPING
# ============================================================

OPERATOR_ALIASES = {
    "TELEFONICA MOVILES ESPAÑA, S.A.U.": "movistar",
    "TELEFONICA MOVILES ESPAÑA, S.A.": "movistar",
    "ORANGE ESPAGNE, S.A.": "orange",
    "ORANGE ESPAGNE, S.A.U.": "orange",
    "VODAFONE ESPAÑA, S.A.U.": "vodafone",
    "VODAFONE ESPAÑA, S.A.": "vodafone",
    "DIGI SPAIN TELECOM, S.L.": "digi",
    "DS MOBILE NETWORKS, S.L.": "digimobil"
}

# Reverse map to support short aliases (e.g., 'vodafone')
ALIAS_TO_FULL_NAME = {v: k for k, v in OPERATOR_ALIASES.items()}


def normalize_operator_string(name):
    """
    Normaliza el nombre del operador eliminando caracteres especiales,
    espacios extra y sufijos societarios (S.A.U., S.A., S.L.).
    """
    cleaned = str(name).strip().upper()
    cleaned = re.sub(r",?\s*S\.?A\.?U?\.?", "", cleaned)
    cleaned = re.sub(r",?\s*S\.?L\.?", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def resolve_operator(operator_input):
    """
    Resuelve la entrada del operador (alias o nombre completo)
    y devuelve tanto el nombre original/oficial como el alias.
    """
    input_clean = operator_input.strip()

    # Si coincide directamente con un alias corto (ej: 'vodafone')
    if input_clean.lower() in ALIAS_TO_FULL_NAME:
        full_name = ALIAS_TO_FULL_NAME[input_clean.lower()]
        alias = input_clean.lower()
        return full_name, alias

    # Si es el nombre oficial largo
    normalized_input = normalize_operator_string(input_clean)
    
    for official_name, alias in OPERATOR_ALIASES.items():
        if normalize_operator_string(official_name) == normalized_input:
            return official_name, alias

    # Fallback si no está registrado en el diccionario
    alias = re.sub(r"[^a-z0-9]+", "_", input_clean.lower()).strip("_")
    return input_clean, alias


# ============================================================
# 1. LOAD AND FILTER REAL RRHs
# ============================================================

def load_rrhs(csv_path, selected_operator):

    df = pd.read_csv(csv_path, dtype=str)

    # Normalizar nombres de columnas
    df.columns = [
        re.sub(r"\s+", "_",
               html.unescape(str(c)).replace("*", "").strip().lower())
        for c in df.columns
    ]

    target_operator_norm = normalize_operator_string(selected_operator)
    rrhs = []

    for _, row in df.iterrows():
        matched_technology = None
        matched_operator = None

        # Identificar todas las columnas relativas a operadores
        op_cols = [c for c in df.columns if "operator" in c]

        for op_col in op_cols:
            val = row.get(op_col, "")
            if pd.isna(val) or not str(val).strip():
                continue

            # Comparar operadores normalizados (ignora S.A. vs S.A.U.)
            if normalize_operator_string(val) == target_operator_norm:
                matched_operator = val

                # Extraer el índice numérico de la columna del operador si existe (ej. operator_2 -> 2)
                idx_match = re.search(r"\d+", op_col)
                tech_col = f"technology_op{idx_match.group(0)}" if idx_match else "technology"

                # Intentar leer la columna de tecnología correspondiente o cualquier columna de tecnología
                tech_val = row.get(tech_col, "")
                if pd.isna(tech_val) or not str(tech_val).strip():
                    # Fallback: buscar en cualquier columna que contenga 'tech' o 'technology'
                    tech_cols = [c for c in df.columns if "tech" in c]
                    for tc in tech_cols:
                        if pd.notna(row.get(tc)):
                            tech_val = row.get(tc)
                            break

                matched_technology = str(tech_val).strip().upper()
                break

        if not matched_operator:
            continue

        # Validar si cumple con tecnologías 4G o 5G
        if "4G" not in matched_technology and "5G" not in matched_technology:
            continue

        # Parsear coordenadas
        coordinates = str(row["coordinates"]).split(",")
        lat = float(coordinates[0].strip())
        lon = float(coordinates[1].strip())

        # Parsear Site ID
        try:
            site_ids = json.loads(row["site_id"])
            site_id = site_ids[0] if site_ids else "UNKNOWN"
        except (ValueError, TypeError, KeyError):
            site_id = str(row.get("site_id", "UNKNOWN"))

        rrhs.append({
            "rrh_id": f"RRH_{len(rrhs)+1:03d}",
            "site_id": site_id,
            "operator": matched_operator,
            "technology": matched_technology,
            "latitude": lat,
            "longitude": lon
        })

    result = pd.DataFrame(rrhs)

    if result.empty:
        raise ValueError(
            f"No 4G/5G stations found for operator: {selected_operator}"
        )

    # Eliminar posiciones geográficas duplicadas
    result = result.drop_duplicates(
        subset=["latitude", "longitude"]
    ).reset_index(drop=True)

    # Regenerar IDs de RRH tras el filtrado
    result["rrh_id"] = [
        f"RRH_{i+1:03d}" for i in range(len(result))
    ]

    return result

# ============================================================
# 2. CONVERT GEOGRAPHIC COORDINATES TO METERS
# ============================================================

def project_coordinates(rrhs):

    # EPSG:25831 = UTM 31N (suitable for Barcelona)
    transformer = Transformer.from_crs(
        "EPSG:4326",
        "EPSG:25831",
        always_xy=True
    )

    x, y = transformer.transform(
        rrhs["longitude"].values,
        rrhs["latitude"].values
    )

    return np.column_stack((x, y))


# ============================================================
# 3. FARTHEST-FIRST K-CENTER
# ============================================================

def farthest_first_k_center(X, K):

    n = len(X)

    _, candidate_idx = np.unique(
        X, axis=0, return_index=True
    )

    candidates = X[candidate_idx]
    n_candidates = len(candidates)

    if K < 1 or K > n_candidates:
        raise ValueError(
            f"K must be between 1 and {n_candidates}"
        )

    centroid = candidates.mean(axis=0)

    first = np.argmax(
        np.linalg.norm(candidates - centroid, axis=1)
    )

    selected = [first]

    nearest_distances = np.linalg.norm(
        candidates - candidates[first], axis=1
    )

    for _ in range(1, K):

        next_center = np.argmax(nearest_distances)

        selected.append(next_center)

        new_distances = np.linalg.norm(
            candidates - candidates[next_center], axis=1
        )

        nearest_distances = np.minimum(
            nearest_distances, new_distances
        )

    return candidate_idx[selected]


# ============================================================
# 4. ASSIGN EACH RRH TO ITS NEAREST BBU POOL
# ============================================================

def assign_rrhs_to_pools(rrhs, X, selected_indices):

    centers = X[selected_indices]

    distance_matrix = np.linalg.norm(
        X[:, np.newaxis, :] - centers[np.newaxis, :, :],
        axis=2
    )

    assigned_pool = np.argmin(distance_matrix, axis=1)

    assigned_distance = distance_matrix[
        np.arange(len(X)), assigned_pool
    ]

    result = rrhs.copy()

    result["bbu_pool"] = [
        f"BBU_{i+1:02d}" for i in assigned_pool
    ]

    result["distance_to_bbu_km"] = assigned_distance / 1000

    pools = rrhs.iloc[selected_indices].copy().reset_index(drop=True)

    pools["bbu_pool"] = [
        f"BBU_{i+1:02d}" for i in range(len(pools))
    ]

    pools["num_assigned_rrhs"] = [
        np.sum(assigned_pool == i)
        for i in range(len(selected_indices))
    ]

    return result, pools


# ============================================================
# 5. MAP GENERATION
# ============================================================

def generate_map(rrhs, bbus, output_html_path):
    pool_ids = sorted(
        rrhs["bbu_pool"].dropna().unique(),
        key=lambda x: str(x)
    )

    cmap = plt.get_cmap("tab10", max(len(pool_ids), 1))

    pool_colors = {
        pool_id: to_hex(cmap(i))
        for i, pool_id in enumerate(pool_ids)
    }

    center_lat = rrhs["latitude"].mean()
    center_lon = rrhs["longitude"].mean()

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=11,
        tiles=None
    )

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Esri Street Map",
        overlay=False,
        control=True
    ).add_to(m)

    # Marcadores RRH
    rrh_layer = folium.FeatureGroup(
        name="RRHs (colored by assigned BBU pool)",
        show=True
    )

    for _, row in rrhs.iterrows():
        pool_id = row["bbu_pool"]
        color = pool_colors.get(pool_id, "gray")

        popup_text = f"""
        <b>RRH:</b> {row.get('rrh_id', 'N/A')}<br>
        <b>Site ID:</b> {row.get('site_id', 'N/A')}<br>
        <b>Assigned BBU:</b> {pool_id}<br>
        <b>Distance:</b> {row.get('distance_to_bbu_km', 'N/A')} km
        """

        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=5,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.8,
            weight=1,
            popup=folium.Popup(popup_text, max_width=300),
            tooltip=f"RRH - BBU Pool {pool_id}"
        ).add_to(rrh_layer)

    rrh_layer.add_to(m)

    # Marcadores BBU Pool
    bbu_layer = folium.FeatureGroup(
        name="BBU Pools",
        show=True
    )

    for i, row in bbus.iterrows():
        pool_id = row.get("bbu_pool", i + 1)

        popup_text = f"""
        <b>BBU Pool:</b> {pool_id}<br>
        <b>Site ID:</b> {row.get('site_id', 'N/A')}<br>
        <b>Assigned RRHs:</b> {row.get('num_assigned_rrhs', 'N/A')}
        """

        folium.Marker(
            location=[row["latitude"], row["longitude"]],
            popup=folium.Popup(popup_text, max_width=300),
            tooltip=f"BBU Pool {pool_id}",
            icon=folium.Icon(
                color="black",
                icon="server",
                prefix="fa"
            )
        ).add_to(bbu_layer)

    bbu_layer.add_to(m)

    # Líneas de conexión RRH - BBU
    connection_layer = folium.FeatureGroup(
        name="RRH - BBU connections",
        show=True
    )

    bbu_coordinates = {
        row.get("bbu_pool", i + 1): [row["latitude"], row["longitude"]]
        for i, row in bbus.iterrows()
    }

    for _, row in rrhs.iterrows():
        pool_id = row["bbu_pool"]

        if pool_id not in bbu_coordinates:
            continue

        folium.PolyLine(
            locations=[
                [row["latitude"], row["longitude"]],
                bbu_coordinates[pool_id]
            ],
            color=pool_colors.get(pool_id, "gray"),
            weight=1.5,
            opacity=0.5
        ).add_to(connection_layer)

    connection_layer.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    m.fit_bounds([
        [rrhs["latitude"].min(), rrhs["longitude"].min()],
        [rrhs["latitude"].max(), rrhs["longitude"].max()]
    ])

    m.save(output_html_path)


# ============================================================
# 6. MAIN EXECUTION
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="BBU Pool Optimizer, CSV Exporter, and Folium Map Generator"
    )
    parser.add_argument(
        "-k", "--k_pools", type=int, required=True, 
        help="Número de BBU pools deseados (K)"
    )
    parser.add_argument(
        "-o", "--operator", type=str, required=True, 
        help="Nombre completo del operador o su alias (ej. 'movistar')"
    )
    parser.add_argument(
        "--input_path", type=str, default="../data/processed/350_CELLS.csv", 
        help="Ruta al CSV de entrada con las celdas"
    )
    parser.add_argument(
        "--data_output_dir", type=str, default="../data/processed/", 
        help="Directorio donde se guardarán los CSVs de salida"
    )
    parser.add_argument(
        "--html_output_dir", type=str, default="../output/", 
        help="Directorio donde se guardará el mapa HTML"
    )

    args = parser.parse_args()

    K = args.k_pools
    operator_input = args.operator

    # Creación automática de directorios si no existen
    os.makedirs(args.data_output_dir, exist_ok=True)
    os.makedirs(args.html_output_dir, exist_ok=True)

    # Resolver operador
    operator_full_name, operator_alias = resolve_operator(operator_input)

    # 1. Cargar RRHs
    rrhs = load_rrhs(args.input_path, operator_full_name)

    print(f"\nSelected operator: {operator_full_name}")
    print(f"Operator alias: {operator_alias}")
    print(f"Number of real RRHs: {len(rrhs)}")

    # 2. Proyectar coordenadas
    X = project_coordinates(rrhs)

    # 3. Ejecutar K-Center
    selected_indices = farthest_first_k_center(X, K)

    # 4. Asignar RRHs a BBU Pools
    assignments, pools = assign_rrhs_to_pools(rrhs, X, selected_indices)

    # Mostrar en consola
    print("\nBBU POOL LOCATIONS:")
    print(pools[
        ["bbu_pool", "site_id", "latitude",
         "longitude", "num_assigned_rrhs"]
    ].to_string(index=False))

    print("\nRRH ASSIGNMENTS:")
    print(assignments[
        ["rrh_id", "site_id", "bbu_pool",
         "distance_to_bbu_km"]
    ].to_string(index=False))

    print("\nPERFORMANCE:")
    print(f"Maximum distance: {assignments['distance_to_bbu_km'].max():.3f} km")
    print(f"Average distance: {assignments['distance_to_bbu_km'].mean():.3f} km")

    # 5. Exportar CSVs
    assignments_file = os.path.join(
        args.data_output_dir, f"rrh_bbu_k{K}_assignments_{operator_alias}.csv"
    )
    pools_file = os.path.join(
        args.data_output_dir, f"bbu_pool_k{K}_locations_{operator_alias}.csv"
    )

    assignments.to_csv(assignments_file, index=False)
    pools.to_csv(pools_file, index=False)

    # 6. Generar mapa HTML
    map_filename = os.path.join(
        args.html_output_dir, f"rrh_bbu_k{K}_assignments_{operator_alias}_map.html"
    )
    generate_map(assignments, pools, map_filename)

    print("\nRESULTS EXPORTED SUCCESSFULLY:")
    print(f" - {assignments_file}")
    print(f" - {pools_file}")
    print(f" - {map_filename}")

    # 7. Resumen de asignación en consola
    print("\nRRH assignment summary:")
    summary = (
        assignments.groupby("bbu_pool")
        .agg(
            assigned_RRHs=("bbu_pool", "count"),
            mean_distance_km=("distance_to_bbu_km", "mean"),
            max_distance_km=("distance_to_bbu_km", "max")
        )
        .round(3)
    )
    print(summary.to_string())


if __name__ == "__main__":
    main()