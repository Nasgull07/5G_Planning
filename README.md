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
    rrh_bbu_k*_s*_kmedian_assignments.csv  Matriz de asignación de celdas a BBU Pools
    bbu_pool_k*_s*_kmedian_locations.csv   Ubicaciones seleccionadas para los BBU Pools
output/
  cellular_sites_map.html         Mapa interactivo base de celdas
  rrh_bbu_k*_s*_kmedian_assignments_map.html  Mapa interactivo de topología y asignación C-RAN
scripts/
  download_cells.py               Descarga estaciones y sus detalles técnicos
  create_scenario_sites.py        Selecciona celdas reales y genera sintéticas dentro del KML
  create_cell_sites_map.py        Genera el mapa HTML base
  bbu_initial_assignment_kmedian.py K-Medoids con distancia máxima obligatoria
```

El script de descarga genera `cellular_sites.csv` y puede sobrescribirlo en
cada ejecución.

## Requisitos

- Python 3.10 o posterior.
- Paquetes externos necesarios para la fase de optimización y mapas: `pandas`, `numpy`, `folium`, `matplotlib`, `pyproj` y `scipy>=1.9`.
- Se necesita Internet para consultar el Geoportal y para cargar las teselas del mapa interactivo.

Instalación de dependencias externas:

```powershell
pip install pandas numpy folium matplotlib pyproj "scipy>=1.9"
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

```bash
python scripts/create_scenario_sites.py --real-count 350 --count 50 --min-distance 200 --seed 42
python scripts/create_cell_sites_map.py
```

`create_scenario_sites.py` lee `cellular_sites.csv`, selecciona el número de
ubicaciones reales 4G/5G indicado por `--real-count` y genera el número de RRH
sintéticos indicado por `--count`, ubicándolos estrictamente dentro del polígono
delimitado por `working_area_900km2.kml`.

El algoritmo garantiza una separación mínima de `--min-distance` metros entre
los sintéticos y las ubicaciones reales seleccionadas, y entre los propios
sintéticos, utilizando indexación espacial (*Spatial Hashing*). La semilla
(`--seed`) hace reproducible la selección y generación.

El primer comando guarda el escenario combinado en
`data/processed/scenario_sites.csv`. El segundo genera el mapa base en
`output/cellular_sites_map.html`.

## Asignación de BBU Pools y optimización C-RAN

Una vez generado `data/processed/scenario_sites.csv`, ejecuta la selección de
K ubicaciones y asignación al pool más cercano, con distancia máxima de 4 km:

```powershell
python scripts/bbu_initial_assignment_kmedian.py -k 30 --max-distance-km 4
```

`bbu_initial_assignment_kmedian.py` proyecta las coordenadas a EPSG:25831 y
conserva las ubicaciones RRH como candidatas a BBU Pool. Tras la inicialización
greedy de K-Medoids, si alguna RRH queda fuera del radio, una selección binaria
de cobertura mediante SciPy/HiGHS busca exactamente K ubicaciones que cubran
todas las RRH, dando preferencia a conservar las ubicaciones iniciales. Después
PAM mejora la suma de distancias aceptando solo intercambios que mantengan la
cobertura total. La suma de distancias se optimiza heurísticamente; no se afirma
que sea el óptimo global.

Si se demuestra que K no permite cubrir el escenario desde las ubicaciones
candidatas, el programa falla sin sobrescribir los resultados anteriores.
Si termina el tiempo de búsqueda sin solución verificada, lo indica sin afirmar
que sea imposible. No añade pools automáticamente ni cambia K.

El radio incluye la frontera: distancia <= 4 km. Con velocidad 200000 km/s,
factor de recorrido 1 y retardo adicional 0, equivale a RTT <= 40 µs.
El analizador de delay conserva su regla estricta RTT < límite: un enlace
exactamente en la frontera de 40 µs se considera inviable en ese análisis.

Soporta esquemas crudos y procesados, gestiona operadores multivalor (separados por `;`), parsea identificadores JSON array y prioriza celdas 5G sobre 4G.

### Parámetros principales

- `-k`, `--k_pools`: *(Requerido)* Número de BBU Pools a desplegar ($K$).
- `--input_path`: Ruta al CSV del escenario (por defecto `data/processed/scenario_sites.csv`).
- `--data_output_dir`: Directorio de destino para los CSVs (por defecto `data/processed/`).
- `--html_output_dir`: Directorio de destino para el mapa HTML (por defecto `output/`).
- `--max-distance-km`: Radio máximo inclusivo (por defecto 4 km).
- `--coverage-time-limit`: Tiempo máximo de reparación de cobertura (por defecto 60 s).

Si el escenario contiene celdas sintéticas ($S > 0$), los archivos exportados incorporan la etiqueta `s{S}` (ej. `k3_s75_kmedian`). Si $S = 0$, la etiqueta se simplifica a `k3_kmedian`.

### Archivos generados

- `data/processed/rrh_bbu_k{K}_s{S}_kmedian_assignments.csv`: Matriz detallada de asignación RRH $\rightarrow$ BBU Pool con distancias en kilómetros.
- `data/processed/bbu_pool_k{K}_s{S}_kmedian_locations.csv`: Ubicaciones seleccionadas para los BBU Pools y recuento de celdas asignadas.
- `output/rrh_bbu_k{K}_s{S}_kmedian_assignments_map.html`: Mapa interactivo con capas controlables para celdas reales, sintéticas, BBU Pools y líneas de conexión.


## Análisis de factibilidad por retardo

El análisis utiliza las ubicaciones fijas de la primera fase. No modifica los
archivos de K-Medoids, no mueve ni desactiva pools y no considera capacidad.
Requiere `pyproj` (también utilizado en la primera fase).

Desde la raíz del proyecto:

```bash
python3 scripts/analyze_delay_feasibility.py -k 15 -s 50
```

El script busca automáticamente la pareja de CSV de K-Medoids en `data/processed/`:
`rrh_bbu_k15_s50_kmedian_assignments.csv` y
`bbu_pool_k15_s50_kmedian_locations.csv`. El CSV de asignaciones conserva
exactamente los RRH e identificadores utilizados. Si falta algún archivo, el
error enumera las rutas que faltan. También comprueba que K y S coincidan con
el contenido. Para S=0 basta `-k 15`: se omite `_s0` en los nombres.

La distancia es euclídea en EPSG:25831, igual que en K-Medoids, y el modelo es:

```text
RTT_us = 2 * distance_km * route_factor / speed_km_s * 1e6 + fixed_rtt_us
```

Parámetros configurables:

- `--max-rtt-us`: 200 por defecto; una conexión es viable si RTT < límite.
- `--speed-km-s`: 200000 por defecto, supuesto de propagación en fibra.
- `--route-factor`: 1 por defecto; multiplicador de recorrido, como mínimo 1.
- `--fixed-rtt-us`: 0 por defecto; retardo adicional total de ida y vuelta.
- `--output-dir`: permite separar experimentos con distintos supuestos.

Estos valores son supuestos del modelo; no representan una medición de la red.
Con los valores por defecto, se requiere una distancia estrictamente menor que
20 km. No se redondean los valores antes de comprobar la viabilidad. Los empates
se resuelven por identificador del pool, de forma reproducible.

Solo se generan dos archivos directamente en `output/delay/`, sin subdirectorios.
K es el número de pools del CSV de entrada y S el número de RRH sintéticos:

- `rrh_delay_k{K}_s{S}_summary.csv`: una fila por RRH con sus datos, asignación original
  y su RTT/viabilidad, pool más cercano incluso si incumple, mejor pool viable,
  número y lista de alternativas (separadas por `;`), estado `served`/`unserved`,
  sin repetir los parámetros de ejecución ni las rutas de entrada.
  Si no hay opción viable, el mejor pool queda vacío.
- `rrh_pool_k{K}_s{S}_links.csv`: una fila por pareja RRH–pool con distancia directa,
  distancia de recorrido, RTT, viabilidad y marcas de asignación original y
  mejor opción. Incluye los enlaces inviables. `feasibility_group` distingue
  `only_option`, `best_with_alternatives`, `alternative` e `infeasible`.

Cuando S=0 se omite `_s{S}`: por ejemplo, `rrh_delay_k15_summary.csv` y
`rrh_pool_k15_links.csv`. Con 50 sintéticos: `rrh_delay_k15_s50_summary.csv`
y `rrh_pool_k15_s50_links.csv`.

Ejecutar de nuevo con los mismos K y S en el mismo directorio reemplaza esos dos
CSV, incluso si cambia la cantidad de RRH reales. Para comparar otros parámetros,
usa otro `--output-dir`. Conserva ambos
CSV juntos para consultar el resumen y el detalle de enlaces de cada ejecución.
Los recuentos por pool se pueden obtener agrupando esta última tabla.

Pruebas del cálculo, límites y entradas:

```bash
python3 -m unittest discover -s tests -p 'test_delay_feasibility.py' -v
```

## Mapa de factibilidad por retardo

Tras ejecutar el análisis, genera el mapa indicando únicamente K y S:

```bash
python3 scripts/create_delay_assignment_map.py -k 15 -s 50
```

El script requiere `folium`. Busca automáticamente los archivos
`output/delay/rrh_delay_k15_s50_summary.csv`,
`output/delay/rrh_pool_k15_s50_links.csv` y
`data/processed/bbu_pool_k15_s50_kmedian_locations.csv`.
Genera únicamente `output/delay/rrh_delay_k15_s50_map.html` y reemplaza ese
mapa si ya existe. Para un escenario sin sintéticos, usa `-k 15` o `-k 15 -s 0`:
se omite `_s0` tanto en las entradas como en la salida.

Si falta algún CSV, el error enumera las rutas que faltan. También se validan K,
S, coordenadas, identificadores, matriz completa de enlaces y concordancia entre
el resumen y la tabla de enlaces antes de generar el HTML.

Los colores identifican pools y conexiones elegidas; los RRH sin servicio llevan
una cruz roja y no se conectan. El borde discontinuo distingue los sintéticos.
Las alternativas viables se pueden activar en el control de capas y están
ocultas inicialmente. Los popups contienen el pool original, mejor pool viable,
distancia/RTT al más cercano y alternativas. El resumen muestra recuentos por
origen y estado. No se recalcula delay ni se modifica ningún CSV.

Abre el HTML en un navegador con conexión a Internet para cargar las bibliotecas
y las teselas del mapa. Los perfiles de tráfico todavía no están incluidos.

## Informe de delay para un K específico

```bash
python3 scripts/generate_delay_report.py -k 15 -s 50
```

Requiere `matplotlib` y lee solo los dos CSV de análisis de `output/delay/`
identificados por K y S. Valida la matriz completa, los recuentos, las alternativas
y la concordancia de las distancias y RTT mínimos antes de producir el informe.
Si S=0, omite `-s` y se omite también `_s0` en los nombres.

Genera un único archivo: `output/delay/rrh_delay_k15_s50_report.html`.
Contiene tres gráficos SVG integrados (sin imágenes externas ni archivos
intermedios) y tablas; se puede consultar sin Internet:

- CDF empírica de distancia al pool más cercano para este K; incluye todos los
  RRH, con peso 1/N, y marca en rojo los que no tienen servicio. Los empates se
  acumulan en un mismo escalón. No compara ni superpone otros valores de K.
- Tres barras por pool: opción única, óptimo con alternativas y alternativa
  viable no óptima. Incluye recuentos y listas desplegables con los RRH.
- Una barra por RRH con la cantidad de pools viables y una tabla completa de
  alternativas. Para escenarios densos se espacian las etiquetas del eje.
- Resumen de cobertura, origen de RRH y distancia/RTT al pool más cercano,
  con medias y máximos calculados sobre todos los RRH, incluso los no servidos.

No recalcula el delay ni modifica los CSV. No presupone el umbral o las constantes
del experimento, que no se guardan en ellos. Una nueva ejecución reemplaza el
informe del mismo K/S. La selección o desactivación de pools no forma parte de
este script.

## Opcional

Para ejecutar las cuatro etapas con un solo comando, desde la raíz del proyecto:

```bash
bash scripts/run_delay_experiment.sh 30 50 40
```

Los argumentos, en orden, son **K** (número de pools), **S** (número de RRH
sintéticos) y **RTT máximo en microsegundos**. El ejemplo utiliza 30 pools,
50 sintéticos y un RTT máximo de 40 µs.

El escenario debe estar generado previamente en `data/processed/scenario_sites.csv`
y contener los S sintéticos indicados. El Bash no regenera el escenario.
Activa primero el entorno Python con las dependencias del proyecto instaladas.
El script utiliza `python` si está disponible o, en su defecto, `python3`.

Se ejecutan en orden:

1. Selección de pools y asignación mediante K-Medoids con radio máximo.
2. Análisis de factibilidad por delay.
3. Mapa de asignaciones.
4. Reporte individual con CDF, tablas y gráficos.

El radio se deriva del RTT usando las mismas constantes en ambas etapas:
velocidad de propagación 200000 km/s, factor de recorrido 1.0 y retardo
adicional de ida y vuelta 0.0 µs. Con estos valores, 40 µs equivalen a 4 km
y 150 µs a 15 km. Las constantes se definen al inicio del Bash.
K-Medoids acepta distancia <= radio; el analizador conserva la regla RTT < límite.

Si alguna etapa falla, la ejecución se detiene. Los resultados se guardan con
la convención K/S existente; repetir K y S reemplaza los archivos correspondientes,
aunque cambie el RTT o el número de RRH reales.
