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
`clasificacion_asumida` aplica esta heurística: si aparece NR n78, se marca
`5G`; si no, una banda LTE 4, 7, 20, 28, 38, 40 o 41 se marca `4G`; las demás
se marcan `2G/3G`. Es una clasificación automática basada en las bandas
inferidas, no confirma la tecnología realmente activa. El comando guarda
avances en un CSV parcial para poder reanudar la descarga si se interrumpe.
Para controlar el número de consultas:

```powershell
python .\scripts\download_cell_site_details.py --workers 2
```

El mapa selecciona aleatoriamente 350 ubicaciones únicas entre las que tienen
al menos una celda clasificada como 4G o 5G; las ubicaciones exclusivamente
2G/3G no entran en la muestra. Si hay varios registros en una ubicación
seleccionada, el pop-up conserva todos. La muestra es reproducible. Para
cambiar el tamaño o la semilla:

```powershell
python .\scripts\create_cell_sites_map.py --count 350 --seed 42
```

Abre `output/cellular_sites_map.html` en un navegador. Las estaciones con las
mismas coordenadas se muestran como un único punto; el pop-up incluye los
registros de operadores, los rangos de frecuencia y sus números de banda LTE/NR.

## Fuentes

- Geoportal del Ministerio: `https://geoportal.minetur.gob.es/VCTEL/`
- Mapa base: OpenStreetMap o Carto, seleccionable en el propio mapa.

Respeta las condiciones de uso y atribución de los servicios consultados.
