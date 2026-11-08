# Estaciones de telefonía móvil

Proyecto para descargar las estaciones de telefonía móvil que intersectan la
superficie de `data/input/selected_area.kml`, consultar sus detalles técnicos
y visualizarlas en un mapa interactivo.

## Estructura

```text
data/
  input/
    selected_area.kml             Polígono de cobertura de entrada
  processed/
    cellular_sites.csv            Estaciones obtenidas del Geoportal
    cellular_site_details.csv     Una fila por Gis_Codigo, con bandas en array JSON
output/
  cellular_sites_map.html         Mapa interactivo generado
scripts/
  download_cell_sites.py          Descarga estaciones por teselas (zoom 7)
  download_cell_site_details.py   Consulta los detalles de cada estación
  create_cell_sites_map.py        Genera el mapa HTML
```

Los CSV existentes se incluyen como una instantánea de los datos descargados.
Los scripts pueden volver a generarlos y sobrescribirlos.

## Requisitos

- Python 3.10 o posterior.
- No hacen falta paquetes externos de Python.
- Se necesita Internet para consultar el Geoportal y para cargar las teselas
  del mapa interactivo.

## Uso

Ejecuta estos comandos desde la carpeta raíz del proyecto:

```powershell
python .\scripts\download_cell_sites.py
python .\scripts\download_cell_site_details.py
python .\scripts\create_cell_sites_map.py
```

El segundo comando guarda una fila por `Gis_Codigo`. `bandas_MHz` contiene los
rangos originales, `numero_banda` relaciona cada rango con las bandas LTE/NR
compatibles, `bandas` contiene un array JSON de números de banda únicos y
`tecnologia` enumera las tecnologías compatibles encontradas.
`clasificacion_asumida` es una categoría única por celda: si aparece NR n77 o
n78, se marca `5G`; si no, una banda LTE 4, 7, 20, 28, 38, 40 o 41 se marca
`4G`; las demás se marcan `2G/3G`. Es una clasificación automática basada en
las bandas inferidas, no confirma la tecnología realmente activa. El comando
guarda avances en un CSV parcial para poder reanudar la descarga si se
interrumpe. Para controlar el número de consultas:

```powershell
python .\scripts\download_cell_site_details.py --workers 2
```

El mapa selecciona aleatoriamente 350 ubicaciones únicas entre las que tienen
al menos una celda clasificada como 4G o 5G; las ubicaciones exclusivamente
2G/3G no entran en la muestra. Si hay varios registros en una ubicación
seleccionada, el pop-up conserva todos. En `350_CELLS.csv`, los operadores de
cada ubicación se ordenan por la tecnología asumida más alta: 5G, 4G y 2G/3G.
Cada operador tiene una sola categoría en esa columna; si agrupa varias celdas
en una ubicación, se muestra la categoría más alta. La muestra es reproducible.
Para cambiar el tamaño o la semilla:

```powershell
python .\scripts\create_cell_sites_map.py --count 350 --seed 42
```

Abre `output/cellular_sites_map.html` en un navegador. Las estaciones con las
mismas coordenadas se muestran como un único punto; el pop-up incluye los
registros de operadores, los rangos de frecuencia y sus números de banda LTE/NR.
Al generar el mapa también se crea `data/processed/350_CELLS.csv`; los alias
`VODAFONE ESPAÑA, S.A.` y `VODAFONE ESPAÑA, S.A.U.` se unifican como
`VODAFONE ESPAÑA, S.A.U.` en los datos y el mapa.

## Fuentes

- Geoportal del Ministerio: `https://geoportal.minetur.gob.es/VCTEL/`
- Mapa base: OpenStreetMap o Carto, seleccionable en el propio mapa.

Respeta las condiciones de uso y atribución de los servicios consultados.


## Generar RRH sintéticas para pruebas de robustez

El script `scripts/add_synthetic_rrhs.py` permite añadir RRH sintéticas al
escenario sin modificar los datos originales de Infoantenas.

- Las ubicaciones se generan uniformemente al azar dentro del bounding box
  rectangular definido por las coordenadas del KML.
- Cada RRH sintética recibe un identificador `SYN_RRH_00001`, etc.
- Cada punto debe quedar al menos a 200 metros de cualquier ubicación real
  (coordenadas únicas de `cellular_sites.csv`) y de las demás RRH sintéticas.
- Las filas se identifican con `source_type=SYNTHETIC` e
  `is_synthetic=True`.
- La semilla aleatoria permite reproducir los mismos resultados.

Ejecuta desde la raíz del repositorio:

```powershell
python .\scripts\add_synthetic_rrhs.py --count 50 --min-distance 200 --seed 42
```

Opciones útiles:

```powershell
python .\scripts\add_synthetic_rrhs.py --count 100 --min-distance 200 --seed 42
python .\scripts\add_synthetic_rrhs.py --count 0
```

Se crean dos archivos:

- `data/processed/synthetic_rrhs.csv`: solo las RRH sintéticas.
- `data/processed/scenario_sites.csv`: ubicaciones reales y sintéticas juntas.

El archivo original `cellular_sites.csv` no se modifica. Si no hay espacio
suficiente para colocar todos los puntos respetando la distancia mínima, el
programa termina con un error explicativo en vez de crear ubicaciones que
incumplan la restricción.

**Nota:** la distribución uniforme se realiza dentro del bounding box
rectangular. En un KML irregular, algunos puntos pueden quedar fuera del
polígono. La distancia se calcula mediante la fórmula Haversine sobre
coordenadas geográficas, en metros.
