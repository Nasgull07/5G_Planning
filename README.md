# Estaciones de Telefonía Móvil y Planificación 5G (C-RAN BBU Pool Allocation)

Este proyecto permite descargar las estaciones de telefonía móvil que intersectan la superficie delimitada en `data/input/selected_area.kml` desde el Geoportal del Ministerio, consultar sus detalles técnicos y realizar la **optimización topológica C-RAN (Cloud Radio Access Network)** mediante algoritmos de agrupamiento (*Farthest-First K-Center*) para la asignación óptima de **BBU Pools** y **RRHs** (Remote Radio Heads).

---

## 📁 Estructura del Proyecto

```text
5G_Planning/
├── data/
│   ├── input/
│   │   └── selected_area.kml                # Polígono de cobertura de entrada (KML)
│   └── processed/
│       ├── cellular_sites.csv               # Estaciones obtenidas del Geoportal
│       ├── cellular_site_details.csv        # Detalles técnicos por Gis_Codigo
│       ├── 350_CELLS.csv                    # Muestra limpia de 350 estaciones 4G/5G
│       ├── rrh_bbu_k*_assignments_*.csv     # Matriz de asignación RRH -> BBU Pool
│       └── bbu_pool_k*_locations_*.csv      # Ubicaciones finales de BBU Pools
├── output/
│   ├── cellular_sites_map.html              # Mapa interactivo de celdas descargadas
│   └── rrh_bbu_k*_assignments_*_map.html    # Mapas interactivos de topología C-RAN
└── scripts/
    ├── download_cell_sites.py               # Descarga estaciones por teselas
    ├── download_cell_site_details.py        # Consulta detalles técnicos de cada celda
    ├── create_cell_sites_map.py             # Genera muestra de 350 celdas y mapa base
    ├── bbu_initial_assignment_single.py     # Asignación C-RAN por Operador Individual
    ├── bbu_initial_assignment_shared.py     # Baseline C-RAN Infraestructura Compartida
    └── bbu_initial_assignment_multi.py      # Asignación Compartida con Inyección Sintética y KML
```

---

## 💻 Requisitos y Entorno

- **Python:** 3.10 o superior.
- **Librerías externas:** `pandas`, `numpy`, `folium`, `matplotlib`, `pyproj`, `shapely`, `scipy`.
- **Conexión a Internet:** Requerida para consultar el Geoportal y cargar el mapa base de OpenStreetMap / Esri.

Para instalar las librerías necesarias:

```bash
pip install pandas numpy folium matplotlib pyproj shapely scipy
```

---

## 🔄 Flujo de Trabajo

### Fase 1: Descarga y Procesamiento de Datos de Celdas

Ejecuta los siguientes comandos desde la raíz del proyecto para descargar e inferir las celdas 4G/5G:

```bash
python scripts/download_cell_sites.py
python scripts/download_cell_site_details.py --workers 2
python scripts/create_cell_sites_map.py --count 350 --seed 42
```

**Descripción de la Fase 1:**
1. `download_cell_sites.py`: Descarga las estaciones móviles dentro del área `selected_area.kml`.
2. `download_cell_site_details.py`: Consulta bandas de frecuencia y clasifica la tecnología (`5G`, `4G` o `2G/3G`). Guarda avances en CSV parciales.
3. `create_cell_sites_map.py`: Selecciona una muestra reproducible de 350 emplazamientos 4G/5G (descartando celdas exclusivas 2G/3G), genera `data/processed/350_CELLS.csv` y el mapa visual `output/cellular_sites_map.html`.

---

### Fase 2: Planificación C-RAN y Asignación de BBU Pools

Una vez generado `350_CELLS.csv`, puedes ejecutar los tres modelos de asignación C-RAN según el escenario de red simulado:

#### 1. Modelo Operador Individual (`bbu_initial_assignment_single.py`)
Filtra la red para evaluar la infraestructura de un operador de red móvil específico (ej. Movistar, Vodafone, Orange).

```bash
# Ejecución por alias corto
python scripts/bbu_initial_assignment_single.py -k 3 -o movistar

# Ejecución por nombre oficial completo
python scripts/bbu_initial_assignment_single.py -k 3 -o "VODAFONE ESPAÑA, S.A.U."
```

* **Salidas generadas:**
  - `data/processed/rrh_bbu_k3_assignments_movistar.csv`
  - `data/processed/bbu_pool_k3_locations_movistar.csv`
  - `output/rrh_bbu_k3_assignments_movistar_map.html`

#### 2. Modelo Baseline Compartido (`bbu_initial_assignment_shared.py`)
Simula una red de infraestructura neutra compartida (*Neutral Host*), seleccionando una única RRH por ubicación física priorizando **5G sobre 4G**, sin añadir celdas sintéticas.

```bash
python scripts/bbu_initial_assignment_shared.py -k 3
```

* **Salidas generadas:**
  - `data/processed/rrh_bbu_k3_assignments_shared.csv`
  - `data/processed/bbu_pool_k3_locations_shared.csv`
  - `output/rrh_bbu_k3_assignments_shared_map.html`

#### 3. Modelo Compartido con RRHs Sintéticas y Delimitación KML (`bbu_initial_assignment_multi.py`)
Ejecuta la prueba de estrés e inyección de celdas aleatorias (*Synthetic RRHs*) garantizando que el 100% de los nodos caigan estrictamente dentro del polígono delimitado por el archivo KML.

```bash
# Inyección de 50 RRHs sintéticas ajustadas estrictamente al polígono KML
python scripts/bbu_initial_assignment_multi.py -k 3 -s 50 --kml_path data/input/selected_area.kml

# Con margen de seguridad interno (padding de -1.5 km para evitar bordes)
python scripts/bbu_initial_assignment_multi.py -k 3 -s 50 --kml_path data/input/selected_area.kml --padding_km -1.5
```

* **Salidas generadas:**
  - `data/processed/rrh_bbu_k3_s50_shared_assignments.csv`
  - `data/processed/bbu_pool_k3_s50_shared_locations.csv`
  - `output/rrh_bbu_k3_s50_shared_assignments_map.html`

---

## ⚙️ Parámetros de Línea de Comandos (CLI)

| Script | Parámetro | Descripción | Valor por Defecto |
| :--- | :--- | :--- | :--- |
| `single` | `-k`, `--k_pools` | **(Requerido)** Número de BBU Pools a desplegar ($K$). | — |
| `single` | `-o`, `--operator` | **(Requerido)** Nombre completo del operador o su alias (ej. `movistar`, `vodafone`). | — |
| `shared / multi` | `-k`, `--k_pools` | **(Requerido)** Número de BBU Pools a desplegar ($K$). | — |
| `multi` | `-s`, `--n_synthetic` | Número de RRHs sintéticas a inyectar aleatoriamente. | `0` |
| `multi` | `--kml_path` | Ruta al archivo `.kml` que delimita el polígono del escenario. | `None` |
| `multi` | `--padding_km` | Margen en km alrededor del polígono (negativo para contraer/margen interior). | `0.0` |
| `multi` | `--seed` | Semilla aleatoria para la generación de RRHs sintéticas. | `42` |

---

## 📊 Fuentes y Atribución

- **Geoportal del Ministerio:** [https://geoportal.minetur.gob.es/VCTEL/](https://geoportal.minetur.gob.es/VCTEL/)
- **Mapas base:** OpenStreetMap / Esri World Street Map (seleccionables en la capa del mapa HTML).