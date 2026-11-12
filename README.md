# Estaciones de telefonía móvil

Proyecto para descargar las estaciones de telefonía móvil que intersectan la
superficie de `data/input/working_area_900km2.kml`, consultar sus detalles
técnicos y visualizarlas en un mapa interactivo.

## Estructura

```text
data/
  input/
    working_area_900km2.kml       Polígono que delimita la descarga de estaciones
  processed/
    cellular_sites.csv            Estaciones y detalles técnicos combinados
output/
  cellular_sites_map.html         Mapa interactivo generado
scripts/
  download_cells.py               Descarga estaciones y sus detalles técnicos
  create_cell_sites_map.py        Genera el mapa HTML
  bbu_initial_assignment_kmedian.py  Optimiza y asigna RRHs a BBU Pools
```

El script de descarga genera `cellular_sites.csv` y puede sobrescribirlo en
cada ejecución.

## Requisitos

- Python 3.10 o posterior.
- Para la descarga y generación del escenario no hacen falta paquetes externos
  de Python. La optimización de BBU Pools requiere `pandas`, `numpy`, `folium`,
  `matplotlib` y `pyproj`.
- Se necesita Internet para consultar el Geoportal y para cargar las teselas
  del mapa interactivo.

## Dashboard local

Inicia la interfaz desde la raíz del proyecto:

```powershell
python .\scripts\dashboard_server.py
```

Abre `http://127.0.0.1:8765` en el navegador. El dashboard permite descargar
estaciones, generar un escenario, ejecutar la asignación K-Medoids y consultar
los mapas, gráficos y tablas existentes. La galería de mapas permite filtrar
por número de BBU Pools, RRHs totales y RRHs sintéticos; al terminar una
asignación, muestra automáticamente el mapa recién generado. Las acciones usan
los scripts del proyecto y muestran su progreso y errores. El servidor solo
escucha en la máquina local. Los nuevos análisis de retardo y perfiles de
tráfico aparecen como futuras extensiones y todavía no se ejecutan.

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

El generador del escenario selecciona directamente 350 ubicaciones únicas
clasificadas como 4G o 5G desde `cellular_sites.csv`; no necesita un CSV de
muestra intermedio.

## Fuentes

- Geoportal del Ministerio: `https://geoportal.minetur.gob.es/VCTEL/`
- Mapa base: Esri World Street Map.

Respeta las condiciones de uso y atribución de los servicios consultados.


## Crear y visualizar un escenario

Ejecuta estos comandos desde la carpeta raíz del proyecto:

```powershell
py scripts/create_scenario_sites.py --real-count 350 --count 50 --min-distance 200 --seed 42
py scripts/create_cell_sites_map.py
```

`create_scenario_sites.py` elige aleatoriamente 350 ubicaciones reales únicas
que tengan al menos una celda clasificada como 4G o 5G y genera 50 RRHs
sintéticos dentro del polígono KML. Los RRHs mantienen una distancia mínima de
200 m respecto a las ubicaciones reales seleccionadas y entre sí. La semilla
hace reproducible la selección y la generación. `--real-count`, `--count`,
`--min-distance` y `--seed` permiten ajustar esos valores.
Para cada ubicación real, el escenario muestra solo la tecnología de mayor
estándar y un operador que ofrezca esa tecnología; si varios operadores
empatan en el estándar máximo, elige uno aleatoriamente de forma reproducible
con la semilla.

El proceso escribe un único archivo de escenario:
`data/processed/scenario_sites.csv`. Después,
`create_cell_sites_map.py` lo lee para generar
`output/cellular_sites_map.html`.

## Asignación de RRHs a BBU Pools

Instala las dependencias de la optimización:

```powershell
pip install pandas numpy folium matplotlib pyproj
```

Con `scenario_sites.csv` generado, ejecuta la asignación para desplegar tres
BBU Pools:

```powershell
python .\scripts\bbu_initial_assignment_kmedian.py -k 3
```

El algoritmo K-Median (K-Medoids) busca minimizar la suma de las distancias
entre RRHs y sus BBU Pools asignados. Lee por defecto
`data/processed/scenario_sites.csv` y genera los archivos de asignaciones y
ubicaciones de pools en `data/processed/`, además de un mapa interactivo en
`output/`. Usa `--input_path`, `--data_output_dir` y `--html_output_dir` para
cambiar las rutas.
