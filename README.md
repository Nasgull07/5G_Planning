# Estaciones de telefonía móvil y Planificación C-RAN

Proyecto para descargar las estaciones de telefonía móvil que intersectan la
superficie de `data/input/working_area_900km2.kml`, consultar sus detalles
técnicos, generar escenarios de estrés con celdas sintéticas y optimizar la
asignación de BBU Pools (Cloud Radio Access Network) mediante algoritmos de
agrupamiento espacial.

## Estructura

```text
data/
  input/
    working_area_900km2.kml       Polígono que delimita la descarga y generación de celdas
  processed/
    cellular_sites.csv            Estaciones y detalles técnicos combinados
    scenario_sites.csv            Escenario combinado (celdas reales seleccionadas + sintéticas)
    synthetic_rrhs.csv            Solo celdas sintéticas generadas
    rrh_bbu_k*_s*_multi_assignments.csv  Matriz de asignación de celdas a BBU Pools
    bbu_pool_k*_s*_multi_locations.csv   Ubicaciones seleccionadas para los BBU Pools
output/
  cellular_sites_map.html         Mapa interactivo base de celdas
  rrh_bbu_k*_s*_multi_assignments_map.html  Mapa interactivo de topología y asignación C-RAN
scripts/
  download_cells.py               Descarga estaciones y sus detalles técnicos
  add_synthetic_rrhs.py           Genera celdas sintéticas con restricción de separación y KML
  create_cell_sites_map.py        Genera el mapa HTML base
  bbu_initial_assignment_multi.py Asignación óptima de BBU Pools y celdas (K-Center)
```

El script de descarga genera `cellular_sites.csv` y puede sobrescribirlo en
cada ejecución.

## Requisitos

- Python 3.10 o posterior.
- Paquetes externos necesarios para la fase de optimización y mapas: `pandas`, `numpy`, `folium`, `matplotlib`, `pyproj`.
- Se necesita Internet para consultar el Geoportal y para cargar las teselas del mapa interactivo.

Instalación de dependencias externas:

```powershell
pip install pandas numpy folium matplotlib pyproj
```

## Uso

Ejecuta estos comandos desde la carpeta raíz del proyecto:

```powershell
python .\scripts\download_cells.py
```

`download_cells.py` descarga las estaciones dentro del KML, consulta sus
detalles técnicos y guarda el resultado combinado en un único
`cellular_sites.csv`: cada estación descargada ocupa una fila y contiene
también los detalles asociados a su `Gis_Codigo`. `bandas_MHz` contiene los
rangos originales, `numero_banda` relaciona cada rango con las bandas LTE/NR
compatibles, `bandas` contiene un array JSON de números de banda únicos y
`tecnologia` enumera las tecnologías compatibles.
`clasificacion_asumida` es una categoría única por celda: si aparece NR n77 o
n78, se marca `5G`; si no, una banda LTE 4, 7, 20, 28, 38, 40 o 41 se marca
`4G`; las demás se marcan `2G/3G`. Es una clasificación automática basada en
las bandas inferidas, no confirma la tecnología realmente activa. El script
guarda temporalmente los avances para reanudar las consultas si se interrumpen;
al completar, elimina ese archivo temporal. Para controlar el número de
consultas simultáneas:

```powershell
python .\scripts\download_cells.py --workers 2
```

## Fuentes

- Geoportal del Ministerio: `https://geoportal.minetur.gob.es/VCTEL/`
- Mapa base: Esri World Street Map.

Respeta las condiciones de uso y atribución de los servicios consultados.

## Generación de celdas sintéticas y escenario combinado

Para construir el escenario de simulación añadiendo celdas sintéticas con restricciones espaciales estrictas, ejecuta desde la raíz:

```powershell
python .\scripts\add_synthetic_rrhs.py --count 75 --min-distance 200.0 --seed 42
```

`add_synthetic_rrhs.py` lee las estaciones reales y genera el número de celdas sintéticas solicitado (`--count`, por ejemplo 75) ubicándolas estrictamente dentro del polígono delimitado por `working_area_900km2.kml`.

El algoritmo garantiza una separación física mínima (por defecto `--min-distance 200.0` metros) entre cualquier par de antenas (reales o sintéticas) utilizando indexación espacial (*Spatial Hashing*). La semilla (`--seed`) permite que la generación sea 100% reproducible.

Este proceso genera dos archivos en `data/processed/`:
- `synthetic_rrhs.csv`: Contiene únicamente el conjunto de celdas sintéticas generadas.
- `scenario_sites.csv`: Contiene el escenario unificado combinando las celdas reales y sintéticas.

Opcionalmente, se puede visualizar el mapa base con:

```powershell
python .\scripts\create_cell_sites_map.py
```

## Asignación de BBU Pools y optimización C-RAN

Una vez generado el archivo de escenario (`data/processed/scenario_sites.csv`), ejecuta el script de asignación para calcular las ubicaciones óptimas de los BBU Pools y asignar cada RRH a su pool más cercano:

```powershell
python .\scripts\bbu_initial_assignment_multi.py -k 3
```

`bbu_initial_assignment_multi.py` lee `scenario_sites.csv`, detecta automáticamente el número de celdas sintéticas ($S$), proyecta las coordenadas geográficas a espacio métrico en metros (UTM 31N) y aplica la heurística *Farthest-First K-Center* (algoritmo de Gonzalez) para minimizar la distancia máxima de la red de *fronthaul*.

Soporta esquemas crudos y procesados, gestiona operadores multivalor (separados por `;`), parsea identificadores JSON array y prioriza celdas 5G sobre 4G.

### Parámetros principales

- `-k`, `--k_pools`: *(Requerido)* Número de BBU Pools a desplegar ($K$).
- `--input_path`: Ruta al CSV del escenario (por defecto `data/processed/scenario_sites.csv`).
- `--data_output_dir`: Directorio de destino para los CSVs (por defecto `data/processed/`).
- `--html_output_dir`: Directorio de destino para el mapa HTML (por defecto `output/`).

Si el escenario contiene celdas sintéticas ($S > 0$), los archivos exportados incorporan la etiqueta `s{S}` (ej. `k3_s75_multi`). Si $S = 0$, la etiqueta se simplifica a `k3_multi`.

### Archivos generados

- `data/processed/rrh_bbu_k{K}_s{S}_multi_assignments.csv`: Matriz detallada de asignación RRH $\rightarrow$ BBU Pool con distancias en kilómetros.
- `data/processed/bbu_pool_k{K}_s{S}_multi_locations.csv`: Ubicaciones seleccionadas para los BBU Pools y recuento de celdas asignadas.
- `output/rrh_bbu_k{K}_s{S}_multi_assignments_map.html`: Mapa interactivo con capas controlables para celdas reales, sintéticas, BBU Pools y líneas de conexión.